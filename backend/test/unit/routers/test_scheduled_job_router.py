from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from server.routers import scheduled_job_router
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.scheduled_jobs.schemas import PersonalScheduledJobRequest
from yuxi.services.scheduled_job_service import IdempotencyKeyReusedError, ScheduledJobDomainError, ScheduledJobService

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class _FakeDb:
    def begin(self):
        return _Transaction()

    async def commit(self):
        pass

    async def rollback(self):
        pass


async def test_create_scheduled_job_requires_idempotency_key():
    app = FastAPI()
    app.include_router(scheduled_job_router.scheduled_jobs, prefix="/api")

    async def override_db():
        yield _FakeDb()

    async def override_user():
        return SimpleNamespace(uid="alice")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_required_user] = override_user
    payload = {
        "name": "monthly review",
        "schedule": {"kind": "at", "run_at": "2030-01-02T09:00:00+08:00"},
        "action": {"type": "notification", "title": "review", "content": "prepare report"},
        "timezone": "Asia/Shanghai",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/scheduled-jobs", json=payload)

    assert response.status_code == 422
    assert "Idempotency-Key" in response.text


async def test_get_scheduled_job_scopes_lookup_to_current_owner(monkeypatch):
    captured = {}

    class FakeService:
        def __init__(self, _db):
            pass

        async def get_owned_job(self, *, job_id, owner_uid):
            captured.update(job_id=job_id, owner_uid=owner_uid)
            return None

    monkeypatch.setattr(scheduled_job_router, "ScheduledJobService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        await scheduled_job_router.get_scheduled_job(
            "job-owned-by-another-user",
            current_user=SimpleNamespace(uid="alice"),
            db=_FakeDb(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "scheduled_job_not_found"
    assert captured == {"job_id": "job-owned-by-another-user", "owner_uid": "alice"}


async def test_create_scheduled_job_maps_reused_idempotency_key_to_conflict(monkeypatch):
    class FakeService:
        def __init__(self, _db):
            pass

        async def create_personal_job(self, **_kwargs):
            raise IdempotencyKeyReusedError("key belongs to another request")

    monkeypatch.setattr(scheduled_job_router, "ScheduledJobService", FakeService)
    payload = PersonalScheduledJobRequest.model_validate(
        {
            "name": "monthly review",
            "schedule": {"kind": "at", "run_at": "2030-01-02T09:00:00+08:00"},
            "action": {"type": "notification", "title": "review", "content": "prepare report"},
            "timezone": "Asia/Shanghai",
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await scheduled_job_router.create_scheduled_job(
            payload,
            idempotency_key="request-1",
            current_user=SimpleNamespace(uid="alice"),
            db=_FakeDb(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "idempotency_key_reused"


async def test_scheduled_job_routes_require_authenticated_user():
    app = FastAPI()
    app.include_router(scheduled_job_router.scheduled_jobs, prefix="/api")

    async def override_db():
        yield _FakeDb()

    async def reject_user():
        raise HTTPException(status_code=401, detail="not_authenticated")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_required_user] = reject_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/scheduled-jobs/job_1")

    assert response.status_code == 401
    assert response.json() == {"detail": "not_authenticated"}


async def test_status_command_delegates_cancel_reason(monkeypatch):
    captured = {}

    class FakeService:
        def __init__(self, _db):
            # 通知任务没有关联 Agent Run，路由不应发布取消信号。
            self.cancelled_agent_run_ids = []

        async def cancel(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                id="sj_1",
                name="monthly review",
                source_type="personal",
                schedule_kind="interval",
                run_at=None,
                anchor_at=None,
                interval_seconds=3600,
                cron_expression=None,
                timezone="Asia/Shanghai",
                next_run_at=None,
                action_type="notification",
                action_data={"title": "review", "content": "prepare report"},
                status="cancelled",
                version=2,
                last_run_at=None,
                paused_at=None,
                cancelled_at=None,
                created_at=None,
                updated_at=None,
            )

    monkeypatch.setattr(scheduled_job_router, "ScheduledJobService", FakeService)

    response = await scheduled_job_router.change_scheduled_job_status(
        "sj_1",
        scheduled_job_router.StatusChangeRequest(action="cancel", version=1, reason="no longer needed"),
        current_user=SimpleNamespace(uid="alice"),
        db=_FakeDb(),
    )

    assert response["job"]["status"] == "cancelled"
    assert captured == {
        "job_id": "sj_1",
        "owner_uid": "alice",
        "version": 1,
        "reason": "no longer needed",
    }


async def test_scheduled_job_cursor_is_opaque_and_rejects_invalid_payload():
    service = ScheduledJobService(SimpleNamespace())
    cursor = service._encode_cursor(
        SimpleNamespace(id="sj_1", updated_at=datetime.fromisoformat("2030-01-02T09:00:00+08:00"))
    )

    assert service._decode_cursor(cursor)[1] == "sj_1"
    with pytest.raises(ScheduledJobDomainError, match="cursor 无效"):
        service._decode_cursor("not-a-cursor")


async def test_schedule_preview_returns_three_localized_occurrences_without_writing_database():
    payload = scheduled_job_router.SchedulePreviewRequest.model_validate(
        {
            "schedule": {
                "kind": "interval",
                "anchor_at": "2030-01-02T09:00:00+08:00",
                "interval_seconds": 3600,
            },
            "timezone": "Asia/Shanghai",
        }
    )

    preview = scheduled_job_router._schedule_preview(
        schedule=payload.schedule,
        timezone=payload.timezone,
        now=datetime(2030, 1, 2, 0, 30, tzinfo=UTC),
    )

    assert preview["next_run_at"] == "2030-01-02T01:00:00+00:00"
    assert [item["local"] for item in preview["occurrences"]] == [
        "2030-01-02T09:00:00+08:00",
        "2030-01-02T10:00:00+08:00",
        "2030-01-02T11:00:00+08:00",
    ]


async def test_schedule_preview_rejects_elapsed_one_off_schedule():
    payload = scheduled_job_router.SchedulePreviewRequest.model_validate(
        {
            "schedule": {"kind": "at", "run_at": "2030-01-02T09:00:00+08:00"},
            "timezone": "Asia/Shanghai",
        }
    )

    with pytest.raises(ScheduledJobDomainError, match="触发时间"):
        scheduled_job_router._schedule_preview(
            schedule=payload.schedule,
            timezone=payload.timezone,
            now=datetime(2030, 1, 2, 1, 0, tzinfo=UTC),
        )


async def test_update_scheduled_job_uses_current_owner_and_version(monkeypatch):
    calls = []

    class FakeService:
        def __init__(self, _db):
            pass

        async def update_personal_job(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id="sj_1", name="updated", source_type="personal", schedule_kind="at", run_at=None,
                anchor_at=None, interval_seconds=None, cron_expression=None, timezone="Asia/Shanghai",
                next_run_at=None, action_type="notification", action_data={}, status="active", version=2,
                last_run_at=None, paused_at=None, cancelled_at=None, created_at=None, updated_at=None,
            )

    monkeypatch.setattr(scheduled_job_router, "ScheduledJobService", FakeService)
    payload = scheduled_job_router.ScheduledJobPatchRequest.model_validate(
        {
            "version": 1,
            "name": "updated",
            "schedule": {"kind": "at", "run_at": "2030-01-02T09:00:00+08:00"},
            "action": {"type": "notification", "title": "title", "content": "content"},
            "timezone": "Asia/Shanghai",
        }
    )
    response = await scheduled_job_router.update_scheduled_job("sj_1", payload, SimpleNamespace(uid="alice"), _FakeDb())

    assert response["job"]["version"] == 2
    assert calls[0]["owner_uid"] == "alice"
    assert calls[0]["version"] == 1
