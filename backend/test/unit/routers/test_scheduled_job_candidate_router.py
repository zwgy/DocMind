from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import scheduled_job_candidate_router
from yuxi.services.incoming_task_candidate_service import CandidateVersionConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class _FakeDb:
    def begin(self):
        return _Transaction()


async def test_candidate_update_delegates_only_mutable_fields(monkeypatch):
    captured = {}

    class FakeService:
        def __init__(self, _db):
            pass

        async def update_candidate(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                id="sjc_1",
                batch_id="sjb_1",
                incoming_id="inc_1",
                extraction_run_id="run_1",
                extraction_item_id="item_1",
                owner_uid="owner",
                name="提醒",
                notification_title="标题",
                notification_content="正文",
                schedule_data={"kind": "cron", "cron_expression": "0 9 * * 1"},
                timezone="Asia/Shanghai",
                recipient_scope="all",
                raw_recipient_names=[],
                recipient_resolution={},
                resolved_recipient_uids=["owner"],
                evidence={},
                validation_errors=[],
                validation_warnings=[],
                status="pending_confirmation",
                version=2,
                enabled_at=None,
                rejected_at=None,
                created_at=None,
                updated_at=None,
            )

    monkeypatch.setattr(scheduled_job_candidate_router, "IncomingTaskCandidateService", FakeService)
    payload = scheduled_job_candidate_router.CandidatePatchRequest.model_validate(
        {
            "version": 1,
            "action": {"type": "notification", "title": "标题", "content": "正文"},
            "recipient_scope": "all",
            "recipient_names": [],
        }
    )

    response = await scheduled_job_candidate_router.update_scheduled_job_candidate(
        "sjc_1",
        payload,
        current_user=SimpleNamespace(uid="admin"),
        db=_FakeDb(),
    )

    assert response["candidate"]["version"] == 2
    assert captured["candidate_id"] == "sjc_1"
    assert captured["actor_uid"] == "admin"
    assert captured["recipient_scope"] == "all"
    assert captured["recipient_names"] == []


async def test_candidate_enable_maps_version_conflict(monkeypatch):
    class FakeService:
        def __init__(self, _db):
            pass

        async def enable_candidate(self, **_kwargs):
            raise CandidateVersionConflictError("候选已被其他操作更新")

    monkeypatch.setattr(scheduled_job_candidate_router, "IncomingTaskCandidateService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        await scheduled_job_candidate_router.enable_scheduled_job_candidate(
            "sjc_1",
            scheduled_job_candidate_router.CandidateEnableRequest(version=1),
            current_user=SimpleNamespace(uid="admin"),
            db=_FakeDb(),
        )

    assert exc_info.value.status_code == 409


async def test_candidate_cursor_rejects_invalid_value():
    with pytest.raises(HTTPException) as exc_info:
        scheduled_job_candidate_router._decode_cursor("not-a-cursor")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid_cursor"
