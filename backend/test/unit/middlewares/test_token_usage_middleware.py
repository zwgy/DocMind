from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from yuxi.agents.middlewares.token_usage import (
    ContextWindowExceededError,
    TokenUsageMiddleware,
    estimate_model_request,
    resolve_context_budget,
)


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
    assert token_usage["context_window"] == 2_000
    assert token_usage["prompt_budget"] == 1_400
    assert token_usage["input_budget_delta"] == 1_388
    assert token_usage["context_remaining_after_input"] == 1_988
    assert token_usage["calibration_samples"] == 1


@pytest.mark.asyncio
async def test_usage_calibrates_the_next_request_with_maximum_error_and_ratio() -> None:
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
    assert estimate.source == "calibrated_estimate"
    assert estimate.admission >= 32_601


def test_tool_schema_change_resets_the_conversation_calibration() -> None:
    def token_counter(_messages, **_kwargs):
        return 100

    first_request = _request(tools=[{"name": "first", "description": "first tool"}])
    first_estimate = estimate_model_request(first_request, token_counter=token_counter)
    previous = {
        "calibration_key": first_estimate.calibration_key,
        "calibration_samples": 2,
        "max_positive_error": 500,
        "max_ratio": 2.0,
    }
    changed_request = _request(
        state={"token_usage": previous},
        tools=[{"name": "second", "description": "second tool"}],
    )

    estimate = estimate_model_request(changed_request, token_counter=token_counter)

    assert estimate.source == "fallback_estimate"
    assert estimate.calibration_samples == 0
    assert estimate.max_positive_error == 0
    assert estimate.max_ratio == 1.0


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
