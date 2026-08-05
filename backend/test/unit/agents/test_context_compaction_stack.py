"""验证上下文压缩在真实 LangGraph 中间件栈中的组合边界。"""

from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from yuxi.agents.buildin.chatbot import graph as chatbot_graph
from yuxi.agents.buildin.subagent import graph as subagent_graph
from yuxi.agents.middlewares import context_compaction as compaction_module
from yuxi.agents.middlewares.context_compaction import (
    ContextCompactionMiddleware,
    create_context_compaction_middleware,
)
from yuxi.agents.middlewares.output_continuation import OutputContinuationMiddleware
from yuxi.agents.internal_messages import is_internal_output_continuation
from yuxi.agents.middlewares.retry import create_model_retry_middleware
from yuxi.agents.middlewares.token_usage import ModelOutputIncompleteError, TokenUsageMiddleware


class _StackModel(BaseChatModel):
    """区分内部摘要与主调用，并返回可供 TokenUsage 校验的 provider usage。"""

    # 620-token prompt budget 仍能稳定触发小窗口压缩，同时可容纳生产级固定摘要与恢复协议。
    profile: dict = {"max_input_tokens": 1_200, "min_output_reserve_tokens": 500, "context_safety_tokens": 80}
    main_calls: int = 0
    summary_calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "context-compaction-stack-test"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def with_config(self, **kwargs):  # noqa: ARG002
        return self

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: ARG002
        if isinstance(input, str):
            self.summary_calls += 1
            return AIMessage(content="已压缩旧对话")
        self.main_calls += 1
        return AIMessage(
            content="可见回答",
            usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128},
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # pragma: no cover
        raise AssertionError("测试只使用异步模型路径")


class _TruncatedToolModel(BaseChatModel):
    profile: dict = {"max_input_tokens": 2_000, "min_output_reserve_tokens": 500, "context_safety_tokens": 100}
    main_calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "truncated-tool-stack-test"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: ARG002
        self.main_calls += 1
        return AIMessage(
            content="",
            tool_calls=[{"id": "call-1", "name": "side_effect_probe", "args": {"value": "unsafe"}}],
            response_metadata={"finish_reason": "length"},
            usage_metadata={"input_tokens": 100, "output_tokens": 500, "total_tokens": 600},
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # pragma: no cover
        raise AssertionError("测试只使用异步模型路径")


class _MeasuredOverflowModel(_StackModel):
    profile: dict = {"max_input_tokens": 2_000, "min_output_reserve_tokens": 500, "context_safety_tokens": 100}

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: ARG002
        if isinstance(input, str):
            self.summary_calls += 1
            return AIMessage(content="已压缩旧对话")
        self.main_calls += 1
        if self.main_calls == 1:
            return AIMessage(
                content="",
                response_metadata={"finish_reason": "length"},
                usage_metadata={"input_tokens": 1_500, "output_tokens": 500, "total_tokens": 2_000},
            )
        return AIMessage(
            content="恢复后的可见回答",
            usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128},
        )


class _OutputContinuationModel(BaseChatModel):
    profile: dict = {
        "max_input_tokens": 32_768,
        "min_output_reserve_tokens": 4_096,
        "context_safety_tokens": 1_024,
    }
    main_calls: int = 0
    output_limits: list[int | None] = []
    received_messages: list[list] = []

    @property
    def _llm_type(self) -> str:
        return "output-continuation-stack-test"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: ARG002
        self.main_calls += 1
        self.output_limits.append(kwargs.get("max_tokens"))
        self.received_messages.append(list(input))
        if self.main_calls == 1:
            return AIMessage(
                content="第一段回答",
                response_metadata={"finish_reason": "length"},
                usage_metadata={"input_tokens": 1_000, "output_tokens": 4_096, "total_tokens": 5_096},
            )
        return AIMessage(
            content="第二段回答",
            response_metadata={"finish_reason": "stop"},
            usage_metadata={"input_tokens": 1_200, "output_tokens": 500, "total_tokens": 1_700},
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # pragma: no cover
        raise AssertionError("测试只使用异步模型路径")


class _EmptyOutputRetryModel(_OutputContinuationModel):
    @property
    def _llm_type(self) -> str:
        return "empty-output-retry-stack-test"

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: ARG002
        self.main_calls += 1
        self.output_limits.append(kwargs.get("max_tokens"))
        self.received_messages.append(list(input))
        if self.main_calls == 1:
            return AIMessage(
                content="",
                response_metadata={"finish_reason": "length"},
                usage_metadata={"input_tokens": 1_000, "output_tokens": 4_096, "total_tokens": 5_096},
            )
        return AIMessage(
            content="重试后的完整回答",
            response_metadata={"finish_reason": "stop"},
            usage_metadata={"input_tokens": 1_000, "output_tokens": 500, "total_tokens": 1_500},
        )


@pytest.fixture
def archive_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ArchiveBackend:
        async def awrite(self, path, content):  # noqa: ARG002
            return SimpleNamespace(error=None)

    monkeypatch.setattr(compaction_module, "create_agent_composite_backend", lambda _runtime: _ArchiveBackend())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_stack_preserves_token_usage_and_outer_message_overwrite(
    archive_backend: None,  # noqa: ARG001
) -> None:
    model = _StackModel()
    graph = create_agent(
        model=model,
        tools=[],
        middleware=[
            create_context_compaction_middleware(model=model, summary_prompt="summary\n{messages}"),
            TokenUsageMiddleware(),
            create_model_retry_middleware(),
        ],
        checkpointer=InMemorySaver(),
    )

    state = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="旧问题 " + "x" * 2_400),
                AIMessage(content="旧回答"),
                HumanMessage(content="当前问题"),
            ]
        },
        config={"configurable": {"thread_id": "context-compaction-stack"}},
    )

    assert model.summary_calls > 0
    assert model.main_calls == 1
    assert state["context_revision"] == 1
    assert state["context_summary"]
    assert state["token_usage"]["provider_input_tokens"] == 120
    assert state["token_usage"]["summary_active"] is True
    assert [message.content for message in state["messages"]] == ["当前问题", "可见回答"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_stack_applies_measured_overflow_calibration_and_retries_once(
    archive_backend: None,  # noqa: ARG001
) -> None:
    model = _MeasuredOverflowModel()
    graph = create_agent(
        model=model,
        tools=[],
        middleware=[
            create_context_compaction_middleware(model=model, summary_prompt="summary\n{messages}"),
            TokenUsageMiddleware(),
            create_model_retry_middleware(max_retries=2),
        ],
    )

    state = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="旧问题一 " + "x" * 800),
                AIMessage(content="旧回答一"),
                HumanMessage(content="旧问题二 " + "x" * 800),
                AIMessage(content="旧回答二"),
                HumanMessage(content="当前问题"),
            ]
        }
    )

    # 第一次空正文 length 由实测 input 证明为容量溢出；普通 ModelRetry 不接管，
    # 外层压缩器只重建并调用一次，避免形成双层重试乘积。
    assert model.main_calls == 2
    assert model.summary_calls > 0
    assert state["context_revision"] == 1
    assert state["messages"][-1].content == "恢复后的可见回答"
    assert state["token_usage"]["provider_input_tokens"] == 120
    assert state["token_usage"]["calibration_samples"] >= 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_stack_continues_visible_length_once_without_checkpointing_internal_human() -> None:
    model = _OutputContinuationModel()
    graph = create_agent(
        model=model,
        tools=[],
        middleware=[
            OutputContinuationMiddleware(),
            create_context_compaction_middleware(model=model, summary_prompt="summary\n{messages}"),
            TokenUsageMiddleware(),
            create_model_retry_middleware(max_retries=2),
        ],
    )

    state = await graph.ainvoke({"messages": [HumanMessage(content="请生成详细报告", id="user-1")]})

    assert state["token_usage"]["response_outcome"] == "completed", state["token_usage"]
    assert state.get("output_recovery") is None, state
    assert model.main_calls == 2
    assert model.output_limits == [None, 8_192]
    assert is_internal_output_continuation(model.received_messages[1][-1])
    assert [message.content for message in state["messages"]] == ["请生成详细报告", "第一段回答", "第二段回答"]
    assert all(not is_internal_output_continuation(message) for message in state["messages"])
    assert state.get("output_recovery") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_stack_retries_empty_output_exhaustion_once_without_continuation_message() -> None:
    model = _EmptyOutputRetryModel()
    graph = create_agent(
        model=model,
        tools=[],
        middleware=[
            OutputContinuationMiddleware(),
            create_context_compaction_middleware(model=model, summary_prompt="summary\n{messages}"),
            TokenUsageMiddleware(),
            create_model_retry_middleware(max_retries=2),
        ],
    )

    state = await graph.ainvoke({"messages": [HumanMessage(content="请回答", id="user-1")]})

    assert model.main_calls == 2
    assert model.output_limits == [None, 8_192]
    assert all(not is_internal_output_continuation(message) for message in model.received_messages[1])
    assert [message.content for message in state["messages"]] == ["请回答", "重试后的完整回答"]
    assert state["token_usage"]["response_outcome"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_truncated_tool_call_never_reaches_tool_node() -> None:
    executions: list[str] = []

    @tool
    def side_effect_probe(value: str) -> str:
        """记录一次本不应发生的工具副作用。"""
        executions.append(value)
        return "executed"

    model = _TruncatedToolModel()
    graph = create_agent(
        model=model,
        tools=[side_effect_probe],
        middleware=[
            OutputContinuationMiddleware(),
            TokenUsageMiddleware(),
            create_model_retry_middleware(max_retries=2),
        ],
    )

    with pytest.raises(ModelOutputIncompleteError, match="未执行工具") as raised:
        await graph.ainvoke({"messages": [HumanMessage(content="调用探针")]})

    assert raised.value.token_usage["response_outcome"] == "tool_call_truncated"
    assert model.main_calls == 1
    assert executions == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_and_subagent_use_the_same_compaction_stack_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _StackModel()
    filesystem_limits: list[int] = []

    async def no_subagent(_context):
        return None

    for module in (chatbot_graph, subagent_graph):
        monkeypatch.setattr(module, "resolve_chat_model_spec", lambda _model: "test:model")
        monkeypatch.setattr(module, "load_chat_model", lambda **_kwargs: model)
        monkeypatch.setattr(
            module,
            "create_agent_filesystem_middleware",
            lambda limit, **_kwargs: filesystem_limits.append(limit) or object(),
        )
    monkeypatch.setattr(chatbot_graph, "create_subagent_task_middleware", no_subagent)

    context = SimpleNamespace(
        model="test:model",
        summary_prompt="summary\n{messages}",
        tool_token_limit=8,
        model_retry_times=2,
    )
    main_middlewares = await chatbot_graph._build_middlewares(context)
    subagent_middlewares = await subagent_graph._build_middlewares(context)

    # 旧 Agent JSON 中即使残留手工值也不再改变策略；两类 Agent 只使用模型提示预算解析同一阈值。
    assert filesystem_limits == [3 * 1_024, 3 * 1_024]

    for middlewares in (main_middlewares, subagent_middlewares):
        continuation_indexes = [
            index
            for index, middleware in enumerate(middlewares)
            if isinstance(middleware, OutputContinuationMiddleware)
        ]
        compaction_indexes = [
            index for index, middleware in enumerate(middlewares) if isinstance(middleware, ContextCompactionMiddleware)
        ]
        usage_indexes = [
            index for index, middleware in enumerate(middlewares) if isinstance(middleware, TokenUsageMiddleware)
        ]
        retry_indexes = [
            index for index, middleware in enumerate(middlewares) if isinstance(middleware, ModelRetryMiddleware)
        ]
        assert len(continuation_indexes) == len(compaction_indexes) == len(usage_indexes) == len(retry_indexes) == 1
        assert continuation_indexes[0] < compaction_indexes[0] < usage_indexes[0] < retry_indexes[0]
