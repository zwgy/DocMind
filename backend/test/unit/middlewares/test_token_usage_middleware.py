from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from yuxi.agents.middlewares.token_usage import (
    ContextWindowExceededError,
    ModelOutputIncompleteError,
    TokenUsageMiddleware,
    estimate_model_request,
    resolve_context_budget,
    resolve_tool_token_limit,
)
from yuxi.agents.backends.composite import _TOOL_RESULT_SAVED_MARKER


def _model(max_input_tokens: int = 2_000) -> SimpleNamespace:
    return SimpleNamespace(
        model_name="test-model",
        openai_api_base="http://model.test/v1",
        profile={
            "max_input_tokens": max_input_tokens,
            "min_output_reserve_tokens": 500,
            "context_safety_tokens": 100,
        },
    )


def _request(*, state: dict | None = None, tools: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        model=_model(),
        state=state or {"messages": [HumanMessage(content="old message")]},
        messages=[HumanMessage(content="current message")],
        system_message=SystemMessage(content="system prompt"),
        tools=tools or [],
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )


@pytest.mark.parametrize(
    ("context_window", "expected_limit"),
    [
        (32_768, 3_072),
        (65_536, 3_776),
        (131_072, 7_872),
        (262_144, 16_064),
        (1_048_576, 16_384),
    ],
)
def test_auto_tool_token_limit_scales_with_prompt_budget(context_window: int, expected_limit: int) -> None:
    model = SimpleNamespace(
        model_name="test-model",
        profile={
            "max_input_tokens": context_window,
            "min_output_reserve_tokens": 4_096,
            "context_safety_tokens": 1_024,
        },
    )
    context = SimpleNamespace()

    limit = resolve_tool_token_limit(context, model=model)

    assert limit == expected_limit
    assert context._resolved_tool_token_limit_tokens == expected_limit


def test_resolved_tool_token_limit_can_be_reused_without_loading_model_again() -> None:
    context = SimpleNamespace(_resolved_tool_token_limit_tokens=7_872)

    assert resolve_tool_token_limit(context) == 7_872


def test_auto_tool_token_limit_requires_model_on_first_resolution() -> None:
    context = SimpleNamespace()

    with pytest.raises(ValueError, match="模型预算"):
        resolve_tool_token_limit(context)


@pytest.mark.asyncio
async def test_token_usage_middleware_records_provider_total_and_estimated_breakdown() -> None:
    middleware = TokenUsageMiddleware()
    tool_schema = {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search project documents.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
    request = _request(tools=[tool_schema])

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="answer",
                    usage_metadata={"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
                )
            ]
        )

    result = await middleware.awrap_model_call(request, handler)

    assert isinstance(result, ExtendedModelResponse)
    token_usage = result.command.update["token_usage"]
    assert token_usage["input_tokens"] == 12
    assert token_usage["input_source"] == "provider_usage"
    assert token_usage["provider_input_tokens"] == 12
    assert token_usage["provider_output_tokens"] == 5
    assert token_usage["baseline_input_tokens"] > 0
    assert token_usage["fallback_input_tokens"] >= token_usage["baseline_input_tokens"]
    assert token_usage["estimated_input_tokens"] == sum(token_usage["breakdown_estimate"].values())
    assert token_usage["breakdown_estimate"]["system"] > 0
    assert token_usage["breakdown_estimate"]["tools"] > 0
    assert token_usage["protocol_correction_tokens"] == 12 - token_usage["estimated_input_tokens"]
    assert token_usage["tool_count"] == 1
    assert len(token_usage["tool_schema_hash"]) == 64
    assert len(token_usage["system_prompt_hash"]) == 64
    assert token_usage["context_window"] == 2_000
    assert token_usage["prompt_budget"] == 1_400
    assert token_usage["input_budget_delta"] == 1_388
    assert token_usage["context_remaining_after_input"] == 1_988
    assert token_usage["calibration_samples"] == 1


@pytest.mark.asyncio
async def test_usage_calibrates_the_next_request_with_bucketed_positive_gap() -> None:
    def token_counter(_messages, **_kwargs):
        return 20_284

    middleware = TokenUsageMiddleware(token_counter=token_counter)
    request = _request()
    request.model = _model(max_input_tokens=40_000)

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="answer",
                    usage_metadata={"input_tokens": 32_601, "output_tokens": 167, "total_tokens": 32_768},
                )
            ]
        )

    result = await middleware.awrap_model_call(request, handler)
    snapshot = result.command.update["token_usage"]
    next_request = _request(state={"token_usage": snapshot})
    next_request.model = request.model

    estimate = estimate_model_request(next_request, token_counter=token_counter)

    assert snapshot["max_positive_error"] == 12_317
    assert snapshot["max_ratio"] == pytest.approx(32_601 / 20_284, rel=1e-6)
    assert snapshot["max_positive_gap_by_bucket"] == {"medium": 12_317}
    assert estimate.source == "calibrated_estimate"
    assert estimate.admission == estimate.fallback + 12_317


def test_tool_schema_change_preserves_same_deployment_bucket_calibration() -> None:
    def token_counter(_messages, **_kwargs):
        return 100

    first_request = _request(tools=[{"name": "first", "description": "first tool"}])
    first_estimate = estimate_model_request(first_request, token_counter=token_counter)
    previous = {
        "calibration_key": first_estimate.calibration_key,
        "calibration_samples": 2,
        "max_positive_error": 500,
        "max_ratio": 2.0,
        "max_positive_gap_by_bucket": {"small": 500},
    }
    changed_request = _request(
        state={"token_usage": previous},
        tools=[{"name": "second", "description": "second tool"}],
    )

    estimate = estimate_model_request(changed_request, token_counter=token_counter)

    assert estimate.source == "calibrated_estimate"
    assert estimate.calibration_samples == 2
    assert estimate.max_positive_gap_by_bucket == {"small": 500}
    assert estimate.admission == estimate.fallback + 500


@pytest.mark.asyncio
async def test_private_summary_is_reported_as_an_estimated_component() -> None:
    middleware = TokenUsageMiddleware()
    request = _request(
        state={
            "messages": [HumanMessage(content="raw history")],
            "_active_context_summary": "conversation summary",
            "_base_system_message": SystemMessage(content="system prompt"),
        }
    )
    request.system_message = SystemMessage(
        content="system prompt\n<private_conversation_context>conversation summary</private_conversation_context>"
    )

    async def handler(_request):
        return ModelResponse(result=[AIMessage(content="answer")])

    result = await middleware.awrap_model_call(request, handler)
    token_usage = result.command.update["token_usage"]

    assert token_usage["summary_active"] is True
    assert token_usage["breakdown_estimate"]["private_summary"] > 0
    assert token_usage["input_source"] == "fallback_estimate"


@pytest.mark.asyncio
async def test_externalized_tool_results_are_reported_separately_from_summary() -> None:
    middleware = TokenUsageMiddleware()
    request = _request()
    request.messages.append(
        ToolMessage(
            content="工具结果已收纳到线程文件",
            tool_call_id="tool-1",
            additional_kwargs={_TOOL_RESULT_SAVED_MARKER: True},
        )
    )

    async def handler(_request):
        return ModelResponse(result=[AIMessage(content="answer")])

    result = await middleware.awrap_model_call(request, handler)
    token_usage = result.command.update["token_usage"]

    assert token_usage["tool_results_externalized"] == 1
    assert token_usage["summary_active"] is False


@pytest.mark.asyncio
async def test_empty_length_response_preserves_usage_on_context_overflow() -> None:
    middleware = TokenUsageMiddleware()
    request = _request()
    request.model = SimpleNamespace(
        model_name="qwen",
        profile={
            "max_input_tokens": 32_768,
            "min_output_reserve_tokens": 4_096,
            "context_safety_tokens": 512,
        },
    )

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    response_metadata={"finish_reason": "length"},
                    usage_metadata={
                        "input_tokens": 32_601,
                        "output_tokens": 167,
                        "total_tokens": 32_768,
                    },
                )
            ]
        )

    with pytest.raises(ContextWindowExceededError, match="上下文上限") as raised:
        await middleware.awrap_model_call(request, handler)

    assert raised.value.token_usage["provider_input_tokens"] == 32_601
    assert raised.value.token_usage["provider_output_tokens"] == 167
    assert raised.value.token_usage["near_context_limit"] is True
    assert raised.value.token_usage["calibration_samples"] == 1


@pytest.mark.asyncio
async def test_current_length_response_with_visible_content_is_not_input_overflow() -> None:
    """P0 基线：旧 Token 中间件只将空正文 length 视为容量异常。"""
    middleware = TokenUsageMiddleware()
    request = _request()

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="回答在输出上限处截断",
                    response_metadata={"finish_reason": "length"},
                    usage_metadata={"input_tokens": 100, "output_tokens": 500, "total_tokens": 600},
                )
            ]
        )

    result = await middleware.awrap_model_call(request, handler)

    assert isinstance(result, ExtendedModelResponse)
    assert result.command.update["token_usage"]["provider_input_tokens"] == 100
    assert result.command.update["token_usage"]["response_outcome"] == "output_exhausted"


@pytest.mark.asyncio
async def test_empty_length_with_output_usage_is_explicit_output_exhaustion() -> None:
    middleware = TokenUsageMiddleware()
    request = _request()

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    response_metadata={"finish_reason": "length"},
                    usage_metadata={"input_tokens": 100, "output_tokens": 500, "total_tokens": 600},
                )
            ]
        )

    with pytest.raises(ModelOutputIncompleteError, match="耗尽输出预算") as raised:
        await middleware.awrap_model_call(request, handler)

    assert raised.value.token_usage["response_outcome"] == "output_exhausted"


@pytest.mark.asyncio
async def test_length_with_tool_call_is_rejected_before_tool_node() -> None:
    middleware = TokenUsageMiddleware()
    request = _request()

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[{"id": "call-1", "name": "search_docs", "args": {"query": "partial"}}],
                    response_metadata={"finish_reason": "length"},
                    usage_metadata={"input_tokens": 100, "output_tokens": 500, "total_tokens": 600},
                )
            ]
        )

    with pytest.raises(ModelOutputIncompleteError, match="未执行工具") as raised:
        await middleware.awrap_model_call(request, handler)

    assert raised.value.token_usage["response_outcome"] == "tool_call_truncated"


@pytest.mark.asyncio
async def test_empty_length_without_usage_is_explicitly_unverified() -> None:
    middleware = TokenUsageMiddleware()
    request = _request()

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    response_metadata={"finish_reason": "length"},
                )
            ]
        )

    with pytest.raises(ModelOutputIncompleteError, match="未返回可校验的 usage") as raised:
        await middleware.awrap_model_call(request, handler)

    assert raised.value.token_usage["response_outcome"] == "length_unverified"
    assert raised.value.token_usage["provider_input_tokens"] is None
    assert raised.value.token_usage["provider_output_tokens"] is None


def test_bucketed_gap_does_not_cross_request_size_boundaries() -> None:
    def token_counter(_messages, **_kwargs):
        return 20_000

    request = _request(
        state={
            "token_usage": {
                "calibration_key": estimate_model_request(_request(), token_counter=token_counter).calibration_key,
                "calibration_samples": 1,
                "max_positive_gap_by_bucket": {"small": 4_000},
            }
        }
    )

    estimate = estimate_model_request(request, token_counter=token_counter)

    assert estimate.request_size_bucket == "medium"
    assert estimate.source == "fallback_estimate"
    assert estimate.admission == estimate.fallback


@pytest.mark.asyncio
async def test_invalid_provider_usage_does_not_create_a_calibration_sample() -> None:
    middleware = TokenUsageMiddleware()
    request = _request()

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="answer",
                    usage_metadata={"input_tokens": 100, "output_tokens": 100, "total_tokens": 10},
                )
            ]
        )

    result = await middleware.awrap_model_call(request, handler)
    snapshot = result.command.update["token_usage"]

    assert snapshot["provider_input_tokens"] is None
    assert snapshot["calibration_samples"] == 0
    assert snapshot["max_positive_gap_by_bucket"] == {}


def test_resolve_context_budget_preserves_minimum_reserve_for_smaller_explicit_output_limit() -> None:
    request = SimpleNamespace(
        model=SimpleNamespace(
            profile={"max_input_tokens": 32_768, "min_output_reserve_tokens": 4_096, "context_safety_tokens": 512}
        ),
        model_settings={"max_tokens": 1_024},
    )

    budget = resolve_context_budget(request)

    assert budget.context_window == 32_768
    assert budget.min_output_reserve_tokens == 4_096
    assert budget.effective_output_reserve == 4_096
    assert budget.context_safety_tokens == 512
    assert budget.prompt_budget == 28_160


def test_resolve_context_budget_expands_reserve_for_larger_explicit_output_limit() -> None:
    request = SimpleNamespace(
        model=SimpleNamespace(
            profile={"max_input_tokens": 32_768, "min_output_reserve_tokens": 4_096, "context_safety_tokens": 512}
        ),
        model_settings={"max_tokens": 8_192},
    )

    budget = resolve_context_budget(request)

    assert budget.effective_output_reserve == 8_192
    assert budget.prompt_budget == 24_064


def test_resolve_context_budget_uses_deployment_default_output_reserve() -> None:
    request = SimpleNamespace(model=SimpleNamespace(profile={"max_input_tokens": 32_768}), model_settings={})

    budget = resolve_context_budget(request)

    assert budget.min_output_reserve_tokens == 4_096
    assert budget.prompt_budget == 27_648
