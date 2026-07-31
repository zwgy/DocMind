from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command, Overwrite

from yuxi.agents.middlewares import summary as summary_module
from yuxi.agents.middlewares.summary import YuxiSummarizationMiddleware, create_summary_middleware
from yuxi.agents.middlewares.token_usage import (
    ContextBudgetConfigurationError,
    ContextWindowExceededError,
    estimate_model_request,
    resolve_context_budget,
)


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
def test_factory_uses_project_owned_budget_middleware() -> None:
    model = _SummaryModel()
    middleware = create_summary_middleware(model=model, summary_prompt="summary\n{messages}")

    assert isinstance(middleware, YuxiSummarizationMiddleware)
    assert not hasattr(middleware, "_lc_helper")
    assert model.bound_configs == [{"metadata": {"lc_source": "summarization"}}]


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
    assert "Replace and merge previous_summary" in model.prompts[0]
    assert "keep verified exact facts" in model.prompts[0]
    assert captured["request"].messages == [messages[-1]]
    assert "private_conversation_context" in captured["request"].system_message.text
    assert "/outputs/conversation_history/" in captured["request"].system_message.text
    assert "never guess" in captured["request"].system_message.text
    update = result.command.update
    assert update["token_usage"] == {"prompt_tokens": 123}
    assert "已归档旧对话：用户要完成项目文档。" in update["context_summary"]
    assert update["context_compacted_through"] == "assistant-old"
    assert update["context_archive_path"].endswith(".jsonl")
    assert update["context_revision"] == 4
    assert isinstance(update["messages"], Overwrite)
    assert len(archive_backend.writes) == 1
    assert '"message_id":"user-old"' in archive_backend.writes[0][1]
    assert request.runtime.stream_events == [
        {"type": "context_compaction", "status": "started"},
        {"type": "context_compaction", "status": "finished"},
    ]


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

    assert request.runtime.stream_events == [
        {"type": "context_compaction", "status": "started"},
        {"type": "context_compaction", "status": "finished"},
    ]


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
def test_current_fixed_overhead_reports_generic_capacity_error_without_diagnosis(
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

    with pytest.raises(ContextBudgetConfigurationError, match="不存在可安全压缩的完整历史交互段") as raised:
        middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail("固定开销已超预算时不能调用主模型"),
        )

    assert "工具" not in str(raised.value)
    assert model.prompts == []
    assert archive_backend.writes == []


@pytest.mark.unit
def test_oversized_summary_is_bounded_and_marked_degraded() -> None:
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
    captured = {}

    def handler(prepared: ModelRequest):
        captured["request"] = prepared
        return ModelResponse(result=[AIMessage(content="answer")])

    result = middleware.wrap_model_call(request, handler)

    assert middleware._request_tokens(captured["request"]) <= 420
    assert result.command.update["context_summary_quality"] == "degraded"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_summary_generation_failure_uses_archive_receipt(
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
    captured = {}

    def handler(prepared: ModelRequest):
        captured["request"] = prepared
        return ModelResponse(result=[AIMessage(content="answer")])

    async def async_handler(prepared: ModelRequest):
        return handler(prepared)

    if asynchronous:
        result = await middleware.awrap_model_call(request, async_handler)
    else:
        result = middleware.wrap_model_call(request, handler)

    assert len(archive_backend.writes) == 1
    assert result.command.update["context_summary_quality"] == "degraded"
    assert "private_context_archive" in captured["request"].system_message.text


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_summary_rechecks_segment_after_previous_batch_expands(asynchronous: bool) -> None:
    class BudgetBoundedSummaryModel(_SummaryModel):
        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            assert int(count_tokens_approximately([SystemMessage(content=prompt)])) <= 420
            return SimpleNamespace(text="摘要" * 500)

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
        summary, degraded = await middleware._acreate_summary(*arguments)
    else:
        summary, degraded = middleware._create_summary(*arguments)

    assert summary
    assert degraded
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
    assert "Multimodal content is preserved" in summary_prompts
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

    assert "narrower offset/limit window" in captured["messages"][-1].content
    assert '"path":"/outputs/a.txt"' in captured["messages"][-1].content
    assert raw_result not in captured["messages"][-1].content
    assert raw_result not in result.command.update["messages"].value[-2].content


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
    assert "stored outside the active context" in captured["messages"][0].content
    assert raw_input not in result.command.update["messages"].value[0].content


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
