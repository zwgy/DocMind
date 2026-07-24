"""验证摘要状态经 LangGraph custom 事件输出，不把私有摘要正文带入流。"""

from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.stream.transformers import CustomTransformer

from yuxi.agents.middlewares import summary as summary_module
from yuxi.agents.middlewares.summary import create_summary_middleware


class _CompactionStreamModel(BaseChatModel):
    """同时模拟主模型流和摘要模型，避免测试依赖真实模型服务。"""

    profile: dict = {"max_input_tokens": 600, "min_output_reserve_tokens": 100, "context_safety_tokens": 80}
    summary_calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "compaction-stream-test"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def with_config(self, **kwargs):  # noqa: ARG002
        return self

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: ARG002
        if isinstance(input, str):
            self.summary_calls += 1
            return AIMessage(content="已压缩旧对话")
        return AIMessage(content="可见回答")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        yield ChatGenerationChunk(message=AIMessageChunk(content="可见回答"))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="可见回答"))])


@pytest.mark.asyncio
async def test_compaction_lifecycle_is_emitted_as_real_langgraph_custom_events(monkeypatch):
    class _ArchiveBackend:
        async def awrite(self, path, content):  # noqa: ARG002
            return SimpleNamespace(error=None)

        def write(self, path, content):  # noqa: ARG002
            return SimpleNamespace(error=None)

    monkeypatch.setattr(summary_module, "create_agent_composite_backend", lambda _runtime: _ArchiveBackend())
    model = _CompactionStreamModel()
    graph = create_agent(
        model=model,
        tools=[],
        middleware=[create_summary_middleware(model=model, summary_prompt="summary\n{messages}")],
        checkpointer=InMemorySaver(),
    )
    events = []
    run = await graph.astream_events(
        {
            "messages": [
                HumanMessage(content="旧问题 " + "x" * 2_400),
                AIMessage(content="旧回答"),
                HumanMessage(content="当前问题"),
            ]
        },
        config={"configurable": {"thread_id": "compaction-stream-test"}},
        version="v3",
        transformers=[CustomTransformer],
    )
    async for event in run:
        if event.get("method") == "custom":
            events.append(event["params"]["data"])

    assert model.summary_calls > 0
    assert events == [
        {"type": "context_compaction", "status": "started"},
        {"type": "context_compaction", "status": "finished"},
    ]
