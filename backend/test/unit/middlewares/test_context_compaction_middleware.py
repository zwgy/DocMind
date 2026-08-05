from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command, Overwrite

from yuxi.agents.context import DEFAULT_YUXI_SUMMARY_PROMPT
from yuxi.agents.middlewares import context_compaction as context_compaction_module
from yuxi.agents.middlewares.context_compaction import (
    ContextCompactionMiddleware,
    SummaryInvariantLossError,
    SummaryOutputTooLargeError,
    SummaryOutputTruncatedError,
    create_context_compaction_middleware,
)
from yuxi.agents.middlewares.context_projection import ToolProtocolError
from yuxi.agents.internal_messages import INTERNAL_OUTPUT_CONTINUATION_KEY
from yuxi.agents.middlewares.token_usage import (
    ContextBudgetConfigurationError,
    ContextWindowExceededError,
    estimate_model_request,
    resolve_context_budget,
)

# P2 只迁移模块和生产 API；保留测试局部别名，避免数百条成熟断言因无业务价值的名称替换产生噪声。
summary_module = context_compaction_module
create_summary_middleware = create_context_compaction_middleware


class _SummaryModel:
    profile = {"max_input_tokens": 600, "min_output_reserve_tokens": 100, "context_safety_tokens": 80}

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.bound_configs: list[dict] = []

    def with_config(self, **config):
        self.bound_configs.append(config)
        return self

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        return SimpleNamespace(text="已归档旧对话：用户要完成项目文档。")

    async def ainvoke(self, prompt: str):
        return self.invoke(prompt)


class _ArchiveBackend:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def write(self, path: str, content: str):
        self.writes.append((path, content))
        return SimpleNamespace(error=None)

    async def awrite(self, path: str, content: str):
        return self.write(path, content)


@pytest.fixture(autouse=True)
def archive_backend(monkeypatch: pytest.MonkeyPatch) -> _ArchiveBackend:
    backend = _ArchiveBackend()
    monkeypatch.setattr(summary_module, "create_agent_composite_backend", lambda _runtime: backend)
    return backend


def _request(messages):
    model = _SummaryModel()
    stream_events = []
    return model, ModelRequest(
        model=model,
        messages=messages,
        system_message=SystemMessage(content="system instructions"),
        tools=[{"name": "query_kb", "description": "x"}],
        runtime=SimpleNamespace(
            context={},
            config={},
            stream_events=stream_events,
            stream_writer=stream_events.append,
        ),
        state={"messages": messages, "context_revision": 3},
    )


def _single_human_tool_chain(rounds: int) -> list:
    """构造载荷很小但协议骨架很多的单用户工具链，复现旧分段边界无法释放空间的问题。"""
    messages = [HumanMessage(content="请连续检查项目状态", id="current-user")]
    for index in range(rounds):
        call_id = f"call-{index}"
        messages.extend(
            [
                AIMessage(
                    content=f"执行第 {index} 步检查",
                    tool_calls=[{"id": call_id, "name": "query_kb", "args": {}}],
                    id=f"assistant-{index}",
                ),
                ToolMessage(content="ok", tool_call_id=call_id, name="query_kb", id=f"tool-{index}"),
            ]
        )
    return messages


@pytest.mark.unit
def test_internal_continuation_is_not_a_user_boundary_or_summary_fact() -> None:
    internal = HumanMessage(
        content="内部续写指令",
        additional_kwargs={INTERNAL_OUTPUT_CONTINUATION_KEY: True},
    )
    messages = [
        HumanMessage(content="真实用户请求", id="real-user"),
        AIMessage(content="已输出的第一段"),
        internal,
    ]

    assert ContextCompactionMiddleware._current_human_input_index(messages) == 0
    assert summary_module._message_segments(messages) == [messages]
    safe = summary_module._messages_safe_for_summary(messages)
    assert [message.content for message in safe] == ["真实用户请求", "已输出的第一段"]


@pytest.mark.unit
def test_compaction_commit_drops_request_only_internal_continuation() -> None:
    model = _SummaryModel()
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    real_user = HumanMessage(content="真实请求", id="real-user")
    internal = HumanMessage(
        content="内部续写指令",
        id="internal-user",
        additional_kwargs={INTERNAL_OUTPUT_CONTINUATION_KEY: True},
    )
    response = ModelResponse(result=[AIMessage(content="续写完成", id="assistant-final")])
    plan = {
        "survivors": [real_user, internal],
        "summary": "",
        "compacted_through": "",
        "archive_path": "",
        "previous_revision": 0,
        "summary_updated": False,
        "summary_quality": None,
    }

    result = middleware._commit_plan(response, plan)

    committed = result.command.update["messages"].value
    assert [message.id for message in committed] == ["real-user", "assistant-final"]


@pytest.mark.unit
def test_factory_uses_project_owned_budget_middleware() -> None:
    model = _SummaryModel()
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    assert isinstance(middleware, ContextCompactionMiddleware)
    assert not hasattr(middleware, "_lc_helper")
    assert model.bound_configs == [{"metadata": {"lc_source": "summarization"}}]


@pytest.mark.unit
def test_summary_request_binds_its_actual_output_budget() -> None:
    class BoundSummaryModel(_SummaryModel):
        def __init__(self) -> None:
            super().__init__()
            self.output_limits: list[int] = []

        def bind(self, **kwargs):
            self.output_limits.append(kwargs["max_tokens"])
            return self

    model = BoundSummaryModel()
    _, request = _request([])
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    summary = middleware._create_summary(
        "",
        [HumanMessage(content="old request")],
        37,
        resolve_context_budget(request),
    )

    assert summary
    assert model.output_limits == [37]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("context_window", "expected_output_limit"),
    [
        (32_768, 4_096),
        (65_536, 8_192),
        (131_072, 16_384),
        (262_144, 20_000),
    ],
)
def test_summary_call_limits_scale_across_supported_context_windows(
    context_window: int,
    expected_output_limit: int,
) -> None:
    model, request = _request([])
    model.profile = {
        "max_input_tokens": context_window,
        "min_output_reserve_tokens": 4_096,
        "context_safety_tokens": 1_024,
    }
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    output_limit, input_budget = middleware._summary_call_limits(
        resolve_context_budget(request.override(model=model)),
        context_window,
    )

    # 同一公式覆盖 32K～256K：升级部署只改变 profile，不引入模型名或窗口档位分支。
    assert output_limit == expected_output_limit
    assert input_budget + output_limit + 1_024 == context_window


@pytest.mark.unit
def test_summary_output_validation_prefers_provider_usage() -> None:
    class ProviderMeasuredSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return SimpleNamespace(
                text="x" * 100,
                usage_metadata={"input_tokens": 200, "output_tokens": 20, "total_tokens": 220},
            )

    model = ProviderMeasuredSummaryModel()
    _, request = _request([])
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    summary = middleware._create_summary(
        "",
        [HumanMessage(content="old request")],
        37,
        resolve_context_budget(request),
    )

    assert summary == "x" * 100


@pytest.mark.unit
def test_summary_output_validation_ignores_zero_provider_usage() -> None:
    class MissingMeasuredSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return SimpleNamespace(
                text="x" * 1_000,
                usage_metadata={"input_tokens": 200, "output_tokens": 0, "total_tokens": 200},
            )

    model = MissingMeasuredSummaryModel()
    _, request = _request([])
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    with pytest.raises(SummaryOutputTooLargeError, match="未遵守输出上限"):
        middleware._create_summary(
            "",
            [HumanMessage(content="old request")],
            37,
            resolve_context_budget(request),
        )


@pytest.mark.unit
def test_summary_quality_only_checks_labels_without_repair_call() -> None:
    complete = "\n".join(
        [
            "intent: continue implementation",
            "concepts: context compaction",
            "files/code: context_compaction.py",
            "errors/fixes: none",
            "progress: P5",
            "user messages: preserve constraints",
            "pending tasks: remote acceptance",
            "current work: tests",
            "next step: run Docker tests",
        ]
    )

    model = _SummaryModel()
    default_middleware = create_summary_middleware(model=model, summary_prompt=DEFAULT_YUXI_SUMMARY_PROMPT)
    custom_middleware = create_summary_middleware(model=model, summary_prompt="custom\n{messages}")

    assert default_middleware._summary_quality(complete) == "semantic"
    assert default_middleware._summary_quality("intent: continue") == "format_unverified"
    assert custom_middleware._summary_quality("任意自定义结构") == "custom"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_summary_repairs_missing_exact_anchors_once(asynchronous: bool) -> None:
    class RepairingSummaryModel(_SummaryModel):
        def __init__(self) -> None:
            super().__init__()
            self.responses = [
                "## files/code\nNEXT-2026-0805：继续验收",
                (
                    "## files/code\nCONTRACT-2026-0805：禁止实现 L4\n"
                    "/outputs/context-check.md\nNEXT-2026-0805：继续验收"
                ),
            ]

        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return SimpleNamespace(text=self.responses.pop(0))

    previous = "## files/code\nCONTRACT-2026-0805：禁止实现 L4\n/outputs/context-check.md"
    model = RepairingSummaryModel()
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    arguments = (previous, [HumanMessage(content="记录 NEXT-2026-0805")], 512, 3_000)

    if asynchronous:
        summary = await middleware._acreate_summary_once(*arguments)
    else:
        summary = middleware._create_summary_once(*arguments)

    assert len(model.prompts) == 2
    assert "<required_exact_values>" in model.prompts[1]
    for anchor in ("CONTRACT-2026-0805", "L4", "/outputs/context-check.md", "NEXT-2026-0805"):
        assert anchor in summary


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_summary_rejects_checkpoint_when_one_repair_still_loses_anchor(asynchronous: bool) -> None:
    class LosingSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return SimpleNamespace(text="## files/code\nNEXT-2026-0805：继续验收")

    model = LosingSummaryModel()
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    arguments = (
        "## files/code\nCONTRACT-2026-0805：禁止实现 L4\n/outputs/context-check.md",
        [HumanMessage(content="记录 NEXT-2026-0805")],
        512,
        3_000,
    )

    with pytest.raises(SummaryInvariantLossError, match="修复后仍遗漏"):
        if asynchronous:
            await middleware._acreate_summary_once(*arguments)
        else:
            middleware._create_summary_once(*arguments)

    assert len(model.prompts) == 2


@pytest.mark.unit
def test_summary_does_not_repair_when_exact_anchors_are_preserved() -> None:
    class PreservingSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return SimpleNamespace(
                text=(
                    "## files/code\nCONTRACT-2026-0805：禁止实现 L4\n"
                    "/outputs/context-check.md\nNEXT-2026-0805：继续验收"
                )
            )

    model = PreservingSummaryModel()
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    summary = middleware._create_summary_once(
        "## files/code\nCONTRACT-2026-0805：禁止实现 L4\n/outputs/context-check.md",
        [HumanMessage(content="记录 NEXT-2026-0805")],
        512,
        3_000,
    )

    assert "NEXT-2026-0805" in summary
    assert len(model.prompts) == 1


@pytest.mark.unit
def test_summary_exact_anchors_ignore_archive_metadata_and_xml_tags() -> None:
    summary = (
        "<private_context_archive>\n"
        "最新清单：/home/gem/user-data/outputs/conversation_history/archive-r3-a-b.jsonl\n"
        "更早清单位于 /outputs/conversation_history/。\n"
        "</private_context_archive>\n"
        "保留 CONTRACT-2026-0805 和 /outputs/final-report.md"
    )

    assert context_compaction_module._summary_exact_anchors(summary) == [
        "CONTRACT-2026-0805",
        "/outputs/final-report.md",
    ]


def test_default_summary_prompt_owns_nine_fields_and_framework_protocol_is_structure_neutral() -> None:
    """九维字段属于默认策略；框架只追加与字段结构无关的累计合并约束。"""
    model = _SummaryModel()
    middleware = create_context_compaction_middleware(
        model=model,
        summary_prompt=DEFAULT_YUXI_SUMMARY_PROMPT,
    )

    rendered = middleware._render_summary_prompt(
        "## files/code\n/outputs/keep.py\n## errors/fixes\nERR-417",
        [HumanMessage(content="新消息没有重复旧路径和错误码")],
        4_096,
    )

    for label in (
        "intent",
        "concepts",
        "files/code",
        "errors/fixes",
        "progress",
        "user messages",
        "pending tasks",
        "current work",
        "next step",
    ):
        assert f"## {label}" in DEFAULT_YUXI_SUMMARY_PROMPT
        assert label in rendered
    assert "previous_summary 是累计检查点" in rendered
    assert "新消息仅增量补充" in rendered
    assert "只有用户明确取消、替换、更正或确认完成" in rendered
    assert "助手称“已记录、已确认、已回复”" in rendered
    assert "输出前逐项核对旧约束、禁止项、标识符、路径和待办" in rendered
    assert "完成事项从待办转入进展" in rendered


@pytest.mark.unit
def test_custom_summary_prompt_is_not_extended_with_nine_dimension_fields() -> None:
    model = _SummaryModel()
    custom_prompt = """请按以下自定义结构输出：
## SESSION INTENT
## USER REQUIREMENTS AND PREFERENCES
## NEXT STEPS
<messages>{messages}</messages>"""
    middleware = create_summary_middleware(model=model, summary_prompt=custom_prompt)

    rendered = middleware._render_summary_prompt("旧摘要", [HumanMessage(content="新消息")], 512)

    assert "SESSION INTENT" in rendered
    assert "USER REQUIREMENTS AND PREFERENCES" in rendered
    assert "summary_update_protocol" in rendered
    assert "新消息仅增量补充" in rendered
    assert "消息更具体" in rendered
    assert "files/code" not in rendered
    assert "errors/fixes" not in rendered


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_oversized_single_user_turn_repeats_only_the_user_anchor_for_each_summary_piece(
    asynchronous: bool,
) -> None:
    """大工具链滚动摘要时，后续分块仍能看到同一条原始用户硬约束。"""
    model = _SummaryModel()
    model.profile = {
        "max_input_tokens": 2_000,
        "min_output_reserve_tokens": 300,
        "context_safety_tokens": 100,
    }
    _, request = _request([])
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    user_message = HumanMessage(
        content="不得实现 L4；关键路径 /outputs/context_projection.py；错误码 ERR-CONTEXT-417。",
    )
    messages = [
        user_message,
        AIMessage(
            content="读取大文档",
            tool_calls=[{"id": "call-1", "name": "read_file", "args": {"path": "/outputs/large.md"}}],
        ),
        ToolMessage(content="large tool observation\n" * 2_000, tool_call_id="call-1"),
    ]

    arguments = ("", messages, 300, resolve_context_budget(request))
    if asynchronous:
        await middleware._acreate_summary(*arguments)
    else:
        middleware._create_summary(*arguments)

    assert len(model.prompts) > 1
    for prompt in model.prompts:
        assert "<segment_user_anchor>" in prompt
        assert user_message.content in prompt
    assert model.prompts[-1].count("large tool observation") < 2_000


@pytest.mark.unit
def test_compaction_counts_final_request_and_commits_private_summary_after_success(
    archive_backend: _ArchiveBackend,
) -> None:
    messages = [
        HumanMessage(content="old user " + "x" * 2400, id="user-old"),
        AIMessage(content="old assistant", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["request"] = prepared
        return ExtendedModelResponse(
            model_response=ModelResponse(result=[AIMessage(content="answer", id="assistant-current")]),
            command=Command(update={"token_usage": {"prompt_tokens": 123}}),
        )

    result = middleware.wrap_model_call(request, handler)

    assert isinstance(result, ExtendedModelResponse)
    assert model.prompts
    assert "previous_summary 是累计检查点" in model.prompts[0]
    assert "先继承仍有效的旧要求" in model.prompts[0]
    assert captured["request"].messages == [messages[-1]]
    assert "private_conversation_context" in captured["request"].system_message.text
    assert "/outputs/conversation_history/" in captured["request"].system_message.text
    assert "不确定更早的用户要求或事实" in captured["request"].system_message.text
    assert "再继续操作或回答" in captured["request"].system_message.text
    assert "不得猜测" in captured["request"].system_message.text
    update = result.command.update
    assert update["token_usage"] == {"prompt_tokens": 123}
    assert "已归档旧对话：用户要完成项目文档。" in update["context_summary"]
    assert update["context_summary_quality"] == "custom"
    assert update["context_compacted_through"] == "assistant-old"
    assert update["context_archive_path"].endswith(".jsonl")
    assert update["context_revision"] == 4
    assert isinstance(update["messages"], Overwrite)
    assert len(archive_backend.writes) == 1
    assert '"message_id":"user-old"' in archive_backend.writes[0][1]
    l5_events = [event for event in request.runtime.stream_events if event["level"] == "L5"]
    assert [event["status"] for event in l5_events] == ["started", "finished"]
    assert l5_events[1]["archive_count"] == 1


@pytest.mark.unit
def test_compaction_keeps_tool_call_and_result_in_the_same_archived_turn() -> None:
    messages = [
        HumanMessage(content="old user " + "x" * 2400, id="user-old"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}], id="tool-call"),
        ToolMessage(content="tool result", tool_call_id="call-1", name="query_kb", id="tool-result"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["messages"] = prepared.messages
        return ModelResponse(result=[AIMessage(content="answer")])

    middleware.wrap_model_call(request, handler)

    assert captured["messages"] == [messages[-1]]
    assert "tool result" in "\n".join(model.prompts)


@pytest.mark.unit
def test_three_l5_revisions_feed_previous_checkpoint_into_the_next_summary() -> None:
    """滚动 L5 依赖累计检查点；每一版摘要必须成为下一版摘要的显式输入。"""
    class CumulativeSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return SimpleNamespace(text="已归档旧对话：用户要完成项目文档，并遵守 REQUIREMENT-001。")

    model = CumulativeSummaryModel()
    model.profile = {
        "max_input_tokens": 8_000,
        "min_output_reserve_tokens": 1_000,
        "context_safety_tokens": 200,
    }
    middleware = create_summary_middleware(model=model, summary_prompt="custom\n{messages}")
    messages = [
        HumanMessage(content="长期约束 REQUIREMENT-001：保持接口兼容。\n" + "x" * 16_000, id="user-0"),
        AIMessage(content="旧回复", id="assistant-0"),
        HumanMessage(content="开始第一阶段", id="user-1"),
    ]
    state = {"messages": messages, "context_revision": 0}

    for revision in range(1, 4):
        stream_events: list[dict] = []
        request = ModelRequest(
            model=model,
            messages=messages,
            system_message=SystemMessage(content="system instructions"),
            tools=[{"name": "query_kb", "description": "x"}],
            runtime=SimpleNamespace(
                context={},
                config={},
                stream_events=stream_events,
                stream_writer=stream_events.append,
            ),
            state=state,
        )
        result = middleware.wrap_model_call(
            request,
            lambda _prepared, current=revision: ModelResponse(
                result=[AIMessage(content="large answer " * 1_200, id=f"assistant-{current}")]
            ),
        )
        update = result.command.update
        assert update["context_revision"] == revision
        assert update["context_archive_path"].endswith(".jsonl")
        assert "<previous_summary>" in model.prompts[-1]
        if revision > 1:
            assert "已归档旧对话：用户要完成项目文档，并遵守 REQUIREMENT-001。" in model.prompts[-1]

        messages = list(update["messages"].value)
        state = {**state, **update, "messages": messages}
        messages.append(HumanMessage(content=f"开始第 {revision + 1} 阶段", id=f"user-{revision + 1}"))
        state["messages"] = messages

    assert len(model.prompts) >= 3
    assert "已归档旧对话：用户要完成项目文档，并遵守 REQUIREMENT-001。" in model.prompts[-1]


@pytest.mark.unit
def test_failed_main_call_does_not_commit_compaction_state(archive_backend: _ArchiveBackend) -> None:
    messages = [
        HumanMessage(content="old user " + "x" * 2400, id="user-old"),
        AIMessage(content="old assistant", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    def failing_handler(_prepared: ModelRequest):
        raise RuntimeError("primary model failed")

    with pytest.raises(RuntimeError, match="primary model failed"):
        middleware.wrap_model_call(request, failing_handler)

    # 原始历史可先幂等归档，但异常没有返回 Command，因此 checkpoint 不会提交裁剪或私有摘要状态。
    assert len(archive_backend.writes) == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_archive_failure_is_reported_as_compaction_lifecycle(
    asynchronous: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingArchiveBackend:
        def write(self, _path: str, _content: str):
            return SimpleNamespace(error="storage unavailable")

        async def awrite(self, path: str, content: str):
            return self.write(path, content)

    monkeypatch.setattr(
        summary_module,
        "create_agent_composite_backend",
        lambda _runtime: _FailingArchiveBackend(),
    )
    messages = [
        HumanMessage(content="old user " + "x" * 2400, id="user-old"),
        AIMessage(content="old assistant", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    with pytest.raises(ContextBudgetConfigurationError, match="历史上下文无法安全归档到线程文件"):
        if asynchronous:
            await middleware.awrap_model_call(
                request,
                lambda _prepared: pytest.fail("archive failure must precede the main model call"),
            )
        else:
            middleware.wrap_model_call(
                request,
                lambda _prepared: pytest.fail("archive failure must precede the main model call"),
            )

    l5_events = [event for event in request.runtime.stream_events if event["level"] == "L5"]
    assert [event["status"] for event in l5_events] == ["started", "failed"]
    failure = l5_events[1]
    assert failure["reason"] == "archive_failure"
    assert failure["tokens_after"] == failure["tokens_before"]
    assert failure["messages_removed"] == 0
    assert failure["rounds_removed"] == 0
    assert failure["archive_count"] == 0


@pytest.mark.unit
def test_current_single_human_tool_chain_has_no_safe_historical_segment(
    archive_backend: _ArchiveBackend,
) -> None:
    """P0 基线：旧实现按 HumanMessage 分段，无法压缩同一请求中的早期 closed round。"""
    messages = _single_human_tool_chain(30)
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    with pytest.raises(ContextBudgetConfigurationError, match="不存在可安全压缩的完整历史交互段"):
        middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail("容量错误必须发生在主模型调用前"),
        )

    assert model.prompts == []
    assert archive_backend.writes == []


@pytest.mark.unit
def test_current_incomplete_parallel_tool_round_is_not_compacted(
    archive_backend: _ArchiveBackend,
) -> None:
    """P0 基线：未闭合并行调用不能为腾出预算被摘要或伪造配对。"""
    messages = [
        HumanMessage(content="请检查并行任务", id="current-user"),
        AIMessage(
            content="x" * 3_000,
            tool_calls=[
                {"id": "call-a", "name": "query_kb", "args": {}},
                {"id": "call-b", "name": "query_kb", "args": {}},
            ],
            id="assistant-parallel",
        ),
        ToolMessage(content="已完成 A", tool_call_id="call-a", name="query_kb", id="tool-a"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    with pytest.raises(ContextBudgetConfigurationError, match="不存在可安全压缩的完整历史交互段"):
        middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail("未闭合工具协议不能发送给主模型"),
        )

    assert model.prompts == []
    assert archive_backend.writes == []


@pytest.mark.unit
def test_fixed_overhead_fails_before_compaction_with_actionable_diagnostics(
    archive_backend: _ArchiveBackend,
) -> None:
    """P0 基线：旧实现未先隔离固定开销，只会返回与历史不足相同的通用错误。"""
    messages = [
        HumanMessage(content="old user", id="user-old"),
        AIMessage(content="old answer", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model, request = _request(messages)
    request = request.override(
        system_message=SystemMessage(content="system " * 2_000),
        tools=[{"name": "oversized_tool", "description": "schema " * 2_000}],
    )
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    with pytest.raises(ContextBudgetConfigurationError, match="固定上下文开销超过可用输入预算") as raised:
        middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail("固定开销已超预算时不能调用主模型"),
        )

    assert "tool_count=1" in str(raised.value)
    assert "system_tokens=" in str(raised.value)
    assert "largest_tool_schema=oversized_tool" in str(raised.value)
    assert model.prompts == []
    assert archive_backend.writes == []


@pytest.mark.unit
def test_oversized_summary_does_not_commit_a_truncated_checkpoint() -> None:
    class OversizedSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return SimpleNamespace(text="摘要" * 10_000)

    messages = [
        HumanMessage(content="old user " + "x" * 2400, id="user-old"),
        AIMessage(content="old assistant", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model = OversizedSummaryModel()
    request = ModelRequest(
        model=model,
        messages=messages,
        system_message=SystemMessage(content="system instructions"),
        tools=[{"name": "query_kb", "description": "x"}],
        runtime=SimpleNamespace(context={}, config={}, stream_writer=lambda _event: None),
        state={"messages": messages, "context_revision": 0},
    )
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    main_model_called = False

    def handler(_prepared: ModelRequest):
        nonlocal main_model_called
        main_model_called = True
        return ModelResponse(result=[AIMessage(content="answer")])

    with pytest.raises(SummaryOutputTooLargeError, match="未遵守输出上限"):
        middleware.wrap_model_call(request, handler)

    assert not main_model_called


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_summary_generation_failure_does_not_commit_or_call_main_model(
    asynchronous: bool, archive_backend: _ArchiveBackend
) -> None:
    class FailingSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            raise RuntimeError("summary model unavailable")

    messages = [
        HumanMessage(content="old user " + "x" * 2_400, id="user-old"),
        AIMessage(content="old assistant", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model = FailingSummaryModel()
    _, request = _request(messages)
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    invoked = False

    def handler(_prepared: ModelRequest):
        nonlocal invoked
        invoked = True
        return ModelResponse(result=[AIMessage(content="answer")])

    async def async_handler(prepared: ModelRequest):
        return handler(prepared)

    with pytest.raises(RuntimeError, match="summary model unavailable"):
        if asynchronous:
            await middleware.awrap_model_call(request, async_handler)
        else:
            middleware.wrap_model_call(request, handler)

    assert len(archive_backend.writes) == 1
    assert not invoked
    assert len(model.prompts) == 1
    l5_events = [event for event in request.runtime.stream_events if event["level"] == "L5"]
    assert [event["status"] for event in l5_events] == ["started", "failed"]
    failure = l5_events[1]
    assert failure["reason"] == "summary_failure"
    assert failure["tokens_after"] == failure["tokens_before"]
    assert failure["messages_removed"] == 0
    assert failure["rounds_removed"] == 0
    assert failure["archive_count"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_summary_prompt_too_long_retries_once_with_a_tighter_budget(asynchronous: bool) -> None:
    class PromptTooLongOnceModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise ContextOverflowError("summary prompt too long")
            return SimpleNamespace(text="已收敛的检查点")

    model = PromptTooLongOnceModel()
    _, request = _request([])
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    messages = [
        HumanMessage(content="first request"),
        AIMessage(content="first answer", id="answer-1"),
        HumanMessage(content="second request"),
        AIMessage(content="second answer", id="answer-2"),
    ]
    arguments = ("", messages, 37, resolve_context_budget(request))

    if asynchronous:
        summary = await middleware._acreate_summary(*arguments)
    else:
        summary = middleware._create_summary(*arguments)

    assert summary == "已收敛的检查点"
    assert len(model.prompts) == 2
    assert "first request" in model.prompts[0]
    assert "first request" not in model.prompts[1]
    assert "second answer" in model.prompts[1]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_summary_prompt_too_long_stops_after_one_retry(
    asynchronous: bool,
) -> None:
    class PromptTooLongModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            raise ContextOverflowError("summary prompt too long")

    model = PromptTooLongModel()
    _, request = _request([])
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    messages = [
        HumanMessage(content="first request"),
        AIMessage(content="first answer", id="answer-1"),
        HumanMessage(content="second request"),
        AIMessage(content="second answer", id="answer-2"),
    ]
    arguments = ("", messages, 37, resolve_context_budget(request))

    with pytest.raises(ContextOverflowError, match="summary prompt too long"):
        if asynchronous:
            await middleware._acreate_summary(*arguments)
        else:
            middleware._create_summary(*arguments)

    assert len(model.prompts) == 2


@pytest.mark.unit
def test_summary_prompt_too_long_drops_floor_twenty_percent_complete_rounds() -> None:
    messages = _single_human_tool_chain(6)

    retry_messages = ContextCompactionMiddleware._summary_ptl_retry_messages(messages)

    # Claude Code 的 fallback 使用 floor(分组数 * 20%)，并至少移除一组；6 组
    # 因此只删除最旧 1 组，而不是向上取整后多丢失一组摘要输入。
    assert retry_messages == messages[3:]


@pytest.mark.unit
def test_summary_prompt_too_long_emits_a_classified_failure_event() -> None:
    class PromptTooLongModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            raise ContextOverflowError("summary prompt too long")

    messages = [
        HumanMessage(content="old user " + "x" * 2_400, id="user-old"),
        AIMessage(content="old assistant", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model = PromptTooLongModel()
    model.profile = {"max_input_tokens": 1_000, "min_output_reserve_tokens": 100, "context_safety_tokens": 80}
    _, request = _request(messages)
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    with pytest.raises(ContextOverflowError, match="summary prompt too long"):
        middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail("summary PTL must precede the main model call"),
        )

    assert len(model.prompts) == 1
    l5_events = [event for event in request.runtime.stream_events if event["level"] == "L5"]
    assert [event["status"] for event in l5_events] == ["started", "failed"]
    assert l5_events[1]["reason"] == "summary_prompt_too_long"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_truncated_summary_output_does_not_commit_checkpoint(
    asynchronous: bool, archive_backend: _ArchiveBackend
) -> None:
    class TruncatedSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return SimpleNamespace(text="不完整摘要", response_metadata={"finish_reason": "length"})

    messages = [
        HumanMessage(content="old user " + "x" * 2_400, id="user-old"),
        AIMessage(content="old assistant", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model = TruncatedSummaryModel()
    _, request = _request(messages)
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    main_model_called = False

    def handler(_prepared: ModelRequest):
        nonlocal main_model_called
        main_model_called = True
        return ModelResponse(result=[AIMessage(content="answer")])

    async def async_handler(prepared: ModelRequest):
        return handler(prepared)

    with pytest.raises(SummaryOutputTruncatedError, match="输出上限处截断"):
        if asynchronous:
            await middleware.awrap_model_call(request, async_handler)
        else:
            middleware.wrap_model_call(request, handler)

    assert len(archive_backend.writes) == 1
    assert not main_model_called
    l5_events = [event for event in request.runtime.stream_events if event["level"] == "L5"]
    assert [event["status"] for event in l5_events] == ["started", "failed"]
    assert l5_events[1]["reason"] == "summary_output_truncated"


@pytest.mark.unit
def test_l5_does_not_overwrite_activated_skill_state() -> None:
    messages = [
        HumanMessage(content="old user " + "x" * 2_400, id="user-old"),
        AIMessage(content="old assistant", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model, request = _request(messages)
    model.profile = {"max_input_tokens": 1_000, "min_output_reserve_tokens": 100, "context_safety_tokens": 80}
    request.runtime.context["_runtime_skill_metadata"] = {
        "flowchart": {
            "name": "Flowchart",
            "description": "Render a flowchart",
            "path": "/home/gem/skills/flowchart/SKILL.md",
        }
    }
    request = request.override(
        state={
            **request.state,
            "activated_skills": ["flowchart"],
            "todos": [{"content": "finish diagram", "status": "in_progress"}],
            "artifacts": [{"path": "/outputs/flow.svg"}],
        }
    )
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["system_message"] = prepared.system_message.text
        return ModelResponse(result=[AIMessage(content="answer")])

    result = middleware.wrap_model_call(request, handler)

    # L5 只 Overwrite messages；LangGraph 会保留 SkillsMiddleware 管理的独立状态键，
    # 因此 checkpoint 后的下一次工具循环仍可使用已经激活的 Skill。
    assert "activated_skills" not in result.command.update
    assert "todos" not in result.command.update
    assert "artifacts" not in result.command.update
    assert "<active_skill_recovery>" in captured["system_message"]
    assert "/home/gem/skills/flowchart/SKILL.md" in captured["system_message"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_summary_rechecks_segment_after_previous_batch_expands(asynchronous: bool) -> None:
    class BudgetBoundedSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            assert int(count_tokens_approximately([SystemMessage(content=prompt)])) <= 420
            return SimpleNamespace(text="摘要" * 20)

    _, request = _request([])
    model = BudgetBoundedSummaryModel()
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    arguments = (
        "",
        [HumanMessage(content="a" * 800), HumanMessage(content="b" * 800)],
        200,
        resolve_context_budget(request),
    )
    if asynchronous:
        summary = await middleware._acreate_summary(*arguments)
    else:
        summary = middleware._create_summary(*arguments)

    assert summary
    assert len(model.prompts) > 2


@pytest.mark.unit
def test_thousand_turn_history_commits_a_bounded_checkpoint(archive_backend: _ArchiveBackend) -> None:
    messages = [
        message
        for turn in range(1_000)
        for message in (
            HumanMessage(content=f"question {turn}", id=f"user-{turn}"),
            AIMessage(content=f"answer {turn}", id=f"assistant-{turn}"),
        )
    ]
    messages.append(HumanMessage(content="current question", id="current-user"))
    model, request = _request(messages)
    # 600-token fixture 只能容纳归档回执，无法保留最小九维 checkpoint；这里使用仍然很小
    # 的独立窗口验证长历史稳定收敛，而不是重新引入字符截断来迁就测试替身。
    model.profile = {"max_input_tokens": 1_000, "min_output_reserve_tokens": 100, "context_safety_tokens": 80}
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    result = middleware.wrap_model_call(request, lambda _prepared: ModelResponse(result=[AIMessage(content="answer")]))

    update = result.command.update
    assert len(update["messages"].value) < 100
    assert update["context_compacted_through"] != ""
    assert update["context_archive_path"].endswith(".jsonl")
    assert len(archive_backend.writes[0][1].splitlines()) > 1_000


@pytest.mark.unit
def test_old_multimodal_content_is_archived_without_entering_summary_prompt(archive_backend: _ArchiveBackend) -> None:
    image_data = "base64-image-data" * 1_000
    messages = [
        HumanMessage(
            content=[
                {"type": "text", "text": "请分析图片 " + "x" * 2400},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
            ],
            id="image-user",
        ),
        AIMessage(content="旧图片分析", id="image-answer"),
        HumanMessage(content="继续", id="current-user"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    middleware.wrap_model_call(request, lambda _prepared: ModelResponse(result=[AIMessage(content="answer")]))

    summary_prompts = "\n".join(model.prompts)
    assert image_data not in summary_prompts
    assert "多模态内容已保存在私有归档中" in summary_prompts
    assert image_data in archive_backend.writes[0][1]


@pytest.mark.unit
def test_final_request_evicts_large_ordinary_tool_result_before_model_call(monkeypatch) -> None:
    class _Backend:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str]] = []

        def write(self, path: str, content: str):
            self.writes.append((path, content))
            return SimpleNamespace(error=None)

    backend = _Backend()
    monkeypatch.setattr(summary_module, "create_agent_composite_backend", lambda _runtime: backend)
    raw_result = "large result\n" * 1_000
    messages = [
        HumanMessage(content="current user", id="user-current"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}]),
        ToolMessage(content=raw_result, tool_call_id="call-1", name="query_kb", id="tool-result"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["messages"] = prepared.messages
        return ModelResponse(result=[AIMessage(content="answer")])

    result = middleware.wrap_model_call(request, handler)

    assert len(backend.writes) == 1
    assert backend.writes[0][1] == raw_result
    assert "[Tool result saved]" in captured["messages"][-1].content
    assert raw_result not in captured["messages"][-1].content
    assert "context_summary" not in result.command.update
    assert raw_result not in result.command.update["messages"].value[-2].content


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_final_request_evicts_large_completed_tool_call_arguments(
    asynchronous: bool,
    archive_backend: _ArchiveBackend,
) -> None:
    raw_definition = "node definition\n" * 1_000
    messages = [
        HumanMessage(content="生成流程图", id="user-current"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-write",
                    "name": "write_file",
                    "args": {"file_path": "/outputs/spec.flow.json", "content": raw_definition},
                }
            ],
            id="tool-call",
        ),
        ToolMessage(content="文件已写入", tool_call_id="call-write", name="write_file", id="tool-result"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["messages"] = prepared.messages
        return ModelResponse(result=[AIMessage(content="answer")])

    async def async_handler(prepared: ModelRequest):
        return handler(prepared)

    if asynchronous:
        result = await middleware.awrap_model_call(request, async_handler)
    else:
        result = middleware.wrap_model_call(request, handler)

    saved_args = captured["messages"][1].tool_calls[0]["args"]
    assert "_yuxi_saved_arguments_path" in saved_args
    assert raw_definition not in str(captured["messages"][1].tool_calls)
    assert '"content":"node definition\\n' in archive_backend.writes[0][1]
    assert "context_summary" not in result.command.update


@pytest.mark.unit
def test_completed_tool_call_argument_candidate_prefers_largest_reduction() -> None:
    messages = [
        HumanMessage(content="生成两个文件", id="user-current"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-large",
                    "name": "write_file",
                    "args": {"file_path": "/outputs/large.txt", "content": "large\n" * 2_000},
                },
                {
                    "id": "call-small",
                    "name": "write_file",
                    "args": {"file_path": "/outputs/small.txt", "content": "small\n" * 500},
                },
            ],
            id="tool-calls",
        ),
        ToolMessage(content="大文件已写入", tool_call_id="call-large", name="write_file"),
        ToolMessage(content="小文件已写入", tool_call_id="call-small", name="write_file"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    candidate = middleware._next_completed_tool_call_arguments_candidate(
        request,
        messages=messages,
        summary="",
        budget=resolve_context_budget(request),
        failed=set(),
    )

    assert candidate is not None
    assert candidate["call_index"] == 0
    assert candidate["path"].endswith(".txt")
    assert '"content":"large\\n' in candidate["content"]


@pytest.mark.unit
def test_completed_tool_call_arguments_keep_small_structured_args() -> None:
    messages = [
        HumanMessage(content="current user " + "x" * 2_400, id="user-current"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-export",
                    "name": "export_office_file",
                    "args": {
                        "definition_path": "/outputs/report.json",
                        "output_format": "pdf",
                        "output_name": "report",
                    },
                }
            ],
            id="tool-call",
        ),
        ToolMessage(content="已生成 PDF", tool_call_id="call-export", name="export_office_file"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    candidate = middleware._next_completed_tool_call_arguments_candidate(
        request,
        messages=messages,
        summary="",
        budget=resolve_context_budget(request),
        failed=set(),
    )

    assert candidate is None


@pytest.mark.unit
def test_completed_tool_call_arguments_never_archive_ask_user_question() -> None:
    messages = [
        HumanMessage(content="请先向我确认", id="user-current"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-question",
                    "name": "ask_user_question",
                    "args": {
                        "questions": [
                            {
                                "question": "风险等级" + "重要" * 2_000,
                                "options": [
                                    {"label": "低", "value": "低"},
                                    {"label": "中", "value": "中"},
                                ],
                            }
                        ]
                    },
                },
                {
                    "id": "call-write",
                    "name": "write_file",
                    "args": {
                        "file_path": "/outputs/result.txt",
                        "content": "result\n" * 500,
                    },
                },
            ],
            id="tool-calls",
        ),
        ToolMessage(content="已回答", tool_call_id="call-question", name="ask_user_question"),
        ToolMessage(content="已写入", tool_call_id="call-write", name="write_file"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    candidate = middleware._next_completed_tool_call_arguments_candidate(
        request,
        messages=messages,
        summary="",
        budget=resolve_context_budget(request),
        failed=set(),
    )

    assert candidate is not None
    assert candidate["call_index"] == 1
    assert '"content":"result\\n' in candidate["content"]


@pytest.mark.unit
def test_final_request_replaces_large_source_window_without_second_offload(monkeypatch) -> None:
    monkeypatch.setattr(
        summary_module,
        "create_agent_composite_backend",
        lambda _runtime: (_ for _ in ()).throw(AssertionError("source window must not be offloaded")),
    )
    raw_result = "source line\n" * 1_000
    messages = [
        HumanMessage(content="current user", id="user-current"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call-read", "name": "read_file", "args": {"path": "/outputs/a.txt", "limit": 100}}],
        ),
        ToolMessage(content=raw_result, tool_call_id="call-read", name="read_file", id="tool-result"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["messages"] = prepared.messages
        return ModelResponse(result=[AIMessage(content="answer")])

    result = middleware.wrap_model_call(request, handler)

    assert "更小的 offset/limit 窗口" in captured["messages"][-1].content
    assert '"path":"/outputs/a.txt"' in captured["messages"][-1].content
    assert raw_result not in captured["messages"][-1].content
    assert raw_result not in result.command.update["messages"].value[-2].content
    l1_event = next(event for event in request.runtime.stream_events if event["level"] == "L1")
    assert l1_event["status"] == "finished"
    assert l1_event["tool_results_projected"] == 1
    assert l1_event["tokens_saved"] > 0
    assert raw_result not in str(request.runtime.stream_events)


@pytest.mark.unit
def test_l2_projects_old_completed_tool_result_and_keeps_tool_round(archive_backend: _ArchiveBackend) -> None:
    raw_result = "historical result\n" * 1_000
    messages = [
        HumanMessage(content="old request", id="old-user"),
        AIMessage(
            content="",
            id="old-tool-call",
            tool_calls=[{"id": "old-call", "name": "query_kb", "args": {"query": "architecture"}}],
        ),
        ToolMessage(content=raw_result, tool_call_id="old-call", name="query_kb", id="old-tool-result"),
        AIMessage(content="old answer", id="old-final-answer"),
        HumanMessage(content="current request", id="current-user"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["messages"] = prepared.messages
        return ModelResponse(result=[AIMessage(content="answer")])

    result = middleware.wrap_model_call(request, handler)

    old_round = captured["messages"][:3]
    assert old_round[1].tool_calls[0]["id"] == "old-call"
    assert old_round[2].tool_call_id == "old-call"
    assert "[Tool result saved]" in old_round[2].content
    assert raw_result not in old_round[2].content
    assert archive_backend.writes[0][1] == raw_result
    assert raw_result not in result.command.update["messages"].value[2].content
    assert [event["level"] for event in request.runtime.stream_events] == ["L1", "L2", "L3", "L5"]
    l2_event = request.runtime.stream_events[1]
    assert l2_event["status"] == "finished"
    assert l2_event["tool_results_projected"] == 1
    assert l2_event["tokens_after"] < l2_event["tokens_before"]
    assert request.runtime.stream_events[-1]["status"] == "skipped"
    assert raw_result not in str(request.runtime.stream_events)


@pytest.mark.unit
def test_l2_projects_old_completed_tool_arguments_and_keeps_readable_receipt(archive_backend: _ArchiveBackend) -> None:
    raw_definition = "step definition\n" * 1_000
    messages = [
        HumanMessage(content="old request", id="old-user"),
        AIMessage(
            content="",
            id="old-tool-call",
            tool_calls=[
                {
                    "id": "old-call",
                    "name": "write_file",
                    "args": {"file_path": "/outputs/old-plan.txt", "content": raw_definition},
                }
            ],
        ),
        ToolMessage(content="written", tool_call_id="old-call", name="write_file", id="old-tool-result"),
        AIMessage(content="old answer", id="old-final-answer"),
        HumanMessage(content="current request", id="current-user"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["messages"] = prepared.messages
        return ModelResponse(result=[AIMessage(content="answer")])

    middleware.wrap_model_call(request, handler)

    saved_args = captured["messages"][1].tool_calls[0]["args"]
    assert saved_args["_yuxi_saved_arguments_path"].endswith(".txt")
    assert raw_definition not in str(captured["messages"][1].tool_calls)
    assert any('"content":"step definition\\n' in content for _, content in archive_backend.writes)
    l2_event = next(event for event in request.runtime.stream_events if event["level"] == "L2")
    assert l2_event["status"] == "finished"
    assert l2_event["tool_arguments_projected"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_projection_events_are_identical_for_sync_and_async_paths() -> None:
    raw_result = "historical result\n" * 1_000
    messages = [
        HumanMessage(content="old request", id="old-user"),
        AIMessage(
            content="",
            id="old-tool-call",
            tool_calls=[{"id": "old-call", "name": "query_kb", "args": {"query": "architecture"}}],
        ),
        ToolMessage(content=raw_result, tool_call_id="old-call", name="query_kb", id="old-tool-result"),
        AIMessage(content="old answer", id="old-final-answer"),
        HumanMessage(content="current request", id="current-user"),
    ]
    sync_model, sync_request = _request(messages)
    async_model, async_request = _request(messages)

    async def async_handler(_prepared: ModelRequest) -> ModelResponse:
        return ModelResponse(result=[AIMessage(content="answer")])

    create_summary_middleware(model=sync_model, summary_prompt="summary\n{messages}").wrap_model_call(
        sync_request,
        lambda _prepared: ModelResponse(result=[AIMessage(content="answer")]),
    )
    await create_summary_middleware(model=async_model, summary_prompt="summary\n{messages}").awrap_model_call(
        async_request,
        async_handler,
    )

    assert sync_request.runtime.stream_events == async_request.runtime.stream_events


@pytest.mark.unit
def test_l3_projects_early_rounds_of_a_single_human_request_and_protects_tail(
    archive_backend: _ArchiveBackend,
) -> None:
    messages = [HumanMessage(content="complete the checks", id="current-user")]
    for index in range(30):
        content = "large early input\n" * 400 if index < 28 else "small"
        call_id = f"call-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    id=f"assistant-{index}",
                    tool_calls=[{"id": call_id, "name": "write_file", "args": {"content": content}}],
                ),
                ToolMessage(content="written", tool_call_id=call_id, name="write_file", id=f"tool-{index}"),
            ]
        )
    model, request = _request(messages)
    model.profile = {"max_input_tokens": 32_000, "min_output_reserve_tokens": 4_096, "context_safety_tokens": 1_024}
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["messages"] = prepared.messages
        return ModelResponse(result=[AIMessage(content="answer")])

    middleware.wrap_model_call(request, handler)

    assert "_yuxi_saved_arguments_path" in captured["messages"][1].tool_calls[0]["args"]
    assert captured["messages"][-2].tool_calls[0]["args"] == {"content": "small"}
    assert captured["messages"][-1].tool_call_id == "call-29"
    assert archive_backend.writes
    l3_event = next(event for event in request.runtime.stream_events if event["level"] == "L3")
    assert l3_event["status"] == "finished"
    assert l3_event["tool_arguments_projected"] > 0
    assert l3_event["protected_messages"] == 4


@pytest.mark.unit
def test_l5_compacts_early_rounds_of_a_single_human_request_after_l3() -> None:
    messages = [HumanMessage(content="complete the investigation", id="current-user")]
    for index in range(30):
        call_id = f"call-{index}"
        messages.extend(
            [
                AIMessage(
                    content="tool observation " * 1_000 if index < 28 else "recent observation",
                    id=f"assistant-{index}",
                    tool_calls=[{"id": call_id, "name": "query_kb", "args": {}}],
                ),
                ToolMessage(content="ok", tool_call_id=call_id, name="query_kb", id=f"tool-{index}"),
            ]
        )
    model, request = _request(messages)
    model.profile = {"max_input_tokens": 32_000, "min_output_reserve_tokens": 4_096, "context_safety_tokens": 1_024}
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["request"] = prepared
        return ModelResponse(result=[AIMessage(content="continued")])

    result = middleware.wrap_model_call(request, handler)

    assert "<summary_update_protocol>" in model.prompts[0]
    assert "files/code" not in model.prompts[0]
    assert captured["request"].messages[0].id == "current-user"
    assert captured["request"].messages[-4].id == "assistant-28"
    assert captured["request"].messages[-2].id == "assistant-29"
    assert result.command.update["context_summary"]
    assert result.command.update["context_revision"] == 4


@pytest.mark.unit
def test_incomplete_tool_protocol_fails_before_model_call() -> None:
    messages = [
        HumanMessage(content="current request", id="current-user"),
        AIMessage(
            content="",
            id="unfinished-call",
            tool_calls=[{"id": "unfinished", "name": "query_kb", "args": {}}],
        ),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    invoked = False

    def handler(_prepared: ModelRequest):
        nonlocal invoked
        invoked = True
        return ModelResponse(result=[AIMessage(content="answer")])

    with pytest.raises(ToolProtocolError):
        middleware.wrap_model_call(request, handler)

    assert not invoked


@pytest.mark.unit
def test_l1_externalizes_current_input_when_fixed_context_makes_it_unadmittable(monkeypatch) -> None:
    class _Backend:
        def write(self, _path: str, _content: str):
            return SimpleNamespace(error=None)

    monkeypatch.setattr(summary_module, "create_agent_composite_backend", lambda _runtime: _Backend())
    message = HumanMessage(content="current input " * 80, id="user-current")
    model, request = _request([message])
    request = request.override(system_message=SystemMessage(content="fixed system " * 30))
    budget = resolve_context_budget(request)
    assert count_tokens_approximately([message]) < budget.prompt_budget
    assert estimate_model_request(request).admission > budget.prompt_budget
    captured = {}

    def handler(prepared: ModelRequest):
        captured["message"] = prepared.messages[0]
        return ModelResponse(result=[AIMessage(content="answer")])

    create_summary_middleware(model=model, summary_prompt="summary\n{messages}").wrap_model_call(request, handler)

    assert "已保存到活动上下文之外" in captured["message"].content
    l1_event = next(event for event in request.runtime.stream_events if event["level"] == "L1")
    assert l1_event["input_externalized"] == 1
    assert l1_event["tokens_after"] <= budget.prompt_budget


@pytest.mark.unit
def test_current_oversized_user_input_is_persisted_before_model_call(monkeypatch) -> None:
    class _Backend:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str]] = []

        def write(self, path: str, content: str):
            self.writes.append((path, content))
            return SimpleNamespace(error=None)

    backend = _Backend()
    monkeypatch.setattr(summary_module, "create_agent_composite_backend", lambda _runtime: backend)
    raw_input = "user input\n" * 1_000
    messages = [HumanMessage(content=raw_input, id="user-current")]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    captured = {}

    def handler(prepared: ModelRequest):
        captured["messages"] = prepared.messages
        return ModelResponse(result=[AIMessage(content="answer")])

    result = middleware.wrap_model_call(request, handler)

    assert len(backend.writes) == 1
    assert backend.writes[0][1] == raw_input
    assert "/outputs/conversation_history/user-current-" in backend.writes[0][0]
    assert "已保存到活动上下文之外" in captured["messages"][0].content
    assert raw_input not in result.command.update["messages"].value[0].content
    l1_event = next(event for event in request.runtime.stream_events if event["level"] == "L1")
    assert l1_event["status"] == "finished"
    assert l1_event["input_externalized"] == 1
    assert l1_event["tokens_saved"] > 0


@pytest.mark.unit
def test_provider_overflow_forces_one_safe_history_compaction_before_retry() -> None:
    messages = [
        HumanMessage(content="old user " + "x" * 1_000, id="user-old"),
        AIMessage(content="old answer", id="assistant-old"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model, request = _request(messages)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    calls = 0

    def handler(prepared: ModelRequest):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ContextOverflowError("provider rejected the estimated request")
        assert prepared.messages == [messages[-1]]
        return ModelResponse(result=[AIMessage(content="answer")])

    result = middleware.wrap_model_call(request, handler)

    assert calls == 2
    assert model.prompts
    assert "已归档旧对话：用户要完成项目文档。" in result.command.update["context_summary"]


@pytest.mark.unit
def test_provider_overflow_without_usage_compacts_all_completed_history() -> None:
    messages = [
        HumanMessage(content="old user one", id="user-old-1"),
        AIMessage(content="old answer one", id="assistant-old-1"),
        HumanMessage(content="old user two", id="user-old-2"),
        AIMessage(content="old answer two", id="assistant-old-2"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model = _SummaryModel()
    model.profile = {"max_input_tokens": 2_000, "min_output_reserve_tokens": 500, "context_safety_tokens": 100}
    _, request = _request(messages)
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    calls = 0

    def handler(prepared: ModelRequest):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ContextOverflowError("provider rejected without usage")
        assert prepared.messages == [messages[-1]]
        return ModelResponse(result=[AIMessage(content="answer")])

    middleware.wrap_model_call(request, handler)

    assert calls == 2
    assert len(model.prompts) == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_measured_overflow_uses_calibration_until_request_fits(asynchronous: bool) -> None:
    messages = [
        HumanMessage(content="old user one " + "x" * 800, id="user-old-1"),
        AIMessage(content="old answer one", id="assistant-old-1"),
        HumanMessage(content="old user two " + "x" * 800, id="user-old-2"),
        AIMessage(content="old answer two", id="assistant-old-2"),
        HumanMessage(content="current user", id="user-current"),
    ]
    model = _SummaryModel()
    model.profile = {"max_input_tokens": 2_000, "min_output_reserve_tokens": 500, "context_safety_tokens": 100}
    _, request = _request(messages)
    request = request.override(model=model)
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")
    initial = estimate_model_request(request)
    budget = resolve_context_budget(request)
    assert initial.admission < budget.prompt_budget
    actual_input = budget.prompt_budget + 100
    calls = 0

    def handler(prepared: ModelRequest):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ContextWindowExceededError(
                "measured overflow",
                {
                    "calibration_key": initial.calibration_key,
                    "calibration_samples": 1,
                    "max_positive_error": max(actual_input - initial.baseline, 0),
                    "max_ratio": max(actual_input / initial.baseline, 1.0),
                },
            )
        assert prepared.messages[-1] == messages[-1]
        assert len(prepared.messages) < len(messages)
        assert middleware._request_tokens(prepared) <= budget.prompt_budget
        return ModelResponse(result=[AIMessage(content="answer")])

    async def async_handler(prepared: ModelRequest):
        return handler(prepared)

    if asynchronous:
        await middleware.awrap_model_call(request, async_handler)
    else:
        middleware.wrap_model_call(request, handler)

    assert calls == 2
    assert model.prompts
