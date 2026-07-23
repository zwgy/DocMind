from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.base import BaseAgent


class _FakeGraph:
    def __init__(self):
        self.last_stream_config = None
        self.last_invoke_config = None

    async def astream(self, payload, *, stream_mode, context, config):
        self.last_stream_config = config
        yield SimpleNamespace(model_dump=lambda: {"type": "ai"}), {"node": "llm"}

    async def ainvoke(self, payload, *, context, config):
        self.last_invoke_config = config
        return {"messages": []}


class _EventGraph(_FakeGraph):
    async def astream_events(self, payload, *, context, config, version, transformers):
        del payload, context, config, version
        self.transformers = transformers

        async def events():
            yield {
                "method": "messages",
                "params": {
                    "namespace": [],
                    "data": ({"content": "internal summary"}, {"metadata": {"lc_source": "summarization"}}),
                },
            }
            yield {
                "method": "messages",
                "params": {"namespace": [], "data": ({"content": "visible answer"}, {"node": "model"})},
            }
            yield {
                "method": "custom",
                "params": {
                    "namespace": [],
                    "data": {"type": "context_compaction", "status": "started"},
                },
            }

        return events()


class _TestAgent(BaseAgent):
    name = "test_agent"
    description = "test"

    async def get_graph(self, **kwargs):
        if getattr(self, "_graph", None) is None:
            self._graph = _FakeGraph()
        return self._graph


_TestAgent.__module__ = "yuxi.agents.tests.fake"


class _EventTestAgent(_TestAgent):
    async def get_graph(self, **kwargs):
        del kwargs
        if getattr(self, "_graph", None) is None:
            self._graph = _EventGraph()
        return self._graph


_EventTestAgent.__module__ = "yuxi.agents.tests.fake"


@pytest.mark.asyncio
async def test_base_agent_stream_messages_passes_callbacks_metadata_and_tags():
    agent = _TestAgent()

    items = []
    async for item in agent.stream_messages(
        ["hello"],
        input_context={"uid": "user-1", "thread_id": "thread-1"},
        callbacks=["handler-1"],
        metadata={"langfuse_user_id": "user-1"},
        tags=["yuxi"],
    ):
        items.append(item)

    graph = await agent.get_graph()
    assert len(items) == 1
    assert graph.last_stream_config == {
        "configurable": {"thread_id": "thread-1", "uid": "user-1"},
        "recursion_limit": 300,
        "callbacks": ["handler-1"],
        "metadata": {"langfuse_user_id": "user-1"},
        "tags": ["yuxi"],
    }


@pytest.mark.asyncio
async def test_base_agent_invoke_messages_passes_callbacks_metadata_and_tags():
    agent = _TestAgent()

    await agent.invoke_messages(
        ["hello"],
        input_context={"uid": "user-1", "thread_id": "thread-1"},
        callbacks=["handler-1"],
        metadata={"langfuse_user_id": "user-1"},
        tags=["yuxi"],
    )

    graph = await agent.get_graph()
    assert graph.last_invoke_config == {
        "configurable": {"thread_id": "thread-1", "uid": "user-1"},
        "recursion_limit": 300,
        "callbacks": ["handler-1"],
        "metadata": {"langfuse_user_id": "user-1"},
        "tags": ["yuxi"],
    }


@pytest.mark.asyncio
async def test_base_agent_uses_configured_max_execution_steps():
    agent = _TestAgent()

    await agent.invoke_messages(
        ["hello"],
        input_context={"uid": "user-1", "thread_id": "thread-1", "max_execution_steps": 42},
    )

    graph = await agent.get_graph()
    assert graph.last_invoke_config["recursion_limit"] == 42


@pytest.mark.asyncio
async def test_base_agent_hides_internal_summary_messages_from_state_stream():
    agent = _EventTestAgent()

    events = []
    async for mode, payload in agent.stream_messages_with_state(
        ["hello"], input_context={"uid": "user-1", "thread_id": "thread-1"}
    ):
        events.append((mode, payload))

    assert events == [
        (
            "messages",
            (
                {"content": "visible answer"},
                {"node": "model", "namespace": [], "stream_event": {"method": "messages", "namespace": []}},
            ),
        ),
        (
            "stream_event",
            {
                "method": "custom",
                "namespace": [],
                "data": {"type": "context_compaction", "status": "started"},
            },
        ),
    ]
    graph = await agent.get_graph()
    assert graph.transformers[0].__name__ == "CustomTransformer"
