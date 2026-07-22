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
from yuxi.agents.middlewares.token_usage import resolve_context_budget


class _SummaryModel:
    profile = {"max_input_tokens": 600, "max_output_tokens": 100, "context_safety_tokens": 80}

    def __init__(self) -> None:
        self.prompts: list[str] = []

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
    return model, ModelRequest(
        model=model,
        messages=messages,
        system_message=SystemMessage(content="system instructions"),
        tools=[{"name": "query_kb", "description": "x"}],
        runtime=SimpleNamespace(context={}, config={}),
        state={"messages": messages, "context_revision": 3},
    )


@pytest.mark.unit
def test_factory_uses_project_owned_budget_middleware() -> None:
    middleware = create_summary_middleware(model=_SummaryModel(), summary_prompt="summary\n{messages}")

    assert isinstance(middleware, YuxiSummarizationMiddleware)
    assert not hasattr(middleware, "_lc_helper")


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
    assert captured["request"].messages == [messages[-1]]
    assert "private_conversation_context" in captured["request"].system_message.text
    update = result.command.update
    assert update["token_usage"] == {"prompt_tokens": 123}
    assert "已归档旧对话：用户要完成项目文档。" in update["context_summary"]
    assert update["context_compacted_through"] == "assistant-old"
    assert update["context_archive_path"].endswith(".jsonl")
    assert update["context_revision"] == 4
    assert isinstance(update["messages"], Overwrite)
    assert len(archive_backend.writes) == 1
    assert '"message_id":"user-old"' in archive_backend.writes[0][1]


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
        runtime=SimpleNamespace(context={}, config={}),
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
