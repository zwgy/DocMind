from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from yuxi.agents.middlewares.skills import resolve_skill_gated_tools
from yuxi.agents.toolkits.scheduled_tasks import tools


def _tool_callable(tool):
    return tool.coroutine


def _runtime(user_message: str | None = None):
    messages = [SimpleNamespace(type="human", content=user_message)] if user_message else []
    return SimpleNamespace(
        context=SimpleNamespace(uid="user-1", thread_id="thread-1"), config={}, state={"messages": messages}
    )


def test_source_snapshot_marks_chat_iframe_and_keeps_current_thread():
    runtime = _runtime()
    runtime.config = {"configurable": {"iframe_context": {"source_system": "oa"}}}

    snapshot = tools._source_snapshot(runtime)

    assert snapshot.entry_point == "chat_iframe"
    assert snapshot.thread_id == "thread-1"


def test_source_snapshot_uses_visible_parent_thread_for_subagent():
    runtime = _runtime()
    runtime.context.file_thread_id = "visible-parent-thread"

    snapshot = tools._source_snapshot(runtime)

    assert snapshot.thread_id == "visible-parent-thread"


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
    return {
        "name": "晨会提醒",
        "schedule_kind": "at",
        "run_at": "2026-08-08T09:00:00+08:00",
        "action_type": "notification",
        "title": "晨会",
        "content": "请参加晨会",
        "timezone": "Asia/Shanghai",
    }


def _agent_request():
    return {
        "name": "每日待办整理",
        "schedule_kind": "at",
        "run_at": "2026-08-08T09:00:00+08:00",
        "action_type": "agent",
        "agent_slug": "daily-assistant",
        "instruction": "整理今天待办并给出优先级。",
        "timeout_seconds": 300,
        "timezone": "Asia/Shanghai",
    }


def test_scheduled_task_tool_schema_never_exposes_owner_or_recipient_uid():
    for task_tool in (
        tools.create_personal_scheduled_task,
        tools.list_scheduled_task_agents,
        tools.list_personal_scheduled_tasks,
        tools.set_personal_scheduled_task_status,
        tools.cancel_personal_scheduled_task,
    ):
        # ``args_schema`` 保留 ToolRuntime 等执行器注入参数；模型实际看到的是 tool_call_schema。
        schema_text = str(task_tool.tool_call_schema.model_json_schema())
        assert "owner_uid" not in schema_text
        assert "recipient_uid" not in schema_text
        assert "recipient_uids" not in schema_text


def test_personal_task_tool_schema_accepts_agent_action_without_capability_overrides():
    schema = tools.create_personal_scheduled_task.tool_call_schema
    parsed = schema.model_validate(_agent_request())

    assert parsed.action_type == "agent"
    assert parsed.agent_slug == "daily-assistant"

    schema_text = str(schema.model_json_schema())
    assert "skills" not in schema_text
    assert "knowledge" not in schema_text
    assert "mcp" not in schema_text


def test_personal_task_tool_requires_explicit_action_type():
    schema = tools.create_personal_scheduled_task.tool_call_schema
    payload = _request()
    payload.pop("action_type")

    with pytest.raises(ValidationError):
        schema.model_validate(payload)

    assert "action_type" in schema.model_json_schema()["required"]


def test_scheduled_task_tools_are_only_resolved_after_skill_is_readable():
    dependency_map = {
        "scheduled-task": {
            "tools": [
                "create_personal_scheduled_task",
                "list_scheduled_task_agents",
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
async def test_list_scheduled_task_agents_returns_only_repository_visible_top_level_agents(monkeypatch):
    owner = SimpleNamespace(uid="user-1")

    class FakeDb:
        async def scalar(self, _statement):
            return owner

    @asynccontextmanager
    async def fake_db_context():
        yield FakeDb()

    class FakeAgentRepository:
        def __init__(self, db):
            assert isinstance(db, FakeDb)

        async def list_visible(self, *, user):
            assert user is owner
            return [
                SimpleNamespace(
                    slug="daily-assistant",
                    name="日常助手",
                    description="整理日常工作",
                    is_default=True,
                )
            ]

    monkeypatch.setattr(tools.pg_manager, "get_async_session_context", fake_db_context)
    monkeypatch.setattr(tools, "AgentRepository", FakeAgentRepository)

    result = await _tool_callable(tools.list_scheduled_task_agents)(runtime=_runtime())

    assert result == {
        "items": [
            {
                "agent_slug": "daily-assistant",
                "name": "日常助手",
                "description": "整理日常工作",
                "is_default": True,
            }
        ]
    }


def test_scheduled_task_skill_provides_canonical_local_model_create_payload():
    skill_path = Path(__file__).resolve().parents[3] / "package/yuxi/agents/skills/buildin/scheduled-task/SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert '"schedule_kind": "at"' in content
    assert '"run_at": "2026-08-08T09:00:00+08:00"' in content
    assert "不要使用 `once`、`time_at` 或嵌套 `schedule` 对象" in content


def test_periodic_task_without_explicit_clock_time_requires_clarification():
    assert tools._needs_periodic_time_clarification("cron", _runtime("每周提醒我写周报"))
    assert not tools._needs_periodic_time_clarification("cron", _runtime("每周五下午五点提醒我写周报"))
    assert not tools._needs_periodic_time_clarification("at", _runtime("明天提醒我写周报"))


def test_answered_schedule_question_allows_periodic_task_recovery():
    runtime = _runtime("每周提醒我写周报")
    runtime.state["messages"].append(
        SimpleNamespace(
            type="tool",
            name="ask_user_question",
            content=(
                '{"questions":[{"question_id":"q-1","question":"When should it run?"}],'
                '"answer":{"q-1":{"type":"other","text":"Friday at 4 PM","selected":[]}}}'
            ),
        )
    )

    assert not tools._needs_periodic_time_clarification("cron", runtime)


def test_unstructured_or_superseded_answer_does_not_bypass_periodic_time_clarification():
    runtime = _runtime("每周提醒我写周报")
    runtime.state["messages"].extend(
        [
            SimpleNamespace(
                type="tool",
                name="ask_user_question",
                content=(
                    '{"questions":[{"question_id":"q-1","question":"When should it run?"}],'
                    '"answer":{"q-1":"Friday at 4 PM"}}'
                ),
            ),
            SimpleNamespace(
                type="tool",
                name="read_file",
                content='{"content":"unrelated result"}',
            ),
        ]
    )

    assert tools._needs_periodic_time_clarification("cron", runtime)

    runtime.state["messages"][-1] = SimpleNamespace(
        type="tool",
        name="ask_user_question",
        content='{"questions":[{"question_id":"q-1"}],"answer":"Friday at 4 PM"}',
    )
    assert tools._needs_periodic_time_clarification("cron", runtime)


def test_previous_request_schedule_answer_does_not_bypass_new_request_clarification():
    runtime = _runtime("上一个每周任务")
    runtime.state["messages"].extend(
        [
            SimpleNamespace(
                type="tool",
                name="ask_user_question",
                content=(
                    '{"questions":[{"question_id":"q-1","question":"When should it run?"}],'
                    '"answer":{"q-1":"Friday at 4 PM"}}'
                ),
            ),
            SimpleNamespace(type="human", content="每周提醒我写周报"),
        ]
    )

    assert tools._needs_periodic_time_clarification("cron", runtime)


def test_create_tool_clarification_result_allows_periodic_task_recovery():
    runtime = _runtime("每周提醒我写周报")
    runtime.state["messages"].append(
        SimpleNamespace(
            type="tool",
            name="create_personal_scheduled_task",
            content=(
                '{"status":"needs_clarification","answer":{"questions":['
                '{"question_id":"scheduled_task_time"}],'
                '"answer":{"scheduled_task_time":"每周五 17:00"}}}'
            ),
        )
    )

    assert not tools._needs_periodic_time_clarification("cron", runtime)


@pytest.mark.asyncio
async def test_periodic_task_without_clock_time_asks_before_creating(monkeypatch):
    questions = []
    monkeypatch.setattr(
        tools,
        "ask_user_question",
        SimpleNamespace(func=lambda **kwargs: questions.append(kwargs) or {"answer": "周五下午五点"}),
    )

    result = await _tool_callable(tools.create_personal_scheduled_task)(
        **{
            **_request(),
            "schedule_kind": "cron",
            "run_at": None,
            "cron_expression": "0 17 * * 5",
        },
        tool_call_id="call-clarify-1",
        runtime=_runtime("每周提醒我写周报"),
    )

    assert result == {"status": "needs_clarification", "answer": {"answer": "周五下午五点"}}
    assert questions[0]["questions"][0]["question_id"] == "scheduled_task_time"


@pytest.mark.asyncio
async def test_agent_task_creation_reaches_service_as_agent_action(monkeypatch):
    calls = []
    job = _job()
    job.action_data = {
        "type": "agent",
        "agent_slug": "daily-assistant",
        "instruction": "整理今天待办并给出优先级。",
        "timeout_seconds": 300,
    }

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeService:
        def __init__(self, db):
            assert db is not None

        async def create_personal_job(self, **kwargs):
            calls.append(kwargs)
            return job

    monkeypatch.setattr(tools.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(tools, "ScheduledJobService", FakeService)

    result = await _tool_callable(tools.create_personal_scheduled_task)(
        **_agent_request(), tool_call_id="call-agent-1", runtime=_runtime()
    )

    assert result["action"]["type"] == "agent"
    assert calls[0]["request"].action.type == "agent"
    assert calls[0]["request"].action.agent_slug == "daily-assistant"
    assert calls[0]["source_snapshot"].entry_point == "web_agent"
    assert calls[0]["source_snapshot"].thread_id == "thread-1"


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
        **_request(), tool_call_id="call-create-1", runtime=_runtime()
    )
    replay = await _tool_callable(tools.create_personal_scheduled_task)(
        **_request(), tool_call_id="call-create-1", runtime=_runtime()
    )

    assert first["job_id"] == replay["job_id"] == "sj-1"
    assert [call["owner_uid"] for call in calls] == ["user-1", "user-1"]
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
    assert calls[0]["idempotency_key"].startswith("agent-v1-")
