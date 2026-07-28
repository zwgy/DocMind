from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import yuxi.services.agent_run_service as agent_run_service


def _sse_data(chunk: str) -> dict:
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError(f"SSE chunk has no data line: {chunk}")


def test_compact_stream_chunk_keeps_context_compaction_state():
    compact = agent_run_service._compact_stream_chunk(
        {
            "status": "stream_event",
            "event": {
                "method": "custom",
                "namespace": [],
                "data": {"type": "context_compaction", "status": "started"},
            },
        }
    )

    assert compact["event"] == {
        "method": "custom",
        "data": {"type": "context_compaction", "status": "started"},
    }


class _FakeContext:
    def __init__(self):
        self.model = "agent-default-model"

    def update_from_dict(self, data: dict):
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)


class _FakeBackend:
    context_schema = _FakeContext


@pytest.mark.asyncio
async def test_stream_agent_run_events_emits_error_on_db_error(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class BrokenRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            raise RuntimeError("db down")

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", BrokenRepo)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    assert '"reason": "db_error"' in chunks[0]


@pytest.mark.asyncio
async def test_stream_agent_run_events_reads_redis_and_ends_on_end_event(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class Repo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return SimpleNamespace(status="completed", thread_id="thread-1")

    calls = {"count": 0}

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        calls["count"] += 1
        if calls["count"] == 1:
            return [
                {
                    "seq": "1700000000000-0",
                    "event_type": "messages",
                    "payload": {
                        "schema_version": 1,
                        "run_id": "run-1",
                        "thread_id": "thread-1",
                        "event": "messages",
                        "payload": {"items": [{"status": "loading", "response": "你"}]},
                        "created_at": "2026-05-27T00:00:00+00:00",
                    },
                    "ts": 1700000000000,
                },
                {
                    "seq": "1700000000001-0",
                    "event_type": "end",
                    "payload": {
                        "schema_version": 1,
                        "run_id": "run-1",
                        "thread_id": "thread-1",
                        "event": "end",
                        "payload": {"status": "completed"},
                        "created_at": "2026-05-27T00:00:01+00:00",
                    },
                    "ts": 1700000000001,
                },
            ]
        return []

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", Repo)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service, "SSE_POLL_INTERVAL_SECONDS", 0)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
    ):
        chunks.append(chunk)

    assert chunks[0].startswith("event: messages")
    assert "id: 1700000000000-0" in chunks[0]
    assert chunks[-1].startswith("event: end")
    assert "id: 1700000000001-0" in chunks[-1]


@pytest.mark.asyncio
async def test_stream_agent_run_events_compacts_verbose_false(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class Repo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return SimpleNamespace(status="completed", thread_id="thread-1")

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        return [
            {
                "seq": "1700000000000-0",
                "event_type": "metadata",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "metadata",
                    "payload": {
                        "request_id": "req-1",
                        "agent_id": "deep-research",
                        "backend_id": "ChatbotAgent",
                        "uid": "user-1",
                    },
                    "created_at": "2026-05-27T00:00:00+00:00",
                },
                "ts": 1700000000000,
            },
            {
                "seq": "1700000000001-0",
                "event_type": "custom",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "custom",
                    "payload": {
                        "name": "yuxi.init",
                        "chunk": {
                            "request_id": "req-1",
                            "response": None,
                            "thread_id": "thread-1",
                            "status": "init",
                            "meta": {"query": "写一个冒泡排序", "uid": "user-1"},
                            "msg": {
                                "role": "user",
                                "content": "写一个冒泡排序",
                                "type": "human",
                                "image_content": "base64-image-data",
                                "extra_metadata": {
                                    "request_id": "req-1",
                                    "attachments": [],
                                    "debug": "drop-me",
                                },
                            },
                        },
                    },
                    "created_at": "2026-05-27T00:00:00+00:00",
                },
                "ts": 1700000000001,
            },
            {
                "seq": "1700000000002-0",
                "event_type": "custom",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "custom",
                    "payload": {
                        "name": "yuxi.agent_state",
                        "chunk": {
                            "request_id": "req-1",
                            "response": None,
                            "thread_id": "thread-1",
                            "status": "agent_state",
                            "agent_state": {
                                "todos": [],
                                "files": {},
                                "artifacts": [],
                                "subagent_runs": [],
                            },
                            "meta": {"uid": "user-1"},
                        },
                        "agent_state": {
                            "todos": [],
                            "files": {},
                            "artifacts": [],
                            "subagent_runs": [],
                        },
                    },
                    "created_at": "2026-05-27T00:00:00+00:00",
                },
                "ts": 1700000000002,
            },
            {
                "seq": "1700000000003-0",
                "event_type": "messages",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "messages",
                    "payload": {
                        "items": [
                            {
                                "request_id": "req-1",
                                "response": "你",
                                "thread_id": "thread-1",
                                "status": "loading",
                                "stream_event": {
                                    "type": "tool_call",
                                    "message_id": "msg-1",
                                    "tool_call_id": "call-1",
                                    "name": "ls",
                                    "args": {"path": "/home/gem/user-data/outputs"},
                                    "thread_id": "thread-1",
                                    "namespace": [],
                                },
                                "metadata": {
                                    "langfuse_user_id": "user-1",
                                    "langgraph_checkpoint_ns": "model:checkpoint",
                                },
                            }
                        ]
                    },
                    "created_at": "2026-05-27T00:00:01+00:00",
                },
                "ts": 1700000000003,
            },
            {
                "seq": "1700000000004-0",
                "event_type": "end",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "end",
                    "payload": {
                        "status": "completed",
                        "chunk": {"status": "finished", "request_id": "req-1", "meta": {"uid": "user-1"}},
                    },
                    "created_at": "2026-05-27T00:00:02+00:00",
                },
                "ts": 1700000000004,
            },
        ]

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", Repo)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
        verbose=False,
    ):
        chunks.append(chunk)

    assert len(chunks) == 3

    init_data = _sse_data(chunks[0])
    init_chunk = init_data["payload"]["chunk"]
    assert init_data["request_id"] == "req-1"
    assert init_data["payload"]["name"] == "yuxi.init"
    assert "meta" not in init_chunk
    assert "request_id" not in init_chunk
    assert "response" not in init_chunk
    assert "thread_id" not in init_chunk
    assert "image_content" not in init_chunk["msg"]
    assert "extra_metadata" not in init_chunk["msg"]

    message_data = _sse_data(chunks[1])
    message_chunk = message_data["payload"]["items"][0]
    assert message_data["request_id"] == "req-1"
    assert "request_id" not in message_chunk
    assert "metadata" not in message_chunk
    assert "response" not in message_chunk
    assert "thread_id" not in message_chunk
    assert message_chunk["stream_event"]["tool_call_id"] == "call-1"
    assert "thread_id" not in message_chunk["stream_event"]
    assert "namespace" not in message_chunk["stream_event"]

    end_data = _sse_data(chunks[2])
    assert end_data["request_id"] == "req-1"
    assert end_data["payload"]["status"] == "completed"
    assert "request_id" not in end_data["payload"]["chunk"]
    assert "meta" not in end_data["payload"]["chunk"]


@pytest.mark.asyncio
async def test_stream_agent_run_events_compact_fallback_end_keeps_request_id(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class Repo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return SimpleNamespace(status="completed", thread_id="thread-1", request_id="req-1")

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        return []

    async def fake_get_last_run_stream_seq(run_id: str):
        del run_id
        return "1700000000004-0"

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", Repo)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service, "get_last_run_stream_seq", fake_get_last_run_stream_seq)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
        verbose=False,
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].startswith("event: end")
    assert "id: 1700000000004-0" in chunks[0]
    data = _sse_data(chunks[0])
    assert data["request_id"] == "req-1"
    assert data["payload"] == {"status": "completed"}


@pytest.mark.asyncio
async def test_create_agent_run_persists_input_before_enqueue(monkeypatch: pytest.MonkeyPatch):
    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(uid="user-1", role="user")

    class FakeDB:
        def __init__(self):
            self.order: list[str] = []
            self.committed = False
            self.added = []

        async def execute(self, stmt):
            del stmt
            return FakeResult()

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            self.order.append("flush")
            for item in self.added:
                if getattr(item, "id", None) is None:
                    item.id = 10

        async def commit(self):
            self.order.append("commit")
            self.committed = True

        async def rollback(self):
            raise AssertionError("rollback should not be called")

    db = FakeDB()
    captured = {}
    created_run = SimpleNamespace(
        id="",
        thread_id="thread-1",
        status="pending",
        request_id="req-1",
        uid="user-1",
    )

    class RunRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def get_active_run_by_checkpoint_thread(self, checkpoint_thread_id: str):
            assert checkpoint_thread_id == "thread-1"
            return None

        async def create_run(self, **kwargs):
            assert kwargs["request_id"] == "req-1"
            assert kwargs["conversation_id"] == 1
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            assert run_id == created_run.id
            assert message_id == 10
            return created_run

    class ConvRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_conversation_by_thread_id_for_update(self, thread_id: str):
            del thread_id
            return SimpleNamespace(id=1, uid="user-1", status="active", agent_id="default")

    class AgentRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_visible_by_slug(self, slug: str, user):
            del user
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    class RequestRepo:
        def __init__(self, db_session):
            del db_session

        async def has_queued_request(self, **_kwargs):
            return False

    class Queue:
        async def enqueue_job(self, job_name: str, run_id: str, _job_id: str):
            assert job_name == "process_agent_run"
            assert run_id == created_run.id
            assert _job_id == f"run:{created_run.id}"
            db.order.append("enqueue")
            assert db.committed is True

    async def fake_get_arq_pool():
        return Queue()

    monkeypatch.setattr(agent_run_service.agent_manager, "get_agent", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(agent_run_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRequestRepository", RequestRepo)
    monkeypatch.setattr(agent_run_service, "get_arq_pool", fake_get_arq_pool)

    result = await agent_run_service.create_agent_run_view(
        query="hello",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1", "iframe_context": {"page": {"title": "Detail"}, "files": []}},
        image_content=None,
        current_uid="user-1",
        db=db,
    )

    assert db.order[-2:] == ["commit", "enqueue"]
    assert result["run_id"] == created_run.id
    assert result["request_id"] == "req-1"
    assert db.added[0].run_id == created_run.id
    assert db.added[0].request_id == "req-1"
    assert captured["input_payload"]["model_spec"] == "agent-default-model"
    assert captured["input_payload"]["iframe_context"] == {"page": {"title": "Detail"}, "files": []}
    assert db.added[0].extra_metadata["model_spec"] == "agent-default-model"
    assert db.added[0].extra_metadata["iframe_context"] == {"enabled": True}


@pytest.mark.asyncio
async def test_create_agent_run_view_enqueues_busy_thread_without_creating_message(monkeypatch: pytest.MonkeyPatch):
    created_requests: list[dict] = []

    async def fake_create_agent_run(**_kwargs):
        raise agent_run_service.HTTPException(
            status_code=409,
            detail={"message": "该会话已有运行中的任务", "run_id": "run-active"},
        )

    async def fake_enqueue_agent_request(**kwargs):
        created_requests.append(kwargs)
        return (
            SimpleNamespace(
                request_id="request-queued",
                thread_id="thread-1",
                agent_id="default",
                status="queued",
                dispatched_run_id=None,
                input_payload={"query": "later question"},
            ),
            True,
        )

    monkeypatch.setattr(agent_run_service, "create_agent_run", fake_create_agent_run)
    monkeypatch.setattr(agent_run_service, "enqueue_agent_request", fake_enqueue_agent_request)

    result = await agent_run_service.create_agent_run_view(
        query="later question",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "request-queued"},
        image_content=None,
        current_uid="user-1",
        db=object(),
        queue_policy="enqueue",
    )

    assert result == {
        "request_id": "request-queued",
        "thread_id": "thread-1",
        "agent_id": "default",
        "status": "queued",
        "queued": True,
        "run_id": None,
        "content": "later question",
    }
    assert len(created_requests) == 1
    assert created_requests[0] | {"db": None} == {
        "query": "later question",
        "agent_id": "default",
        "thread_id": "thread-1",
        "meta": {"request_id": "request-queued"},
        "image_content": None,
        "current_uid": "user-1",
        "db": None,
        "model_spec": None,
        "resume": None,
        "parent_run_id": None,
        "resume_request_id": None,
    }


@pytest.mark.asyncio
async def test_create_agent_run_view_keeps_busy_thread_rejection_by_default(monkeypatch: pytest.MonkeyPatch):
    async def fake_create_agent_run(**_kwargs):
        raise agent_run_service.HTTPException(
            status_code=409,
            detail={"message": "该会话已有运行中的任务", "run_id": "run-active"},
        )

    monkeypatch.setattr(agent_run_service, "create_agent_run", fake_create_agent_run)

    with pytest.raises(agent_run_service.HTTPException) as exc_info:
        await agent_run_service.create_agent_run_view(
            query="later question",
            agent_id="default",
            thread_id="thread-1",
            meta={"request_id": "request-queued"},
            image_content=None,
            current_uid="user-1",
            db=object(),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_dispatch_next_agent_request_creates_run_then_marks_request_dispatched(
    monkeypatch: pytest.MonkeyPatch,
):
    queued_request = SimpleNamespace(
        request_id="request-queued",
        thread_id="thread-1",
        agent_id="default",
        uid="user-1",
        input_payload={
            "query": "later question",
            "meta": {"request_id": "request-queued"},
            "image_content": None,
            "model_spec": None,
            "resume": None,
            "parent_run_id": None,
            "resume_request_id": None,
        },
    )
    marks: list[tuple[object, str]] = []
    enqueued_runs: list[str] = []

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class RequestRepo:
        def __init__(self, db):
            del db

        async def get_next_queued_for_thread_for_update(self, **kwargs):
            assert kwargs == {"thread_id": "thread-1", "uid": "user-1"}
            return queued_request

        async def mark_dispatched(self, request, *, run_id: str):
            marks.append((request, run_id))

    class RunRepo:
        def __init__(self, db):
            del db

        async def get_active_run_by_checkpoint_thread(self, thread_id: str):
            assert thread_id == "thread-1"
            return None

    async def fake_create_agent_run(**kwargs):
        assert kwargs["allow_queued_request_id"] == "request-queued"
        assert kwargs["commit"] is False
        assert kwargs["query"] == "later question"
        return SimpleNamespace(id="run-2"), True

    async def fake_enqueue_agent_run(run_id: str):
        enqueued_runs.append(run_id)

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRequestRepository", RequestRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "create_agent_run", fake_create_agent_run)
    monkeypatch.setattr(agent_run_service, "enqueue_agent_run", fake_enqueue_agent_run)

    run_id = await agent_run_service.dispatch_next_agent_request(thread_id="thread-1", current_uid="user-1")

    assert run_id == "run-2"
    assert marks == [(queued_request, "run-2")]
    assert enqueued_runs == ["run-2"]


@pytest.mark.asyncio
async def test_recover_pending_agent_requests_requeues_runs_and_ready_queue_heads(monkeypatch: pytest.MonkeyPatch):
    recovered_run_ids: list[str] = []
    dispatched_thread_keys: list[tuple[str, str]] = []

    class PendingResult:
        def scalars(self):
            return SimpleNamespace(all=lambda: ["run-1", "run-2"])

    class FakeDB:
        async def execute(self, _statement):
            return PendingResult()

    @asynccontextmanager
    async def fake_session_ctx():
        yield FakeDB()

    class RequestRepo:
        def __init__(self, db):
            del db

        async def list_queued_thread_keys(self):
            return [("thread-1", "user-1"), ("thread-2", "user-2")]

    async def fake_enqueue_agent_run(run_id: str):
        recovered_run_ids.append(run_id)

    async def fake_dispatch_next_agent_request(*, thread_id: str, current_uid: str):
        dispatched_thread_keys.append((thread_id, current_uid))
        return "run-3" if thread_id == "thread-1" else None

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRequestRepository", RequestRepo)
    monkeypatch.setattr(agent_run_service, "enqueue_agent_run", fake_enqueue_agent_run)
    monkeypatch.setattr(agent_run_service, "dispatch_next_agent_request", fake_dispatch_next_agent_request)

    assert await agent_run_service.recover_pending_agent_requests() == (2, 1)
    assert recovered_run_ids == ["run-1", "run-2"]
    assert dispatched_thread_keys == [("thread-1", "user-1"), ("thread-2", "user-2")]


@pytest.mark.asyncio
async def test_get_agent_request_view_returns_only_current_users_request(monkeypatch: pytest.MonkeyPatch):
    request = SimpleNamespace(
        request_id="request-1",
        thread_id="thread-1",
        agent_id="default",
        status="queued",
        dispatched_run_id=None,
        input_payload={"query": "later question"},
    )

    class RequestRepo:
        def __init__(self, db):
            del db

        async def get_for_user(self, **kwargs):
            assert kwargs == {"request_id": "request-1", "uid": "user-1"}
            return request

    monkeypatch.setattr(agent_run_service, "AgentRunRequestRepository", RequestRepo)

    assert await agent_run_service.get_agent_request_view(
        request_id="request-1",
        current_uid="user-1",
        db=object(),
    ) == {
        "request": {
            "request_id": "request-1",
            "thread_id": "thread-1",
            "agent_id": "default",
            "status": "queued",
            "queued": True,
            "run_id": None,
            "content": "later question",
        }
    }


@pytest.mark.asyncio
async def test_cancel_agent_request_view_cancels_only_queued_request(monkeypatch: pytest.MonkeyPatch):
    request = SimpleNamespace(
        request_id="request-1",
        thread_id="thread-1",
        agent_id="default",
        status="queued",
        dispatched_run_id=None,
        input_payload={"query": "later question"},
    )
    cancelled: list[object] = []

    class FakeDB:
        committed = False

        async def commit(self):
            self.committed = True

    class RequestRepo:
        def __init__(self, db):
            del db

        async def get_for_user_for_update(self, **kwargs):
            assert kwargs == {"request_id": "request-1", "uid": "user-1"}
            return request

        async def cancel_queued(self, item):
            cancelled.append(item)
            item.status = "cancelled"

    db = FakeDB()
    monkeypatch.setattr(agent_run_service, "AgentRunRequestRepository", RequestRepo)

    assert await agent_run_service.cancel_agent_request_view(
        request_id="request-1",
        current_uid="user-1",
        db=db,
    ) == {
        "request_id": "request-1",
        "thread_id": "thread-1",
        "agent_id": "default",
        "status": "cancelled",
        "queued": False,
        "run_id": None,
        "content": "later question",
    }
    assert cancelled == [request]
    assert db.committed is True


@pytest.mark.asyncio
async def test_list_queued_agent_requests_view_returns_fifo_positions(monkeypatch: pytest.MonkeyPatch):
    requests = [
        SimpleNamespace(
            request_id="request-1",
            thread_id="thread-1",
            agent_id="default",
            status="queued",
            dispatched_run_id=None,
            input_payload={"query": "first question"},
        ),
        SimpleNamespace(
            request_id="request-2",
            thread_id="thread-1",
            agent_id="default",
            status="queued",
            dispatched_run_id=None,
            input_payload={"query": "second question"},
        ),
    ]

    class ConvRepo:
        def __init__(self, db):
            del db

        async def get_conversation_by_thread_id(self, thread_id: str):
            assert thread_id == "thread-1"
            return SimpleNamespace(uid="user-1", status="active", agent_id="default")

    class RequestRepo:
        def __init__(self, db):
            del db

        async def list_queued_for_thread(self, **kwargs):
            assert kwargs == {"thread_id": "thread-1", "agent_id": "default", "uid": "user-1"}
            return requests

    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRequestRepository", RequestRepo)

    assert await agent_run_service.list_queued_agent_requests_view(
        thread_id="thread-1",
        agent_id="default",
        current_uid="user-1",
        db=object(),
    ) == {
        "requests": [
            {
                "request_id": "request-1",
                "thread_id": "thread-1",
                "agent_id": "default",
                "status": "queued",
                "queued": True,
                "run_id": None,
                "content": "first question",
                "queue_position": 1,
            },
            {
                "request_id": "request-2",
                "thread_id": "thread-1",
                "agent_id": "default",
                "status": "queued",
                "queued": True,
                "run_id": None,
                "content": "second question",
                "queue_position": 2,
            },
        ]
    }


@pytest.mark.asyncio
async def test_create_resume_run_marks_input_message_source(monkeypatch: pytest.MonkeyPatch):
    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(uid="user-1", role="user")

    class FakeDB:
        def __init__(self):
            self.order: list[str] = []
            self.committed = False
            self.added = []

        async def execute(self, stmt):
            del stmt
            return FakeResult()

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            self.order.append("flush")
            for item in self.added:
                if getattr(item, "id", None) is None:
                    item.id = 11

        async def commit(self):
            self.order.append("commit")
            self.committed = True

        async def rollback(self):
            raise AssertionError("rollback should not be called")

    db = FakeDB()
    created_run = SimpleNamespace(
        id="",
        thread_id="thread-1",
        status="pending",
        request_id="resume-req",
        uid="user-1",
    )

    class RunRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_run_for_user(self, run_id: str, uid: str):
            assert run_id == "parent-run"
            assert uid == "user-1"
            return SimpleNamespace(id=run_id, thread_id="thread-1", status="interrupted", input_payload={})

        async def get_resume_run(self, parent_run_id: str, resume_request_id: str):
            assert parent_run_id == "parent-run"
            assert resume_request_id == "resume-req"
            return None

        async def get_run_by_request_id(self, request_id: str):
            assert request_id == "resume-req"
            return None

        async def get_active_run_by_checkpoint_thread(self, checkpoint_thread_id: str):
            assert checkpoint_thread_id == "thread-1"
            return None

        async def create_run(self, **kwargs):
            assert kwargs["run_type"] == "resume"
            assert kwargs["parent_run_id"] == "parent-run"
            assert kwargs["resume_request_id"] == "resume-req"
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            assert run_id == created_run.id
            assert message_id == 11
            return created_run

    class ConvRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_conversation_by_thread_id_for_update(self, thread_id: str):
            del thread_id
            return SimpleNamespace(id=1, uid="user-1", status="active", agent_id="default")

    class AgentRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_visible_by_slug(self, slug: str, user):
            del user
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    class Queue:
        async def enqueue_job(self, job_name: str, run_id: str, _job_id: str):
            assert job_name == "process_agent_run"
            assert run_id == created_run.id
            assert _job_id == f"run:{created_run.id}"
            assert db.committed is True

    async def fake_get_arq_pool():
        return Queue()

    class RequestRepo:
        def __init__(self, db_session):
            del db_session

        async def has_queued_request(self, **_kwargs):
            return False

    monkeypatch.setattr(agent_run_service.agent_manager, "get_agent", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(agent_run_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "get_arq_pool", fake_get_arq_pool)

    result = await agent_run_service.create_agent_run_view(
        query=None,
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        image_content=None,
        current_uid="user-1",
        db=db,
        resume={"language": "python"},
        parent_run_id="parent-run",
        resume_request_id="resume-req",
    )

    assert result["run_id"] == created_run.id
    assert db.added[0].message_type == "resume"
    assert db.added[0].extra_metadata["source"] == "ask_user_question_resume"


@pytest.mark.asyncio
async def test_create_agent_run_core_can_skip_input_message_for_child_run(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    created_run = SimpleNamespace(
        id="",
        thread_id="child-thread",
        status="pending",
        request_id="subagent-req",
        uid="user-1",
    )

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            assert request_id == "subagent-req"
            return None

        async def create_run(self, **kwargs):
            captured["create_run"] = kwargs
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            raise AssertionError("input message should not be persisted")

    db = _patch_common_run_repos(monkeypatch, RunRepo)

    run, created = await agent_run_service.create_agent_run(
        query="delegate this",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "subagent-req"},
        image_content=None,
        current_uid="user-1",
        db=db,
        run_type="subagent",
        parent_agent_run_id="parent-agent-run",
        checkpoint_thread_id="child-thread",
        persist_input_message=False,
    )

    assert created is True
    assert run is created_run
    assert db.added == []
    assert captured["create_run"]["run_type"] == "subagent"
    assert captured["create_run"]["parent_agent_run_id"] == "parent-agent-run"
    assert captured["create_run"]["checkpoint_thread_id"] == "child-thread"
    assert captured["create_run"]["input_payload"]["parent_agent_run_id"] == "parent-agent-run"
    assert db.committed is True


# ==================== run 结果基础能力 ====================


@pytest.mark.asyncio
async def test_get_agent_run_result_uses_output_message_id(monkeypatch: pytest.MonkeyPatch):
    run = SimpleNamespace(
        id="run-1",
        status="completed",
        agent_id="default-chatbot",
        thread_id="thread-1",
        conversation_id=10,
        request_id="req-1",
        output_message_id=2,
        error_type=None,
        error_message=None,
    )
    messages = [
        SimpleNamespace(id=1, role="user", content="question", extra_metadata={}),
        SimpleNamespace(id=2, role="assistant", content="older", extra_metadata={"langfuse_trace_id": "trace-old"}),
        SimpleNamespace(id=3, role="assistant", content="final", extra_metadata={"langfuse_trace_id": "trace-final"}),
    ]

    class FakeScalars:
        def unique(self):
            return self

        def all(self):
            return messages

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeDB:
        async def execute(self, _stmt):
            return FakeResult()

    class RunRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            assert run_id == "run-1"
            assert uid == "user-1"
            return run

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)

    payload = await agent_run_service.get_agent_run_result(run_id="run-1", current_uid="user-1", db=FakeDB())

    assert payload["status"] == "completed"
    assert payload["output"] == "older"
    assert payload["final_message_id"] == 2
    assert payload["langfuse_trace_id"] == "trace-old"
    assert "debug" not in payload


@pytest.mark.asyncio
async def test_get_agent_run_result_missing_run_returns_failed(monkeypatch: pytest.MonkeyPatch):
    class RunRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return None

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)

    payload = await agent_run_service.get_agent_run_result(run_id="run-x", current_uid="user-1", db=object())

    assert payload["status"] == "failed"
    assert payload["error"]["type"] == "run_not_found"


@pytest.mark.asyncio
async def test_await_agent_run_result_drains_stream_then_loads_result(monkeypatch: pytest.MonkeyPatch):
    drained: list[str] = []

    async def fake_stream(*, run_id: str, after_seq: str, current_uid: str, verbose: bool):
        assert run_id == "run-1"
        assert after_seq == "0-0"
        assert current_uid == "user-1"
        assert verbose is False
        for chunk in ("event: messages\n\n", "event: end\n\n"):
            drained.append(chunk)
            yield chunk

    async def fake_load(*, run_id: str, current_uid: str):
        assert run_id == "run-1"
        assert current_uid == "user-1"
        return {"status": "completed", "output": "final"}

    monkeypatch.setattr(agent_run_service, "stream_agent_run_events", fake_stream)
    monkeypatch.setattr(agent_run_service, "load_agent_run_result", fake_load)

    payload = await agent_run_service.await_agent_run_result(run_id="run-1", current_uid="user-1")

    assert len(drained) == 2
    assert payload == {"status": "completed", "output": "final"}


@pytest.mark.asyncio
async def test_request_cancel_agent_run_can_cascade_children(monkeypatch: pytest.MonkeyPatch):
    parent_run = SimpleNamespace(id="parent-run", uid="user-1")
    child_runs = [SimpleNamespace(id="child-1"), SimpleNamespace(id="child-2")]
    requested: list[str] = []
    signals: list[str] = []

    class RunRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            assert run_id == "parent-run"
            assert uid == "user-1"
            return parent_run

        async def list_active_child_runs_for_user(self, parent_agent_run_id: str, uid: str):
            assert parent_agent_run_id == "parent-run"
            assert uid == "user-1"
            return child_runs

        async def request_cancel(self, run_id: str):
            requested.append(run_id)
            return parent_run if run_id == "parent-run" else SimpleNamespace(id=run_id)

    async def fake_publish_cancel_signal(run_id: str):
        signals.append(run_id)

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "publish_cancel_signal", fake_publish_cancel_signal)

    run = await agent_run_service.request_cancel_agent_run(
        run_id="parent-run",
        current_uid="user-1",
        db=object(),
        cascade_children=True,
    )

    assert run is parent_run
    assert requested == ["child-1", "child-2", "parent-run"]
    assert signals == ["child-1", "child-2", "parent-run"]


def test_validate_model_spec_returns_none_when_empty():
    assert agent_run_service._validate_model_spec(None) is None
    assert agent_run_service._validate_model_spec("") is None


def test_validate_model_spec_rejects_unknown_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", lambda spec: None)
    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service._validate_model_spec("nope")
    assert exc.value.status_code == 422


def test_validate_model_spec_rejects_non_chat_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="embedding"),
    )
    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service._validate_model_spec("embed-1")
    assert exc.value.status_code == 422


def test_validate_model_spec_accepts_chat_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat"),
    )
    assert agent_run_service._validate_model_spec("gpt-x") == "gpt-x"


def test_validate_model_spec_strips_explicit_model(monkeypatch: pytest.MonkeyPatch):
    seen = []

    def fake_get_model_info(spec):
        seen.append(spec)
        return SimpleNamespace(model_type="chat")

    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", fake_get_model_info)

    assert agent_run_service._validate_model_spec(" gpt-x ") == "gpt-x"
    assert seen == ["gpt-x"]


def _patch_common_run_repos(
    monkeypatch: pytest.MonkeyPatch,
    run_repo_cls,
    *,
    agent_config_json: dict | None = None,
):
    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(uid="user-1", role="user")

    class FakeDB:
        def __init__(self):
            self.added = []
            self.committed = False

        async def execute(self, stmt):
            del stmt
            return FakeResult()

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            for item in self.added:
                if getattr(item, "id", None) is None:
                    item.id = 10

        async def commit(self):
            self.committed = True

        async def rollback(self):
            raise AssertionError("rollback should not be called")

    class ConvRepo:
        def __init__(self, db_session):
            del db_session

        async def get_conversation_by_thread_id_for_update(self, thread_id: str):
            del thread_id
            return SimpleNamespace(id=1, uid="user-1", status="active", agent_id="default")

    class AgentRepo:
        def __init__(self, db_session):
            del db_session

        async def get_visible_by_slug(self, slug: str, user):
            del user
            return SimpleNamespace(
                slug=slug,
                backend_id="ChatbotAgent",
                config_json=agent_config_json or {"context": {}},
            )

    class Queue:
        async def enqueue_job(self, job_name: str, run_id: str, _job_id: str):
            del job_name, run_id, _job_id

    async def fake_get_arq_pool():
        return Queue()

    class RequestRepo:
        def __init__(self, db_session):
            del db_session

        async def has_queued_request(self, **_kwargs):
            return False

    monkeypatch.setattr(agent_run_service.agent_manager, "get_agent", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(agent_run_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    if hasattr(run_repo_cls, "get_active_run_by_checkpoint_thread"):
        run_repo_factory = run_repo_cls
    else:

        class RunRepoWithActiveCheckpointCheck(run_repo_cls):
            async def get_active_run_by_checkpoint_thread(self, checkpoint_thread_id: str):
                del checkpoint_thread_id
                return None

        run_repo_factory = RunRepoWithActiveCheckpointCheck

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", run_repo_factory)
    monkeypatch.setattr(agent_run_service, "AgentRunRequestRepository", RequestRepo)
    monkeypatch.setattr(agent_run_service, "get_arq_pool", fake_get_arq_pool)
    return FakeDB()


@pytest.mark.asyncio
async def test_create_agent_run_rejects_second_active_checkpoint_thread(monkeypatch: pytest.MonkeyPatch):
    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            assert request_id == "new-request"
            return None

        async def get_active_run_by_checkpoint_thread(self, checkpoint_thread_id: str):
            assert checkpoint_thread_id == "thread-1"
            return SimpleNamespace(id="running-run")

        async def create_run(self, **_kwargs):
            raise AssertionError("active checkpoint must not create another run")

    db = _patch_common_run_repos(monkeypatch, RunRepo)

    with pytest.raises(agent_run_service.HTTPException) as exc_info:
        await agent_run_service.create_agent_run_view(
            query="hello",
            agent_id="default",
            thread_id="thread-1",
            meta={"request_id": "new-request"},
            image_content=None,
            current_uid="user-1",
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["run_id"] == "running-run"


@pytest.mark.asyncio
async def test_create_chat_run_persists_validated_model_spec(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat"),
    )
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="req-1", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(monkeypatch, RunRepo)

    await agent_run_service.create_agent_run_view(
        query="hello",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_uid="user-1",
        db=db,
        model_spec="claude-x",
    )

    assert captured["input_payload"]["model_spec"] == "claude-x"


@pytest.mark.asyncio
async def test_create_chat_run_with_image_persists_multimodal_message_type(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="req-1", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(monkeypatch, RunRepo)

    await agent_run_service.create_agent_run_view(
        query="看图",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content="base64-image",
        current_uid="user-1",
        db=db,
    )

    assert captured["input_payload"]["image_content"] == "base64-image"
    assert db.added[0].message_type == "multimodal_image"
    assert db.added[0].image_content == "base64-image"


@pytest.mark.asyncio
async def test_create_chat_run_snapshots_agent_configured_model_spec(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="req-1", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(
        monkeypatch,
        RunRepo,
        agent_config_json={"context": {"model": "agent-config-model"}},
    )

    await agent_run_service.create_agent_run_view(
        query="hello",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_uid="user-1",
        db=db,
        model_spec=None,
    )

    assert captured["input_payload"]["model_spec"] == "agent-config-model"
    assert db.added[0].extra_metadata["model_spec"] == "agent-config-model"


@pytest.mark.asyncio
async def test_create_chat_run_snapshots_system_default_when_agent_model_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service,
        "resolve_chat_model_spec",
        lambda model_spec: str(model_spec).strip() if str(model_spec or "").strip() else "system-default-model",
    )
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="req-1", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(
        monkeypatch,
        RunRepo,
        agent_config_json={"context": {"model": ""}},
    )

    await agent_run_service.create_agent_run_view(
        query="hello",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_uid="user-1",
        db=db,
        model_spec=None,
    )

    assert captured["input_payload"]["model_spec"] == "system-default-model"
    assert db.added[0].extra_metadata["model_spec"] == "system-default-model"


@pytest.mark.asyncio
async def test_create_resume_run_inherits_parent_model_spec(monkeypatch: pytest.MonkeyPatch):
    # 即使 resume 入参传了别的模型，也必须沿用父运行的模型
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="resume-req", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_for_user(self, run_id: str, uid: str):
            del uid
            return SimpleNamespace(
                id=run_id,
                thread_id="thread-1",
                status="interrupted",
                input_payload={"model_spec": "parent-model"},
            )

        async def get_resume_run(self, parent_run_id: str, resume_request_id: str):
            del parent_run_id, resume_request_id
            return None

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(monkeypatch, RunRepo)

    await agent_run_service.create_agent_run_view(
        query=None,
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        image_content=None,
        current_uid="user-1",
        db=db,
        model_spec="ignored-model",
        resume={"language": "python"},
        parent_run_id="parent-run",
        resume_request_id="resume-req",
    )

    assert captured["input_payload"]["model_spec"] == "parent-model"
