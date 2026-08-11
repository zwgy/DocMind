from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yuxi.scheduled_jobs.schemas import PersonalScheduledJobRequest
from yuxi.services.scheduled_job_service import (
    JobAlreadyTriggeredError,
    JobRunInProgressError,
    ScheduledJobService,
)
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledJobUserState

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _periodic_job(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="sj_periodic",
        name="旧任务名称",
        schedule_kind="interval",
        run_at=None,
        anchor_at=datetime(2030, 1, 1, tzinfo=UTC),
        interval_seconds=3600,
        cron_expression=None,
        timezone="Asia/Shanghai",
        next_run_at=None,
        action_type="notification",
        action_data={"type": "notification", "title": "旧标题", "content": "旧正文"},
        status=status,
        version=4,
    )


def _periodic_request() -> PersonalScheduledJobRequest:
    return PersonalScheduledJobRequest.model_validate(
        {
            "name": "更新后的周期任务",
            "schedule": {
                "kind": "interval",
                "interval_seconds": 7200,
                "anchor_at": "2030-01-01T09:00:00+08:00",
            },
            "action": {"type": "notification", "title": "新标题", "content": "新正文"},
            "timezone": "Asia/Shanghai",
        }
    )


@pytest.mark.parametrize("status", ["active", "paused"])
async def test_update_periodic_job_with_historical_runs_only_changes_future_definition(status: str):
    db = SimpleNamespace(scalar=AsyncMock(), flush=AsyncMock())
    service = ScheduledJobService(db)
    job = _periodic_job(status)
    service._lock_owned_job = AsyncMock(return_value=job)
    service.repository.database_now = AsyncMock(return_value=datetime(2030, 1, 2, tzinfo=UTC))
    service._resolve_action_data = AsyncMock(
        return_value={"type": "notification", "title": "新标题", "content": "新正文"}
    )
    service._audit = Mock()
    db.scalar.return_value = SimpleNamespace(uid="alice", username="Alice")

    updated = await service.update_personal_job(
        job_id=job.id,
        owner_uid="alice",
        version=4,
        request=_periodic_request(),
    )

    assert updated.name == "更新后的周期任务"
    assert updated.interval_seconds == 7200
    assert updated.action_data["content"] == "新正文"
    assert updated.version == 5
    assert (updated.next_run_at is None) is (status == "paused")
    # 周期任务的既有运行已保存独立快照，编辑路径无需查询或改写运行表。
    db.scalar.assert_awaited_once()


async def test_update_one_off_job_with_existing_run_keeps_trigger_conflict():
    db = SimpleNamespace(scalar=AsyncMock(return_value="sjr_existing"), flush=AsyncMock())
    service = ScheduledJobService(db)
    job = SimpleNamespace(id="sj_once", schedule_kind="at", status="active", version=2)
    service._lock_owned_job = AsyncMock(return_value=job)

    request = PersonalScheduledJobRequest.model_validate(
        {
            "name": "一次性提醒",
            "schedule": {"kind": "at", "run_at": "2030-01-03T09:00:00+08:00"},
            "action": {"type": "notification", "title": "标题", "content": "正文"},
            "timezone": "Asia/Shanghai",
        }
    )

    with pytest.raises(JobAlreadyTriggeredError, match="一次性任务已经生成运行"):
        await service.update_personal_job(
            job_id=job.id,
            owner_uid="alice",
            version=2,
            request=request,
        )

    db.scalar.assert_awaited_once()


async def test_agent_action_without_slug_freezes_visible_default_agent(monkeypatch):
    request = PersonalScheduledJobRequest.model_validate(
        {
            "name": "默认 Agent 任务",
            "schedule": {"kind": "at", "run_at": "2030-01-03T09:00:00+08:00"},
            "action": {"type": "agent", "instruction": "整理结果"},
            "timezone": "Asia/Shanghai",
        }
    )
    default_agent = SimpleNamespace(slug="default-chatbot", is_subagent=False)

    class FakeAgentRepository:
        def __init__(self, _db):
            pass

        async def get_default(self):
            return default_agent

        async def get_visible_by_slug(self, *, slug, user):
            assert slug == "default-chatbot" and user.uid == "alice"
            return default_agent

    monkeypatch.setattr("yuxi.services.scheduled_job_service.AgentRepository", FakeAgentRepository)

    action_data = await ScheduledJobService(SimpleNamespace())._resolve_action_data(
        request=request, owner=SimpleNamespace(uid="alice")
    )

    assert action_data["agent_slug"] == "default-chatbot"


async def test_delete_personal_job_rejects_in_flight_run_without_deleting_data():
    db = SimpleNamespace(scalar=AsyncMock(return_value="sjr_running"), execute=AsyncMock(), flush=AsyncMock())
    service = ScheduledJobService(db)
    service._lock_owned_job = AsyncMock(return_value=SimpleNamespace(id="sj_personal"))

    with pytest.raises(JobRunInProgressError, match="排队或执行"):
        await service.delete_personal_job(job_id="sj_personal", owner_uid="alice", version=1)

    db.execute.assert_not_awaited()


async def test_delete_personal_job_removes_domain_rows_but_not_conversation():
    job = SimpleNamespace(id="sj_personal")
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        execute=AsyncMock(),
        delete=AsyncMock(),
        flush=AsyncMock(),
    )
    service = ScheduledJobService(db)
    service._lock_owned_job = AsyncMock(return_value=job)

    await service.delete_personal_job(job_id=job.id, owner_uid="alice", version=3)

    assert db.execute.await_count == 5
    db.delete.assert_awaited_once_with(job)
    db.flush.assert_awaited_once()


async def test_hide_incoming_job_creates_state_for_current_admin_only():
    job = SimpleNamespace(id="sj_incoming", status="completed")
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[job, None, None]),
        add=Mock(),
        flush=AsyncMock(),
    )
    service = ScheduledJobService(db)
    hidden_at = datetime(2030, 1, 2, tzinfo=UTC)
    service.repository.database_now = AsyncMock(return_value=hidden_at)

    await service.hide_incoming_job(job_id=job.id, user_uid="admin-a")

    state = db.add.call_args.args[0]
    assert isinstance(state, ScheduledJobUserState)
    assert (state.scheduled_job_id, state.user_uid, state.hidden_at) == (job.id, "admin-a", hidden_at)
