from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from yuxi.services import scheduled_job_action_handlers as handlers
from yuxi.services import scheduled_job_dispatcher_service as dispatcher_service
from yuxi.services.scheduled_job_service import ScheduledJobService
from yuxi.scheduled_jobs.schemas import PersonalScheduledJobRequest


@pytest.mark.asyncio
async def test_agent_action_does_not_persist_agent_capability_snapshot(monkeypatch):
    request = PersonalScheduledJobRequest.model_validate(
        {
            "name": "每日待办",
            "timezone": "Asia/Shanghai",
            "schedule": {"kind": "at", "run_at": "2026-08-08T09:00:00+08:00"},
            "action": {
                "type": "agent",
                "agent_slug": "daily-assistant",
                "instruction": "整理今天待办。",
                "timeout_seconds": 300,
            },
        }
    )

    class FakeAgentRepository:
        def __init__(self, db):
            assert db is service.db

        async def get_visible_by_slug(self, *, slug, user):
            assert (slug, user.uid) == ("daily-assistant", "user-1")
            return SimpleNamespace(
                is_subagent=False,
                config_json={
                    "context": {
                        "skills": ["scheduled-task", "knowledge-base"],
                        "tools": ["query_kb"],
                        "knowledge_bases": ["kb-1"],
                    }
                },
            )

    service = ScheduledJobService(SimpleNamespace())
    monkeypatch.setattr("yuxi.services.scheduled_job_service.AgentRepository", FakeAgentRepository)

    action_data = await service._resolve_action_data(
        request=request,
        owner=SimpleNamespace(uid="user-1"),
    )

    assert action_data == {
        "type": "agent",
        "agent_slug": "daily-assistant",
        "instruction": "整理今天待办。",
        "timeout_seconds": 300,
    }


@pytest.mark.asyncio
async def test_agent_action_creates_isolated_run_from_current_visible_agent_configuration(monkeypatch):
    created: dict[str, object] = {}
    action = {
        "type": "agent",
        "agent_slug": "daily-assistant",
        "instruction": "整理今天待办。",
        "timeout_seconds": 300,
    }
    run = SimpleNamespace(id="sjr-1", action_snapshot=action)
    job = SimpleNamespace(id="sj-1", owner_uid="user-1", name="每日待办")

    class FakeRepository:
        def __init__(self):
            self.db = SimpleNamespace(scalar=self.scalar)

        async def scalar(self, _statement):
            return SimpleNamespace(uid="user-1")

        async def lock_dispatching_run_with_job(self, *, run_id, instance_id):
            assert (run_id, instance_id) == ("sjr-1", "dispatcher-1")
            return run, job, datetime(2026, 8, 6, tzinfo=UTC)

        async def mark_agent_run_queued(self, **kwargs):
            created["queued"] = kwargs

    class FakeAgentRepository:
        def __init__(self, db):
            assert db is repository.db

        async def get_visible_by_slug(self, *, slug, user):
            assert (slug, user.uid) == ("daily-assistant", "user-1")
            return SimpleNamespace(is_subagent=False)

    class FakeConversationRepository:
        def __init__(self, db):
            assert db is repository.db

        async def create_conversation(self, **kwargs):
            created["conversation"] = kwargs
            return SimpleNamespace(id=42, thread_id="scheduled-thread")

    async def fake_create_agent_run(**kwargs):
        created["agent_run"] = kwargs
        return SimpleNamespace(id="run-1", input_payload={}), True

    repository = FakeRepository()
    monkeypatch.setattr(handlers, "AgentRepository", FakeAgentRepository)
    monkeypatch.setattr(handlers, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(handlers, "create_agent_run", fake_create_agent_run)

    result = await handlers.AgentActionHandler().dispatch(
        repository=repository,
        run_id="sjr-1",
        instance_id="dispatcher-1",
    )

    assert result == handlers.ActionDispatchResult(status="queued", agent_run_id="run-1")
    assert created["conversation"] == {
        "uid": "user-1",
        "agent_id": "daily-assistant",
        "title": "定时任务：每日待办",
        "metadata": {"source": "scheduled_job", "scheduled_job_id": "sj-1", "scheduled_job_run_id": "sjr-1"},
        "commit": False,
    }
    assert created["agent_run"] == {
        "query": "整理今天待办。",
        "agent_id": "daily-assistant",
        "thread_id": "scheduled-thread",
        "meta": {"source": "scheduled_job", "request_id": "scheduled:sjr-1"},
        "image_content": None,
        "current_uid": "user-1",
        "db": repository.db,
        "run_type": "scheduled",
        "commit": False,
    }
    queued = created["queued"]
    assert queued["run"] is run and queued["job"] is job
    assert queued["agent_run_id"] == "run-1"
    assert queued["conversation_id"] == "42"


@pytest.mark.asyncio
async def test_dispatcher_enqueues_agent_run_only_after_transaction_has_closed(monkeypatch):
    events: list[str] = []
    dispatching_run = SimpleNamespace(action_type="agent")

    class FakeTransaction(AbstractAsyncContextManager):
        async def __aenter__(self):
            events.append("transaction_open")

        async def __aexit__(self, *_args):
            events.append("transaction_closed")

    class FakeSession(AbstractAsyncContextManager):
        def begin(self):
            return FakeTransaction()

        async def get(self, model, run_id):
            assert model is dispatcher_service.ScheduledJobRun and run_id == "sjr-1"
            return dispatching_run

        async def __aenter__(self):
            events.append("session_open")
            return self

        async def __aexit__(self, *_args):
            events.append("session_closed")

    class FakeRepository:
        def __init__(self, db):
            assert isinstance(db, FakeSession)

    class FakeHandler:
        async def dispatch(self, *, repository, run_id, instance_id):
            assert isinstance(repository, FakeRepository)
            assert (run_id, instance_id) == ("sjr-1", "dispatcher-1")
            return handlers.ActionDispatchResult(status="queued", agent_run_id="run-1")

    async def fake_enqueue(run_id):
        assert run_id == "run-1"
        assert events == ["session_open", "transaction_open", "transaction_closed", "session_closed"]
        events.append("enqueued")

    monkeypatch.setattr(dispatcher_service, "ScheduledJobRepository", FakeRepository)
    monkeypatch.setattr(dispatcher_service, "get_action_handler", lambda action_type: FakeHandler())
    monkeypatch.setattr(dispatcher_service, "enqueue_agent_run", fake_enqueue)

    result = await dispatcher_service.ScheduledJobDispatcherService(FakeSession).dispatch_action(
        run_id="sjr-1",
        instance_id="dispatcher-1",
    )

    assert result == "queued"
    assert events[-1] == "enqueued"
