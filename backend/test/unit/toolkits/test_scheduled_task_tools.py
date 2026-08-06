from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from yuxi.agents.middlewares.skills import resolve_skill_gated_tools
from yuxi.agents.toolkits.scheduled_tasks import tools


def _tool_callable(tool):
    return tool.coroutine


def _runtime():
    return SimpleNamespace(context=SimpleNamespace(uid="user-1", thread_id="thread-1"), config={}, state={})


def _job():
    return SimpleNamespace(
        id="sj-1",
        name="晨会提醒",
        status="active",
        version=1,
        timezone="Asia/Shanghai",
        schedule_kind="at",
        run_at=datetime(2026, 8, 8, 9, 0),
        anchor_at=None,
        interval_seconds=None,
        cron_expression=None,
        next_run_at=datetime(2026, 8, 8, 9, 0),
        action_data={"type": "notification", "title": "晨会", "content": "请参加晨会"},
    )


def _request():
    return tools.PersonalScheduledJobRequest.model_validate(
        {
            "name": "晨会提醒",
            "schedule": {"kind": "at", "run_at": "2026-08-08T09:00:00+08:00"},
            "action": {"type": "notification", "title": "晨会", "content": "请参加晨会"},
            "timezone": "Asia/Shanghai",
        }
    )


def test_scheduled_task_tool_schema_never_exposes_owner_or_recipient_uid():
    for task_tool in (
        tools.create_personal_scheduled_task,
        tools.list_personal_scheduled_tasks,
        tools.set_personal_scheduled_task_status,
        tools.cancel_personal_scheduled_task,
    ):
        schema_text = str(task_tool.args_schema.model_json_schema())
        assert "owner_uid" not in schema_text
        assert "recipient_uid" not in schema_text
        assert "recipient_uids" not in schema_text


def test_scheduled_task_tools_are_only_resolved_after_skill_is_readable():
    dependency_map = {
        "scheduled-task": {
            "tools": [
                "create_personal_scheduled_task",
                "list_personal_scheduled_tasks",
                "set_personal_scheduled_task_status",
                "cancel_personal_scheduled_task",
            ]
        }
    }
    before = SimpleNamespace(_readable_skills=[], _runtime_skill_dependency_map=dependency_map)
    after = SimpleNamespace(_readable_skills=["scheduled-task"], _runtime_skill_dependency_map=dependency_map)

    assert resolve_skill_gated_tools(before) == []
    assert {tool.name for tool in resolve_skill_gated_tools(after)} == set(dependency_map["scheduled-task"]["tools"])


@pytest.mark.asyncio
async def test_explicit_personal_task_creation_reuses_key_when_tool_call_replays(monkeypatch):
    calls = []

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeService:
        def __init__(self, db):
            assert db is not None

        async def create_personal_job(self, **kwargs):
            calls.append(kwargs)
            return _job()

    monkeypatch.setattr(tools.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(tools, "ScheduledJobService", FakeService)

    first = await _tool_callable(tools.create_personal_scheduled_task)(
        request=_request(), tool_call_id="call-create-1", runtime=_runtime()
    )
    replay = await _tool_callable(tools.create_personal_scheduled_task)(
        request=_request(), tool_call_id="call-create-1", runtime=_runtime()
    )

    assert first["job_id"] == replay["job_id"] == "sj-1"
    assert [call["owner_uid"] for call in calls] == ["user-1", "user-1"]
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
    assert calls[0]["idempotency_key"].startswith("agent-v1-")
