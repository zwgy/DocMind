from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import Command, Overwrite

from yuxi.agents.middlewares.output_continuation import (
    OutputContinuationMiddleware,
    is_internal_output_continuation,
)
from yuxi.agents.middlewares.token_usage import ModelOutputIncompleteError


def _snapshot(outcome: str, *, output_tokens: int = 4_096) -> dict:
    return {
        "response_outcome": outcome,
        "provider_input_tokens": 1_000,
        "provider_output_tokens": output_tokens,
        "prompt_budget": 27_648,
    }


def _response(content: str, outcome: str = "completed", *, messages_update=None) -> ExtendedModelResponse:
    update = {"token_usage": _snapshot(outcome)}
    if messages_update is not None:
        update["messages"] = messages_update
    return ExtendedModelResponse(
        model_response=ModelResponse(
            result=[
                AIMessage(
                    content=content,
                    response_metadata={"finish_reason": "length" if outcome == "output_exhausted" else "stop"},
                )
            ]
        ),
        command=Command(update=update),
    )


def _request(*, state: dict | None = None, events: list[dict] | None = None) -> ModelRequest:
    stream_events = events if events is not None else []
    model = SimpleNamespace(
        model_name="qwen-local",
        profile={
            "max_input_tokens": 32_768,
            "min_output_reserve_tokens": 4_096,
            "context_safety_tokens": 1_024,
        },
    )
    messages = list((state or {}).get("messages") or [HumanMessage(content="请生成详细报告", id="user-1")])
    return ModelRequest(
        model=model,
        messages=messages,
        system_message=SystemMessage(content="使用中文回答"),
        tools=[],
        state=state or {"messages": messages},
        runtime=SimpleNamespace(context={}, stream_writer=stream_events.append),
        model_settings={},
    )


@pytest.mark.unit
def test_completed_response_does_not_set_normal_output_limit() -> None:
    middleware = OutputContinuationMiddleware()
    request = _request()
    seen_settings: list[dict] = []

    def handler(prepared: ModelRequest):
        seen_settings.append(dict(prepared.model_settings))
        return _response("完整回答")

    result = middleware.wrap_model_call(request, handler)

    assert seen_settings == [{}]
    assert result.command.update == {"token_usage": _snapshot("completed")}


@pytest.mark.unit
def test_visible_length_commits_partial_then_continues_once_without_persisting_internal_message() -> None:
    middleware = OutputContinuationMiddleware()
    events: list[dict] = []
    first_request = _request(events=events)

    first = middleware.wrap_model_call(first_request, lambda _request: _response("第一段", "output_exhausted"))
    recovery = first.command.update["output_recovery"]

    assert recovery == {"attempts": 1, "continuations": 1, "output_limit": 8_192, "active": True}
    assert middleware.after_model({"output_recovery": recovery}, SimpleNamespace()) == {"jump_to": "model"}

    continued_state = {
        "messages": [
            HumanMessage(content="请生成详细报告", id="user-1"),
            AIMessage(content="第一段", id="assistant-partial"),
        ],
        "output_recovery": recovery,
    }
    second_request = _request(state=continued_state, events=events)
    captured_messages = []

    def continued_handler(prepared: ModelRequest):
        captured_messages.extend(prepared.messages)
        assert prepared.model_settings == {"max_tokens": 8_192}
        return _response("第二段")

    second = middleware.wrap_model_call(second_request, continued_handler)

    assert is_internal_output_continuation(captured_messages[-1])
    assert all(not is_internal_output_continuation(message) for message in second_request.messages)
    assert second.command.update["output_recovery"] is None
    assert [event["status"] for event in events] == ["started", "finished"]


@pytest.mark.unit
def test_empty_length_retries_original_request_once_and_preserves_inner_updates() -> None:
    middleware = OutputContinuationMiddleware()
    events: list[dict] = []
    request = _request(events=events)
    calls: list[ModelRequest] = []
    messages = Overwrite([HumanMessage(content="保留的有界消息")])

    def handler(prepared: ModelRequest):
        calls.append(prepared)
        if len(calls) == 1:
            raise ModelOutputIncompleteError("输出耗尽", _snapshot("output_exhausted"))
        return _response("重试成功", messages_update=messages)

    result = middleware.wrap_model_call(request, handler)

    assert len(calls) == 2
    assert calls[0].model_settings == {}
    assert calls[1].model_settings == {"max_tokens": 8_192}
    assert calls[1].messages == request.messages
    assert result.command.update["messages"] is messages
    assert result.command.update["token_usage"] == _snapshot("completed")
    assert "output_recovery" not in result.command.update
    assert [event["status"] for event in events] == ["started", "finished"]


@pytest.mark.unit
def test_truncated_tool_call_is_not_retried() -> None:
    middleware = OutputContinuationMiddleware()
    request = _request()
    calls = 0

    def handler(_prepared: ModelRequest):
        nonlocal calls
        calls += 1
        raise ModelOutputIncompleteError("工具调用截断", _snapshot("tool_call_truncated"))

    with pytest.raises(ModelOutputIncompleteError, match="工具调用截断"):
        middleware.wrap_model_call(request, handler)

    assert calls == 1


@pytest.mark.unit
def test_unverified_length_is_not_retried() -> None:
    middleware = OutputContinuationMiddleware()
    request = _request()
    calls = 0

    def handler(_prepared: ModelRequest):
        nonlocal calls
        calls += 1
        raise ModelOutputIncompleteError("截断原因不可验证", _snapshot("length_unverified", output_tokens=0))

    with pytest.raises(ModelOutputIncompleteError, match="不可验证"):
        middleware.wrap_model_call(request, handler)

    assert calls == 1


@pytest.mark.unit
def test_second_visible_length_is_preserved_but_does_not_jump_again() -> None:
    middleware = OutputContinuationMiddleware()
    recovery = {"attempts": 1, "continuations": 1, "output_limit": 8_192, "active": True}
    state = {
        "messages": [HumanMessage(content="问题"), AIMessage(content="第一段")],
        "output_recovery": recovery,
    }
    request = _request(state=state)

    result = middleware.wrap_model_call(request, lambda _request: _response("第二段仍被截断", "output_exhausted"))

    assert result.command.update["output_recovery"] is None
    assert middleware.after_model({"output_recovery": None}, SimpleNamespace()) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_empty_retry_matches_sync_behavior() -> None:
    middleware = OutputContinuationMiddleware()
    request = _request()
    calls = 0

    async def handler(prepared: ModelRequest):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelOutputIncompleteError("输出耗尽", _snapshot("output_exhausted", output_tokens=5_000))
        # 32K 窗口的第一阶段护栏是窗口四分之一，优先于 5K usage 翻倍得到的 10K。
        assert prepared.model_settings == {"max_tokens": 8_192}
        return _response("异步重试成功")

    result = await middleware.awrap_model_call(request, handler)

    assert calls == 2
    assert result.model_response.result[0].content == "异步重试成功"


@pytest.mark.unit
def test_new_real_user_message_cancels_stale_continuation_state() -> None:
    middleware = OutputContinuationMiddleware()
    state = {
        "messages": [
            HumanMessage(content="旧问题"),
            AIMessage(content="旧回答被截断"),
            HumanMessage(content="这是新问题"),
        ],
        "output_recovery": {"attempts": 1, "continuations": 1, "output_limit": 8_192, "active": True},
    }
    request = _request(state=state)
    captured: list = []

    def handler(prepared: ModelRequest):
        captured.extend(prepared.messages)
        return _response("新问题回答")

    result = middleware.wrap_model_call(request, handler)

    assert all(not is_internal_output_continuation(message) for message in captured)
    assert request.model_settings == {}
    assert result.command.update["output_recovery"] is None
