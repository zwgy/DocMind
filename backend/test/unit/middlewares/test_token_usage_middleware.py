from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from yuxi.agents.middlewares.token_usage import (
    ContextBudgetConfigurationError,
    TokenUsageMiddleware,
    resolve_context_budget,
)


@pytest.mark.asyncio
async def test_token_usage_middleware_records_request_and_state_tokens() -> None:
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
    request = SimpleNamespace(
        model=SimpleNamespace(
            profile={"max_input_tokens": 2000, "max_output_tokens": 500, "context_safety_tokens": 100}
        ),
        state={"messages": [HumanMessage(content="old message")]},
        messages=[HumanMessage(content="current message")],
        system_message=SystemMessage(content="system prompt"),
        tools=[tool_schema],
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

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
    assert token_usage["state_message_count"] == 2
    assert token_usage["state_message_count_before_call"] == 1
    assert token_usage["llm_message_count"] == 1
    assert token_usage["state_messages_tokens"] >= token_usage["state_messages_tokens_before_call"]
    assert token_usage["llm_input_tokens"] >= token_usage["llm_messages_tokens"]
    assert token_usage["system_tokens"] > 0
    assert token_usage["tools_tokens"] > 0
    assert token_usage["tool_count"] == 1
    assert token_usage["context_window"] == 2000
    assert token_usage["prompt_budget"] == 1400
    assert token_usage["max_completion_tokens"] == 500
    assert token_usage["context_safety_tokens"] == 100
    assert token_usage["remaining_input_tokens"] == 1400 - token_usage["prompt_tokens"]
    assert token_usage["prompt_deficit_tokens"] == 0
    assert "summary_trigger_tokens" not in token_usage
    assert token_usage["model_usage"] == {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
    assert token_usage["estimate"] is True


@pytest.mark.asyncio
async def test_token_usage_middleware_detects_effective_summary_message() -> None:
    middleware = TokenUsageMiddleware()
    summary_message = HumanMessage(
        content="conversation summary",
        additional_kwargs={"lc_source": "summarization"},
    )
    request = SimpleNamespace(
        model=SimpleNamespace(
            profile={"max_input_tokens": 2000, "max_output_tokens": 500, "context_safety_tokens": 100}
        ),
        state={"messages": [HumanMessage(content="raw history")]},
        messages=[summary_message, HumanMessage(content="recent user turn")],
        system_message=None,
        tools=[],
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    async def handler(_request):
        return ModelResponse(result=[AIMessage(content="answer")])

    result = await middleware.awrap_model_call(request, handler)
    token_usage = result.command.update["token_usage"]

    assert token_usage["summary_active"] is True
    assert token_usage["summary_message_tokens"] > 0
    assert token_usage["context_window"] == 2000
    assert token_usage["context_usage_ratio"] is not None


@pytest.mark.asyncio
async def test_token_usage_middleware_turns_empty_length_response_into_context_overflow() -> None:
    middleware = TokenUsageMiddleware()
    request = SimpleNamespace(
        model=SimpleNamespace(
            profile={"max_input_tokens": 32768, "max_output_tokens": 4096, "context_safety_tokens": 512}
        ),
        state={"messages": [HumanMessage(content="读取附件原文")]},
        messages=[HumanMessage(content="读取附件原文")],
        system_message=None,
        tools=[],
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    async def handler(_request):
        return ModelResponse(result=[AIMessage(content="", response_metadata={"finish_reason": "length"})])

    with pytest.raises(ContextOverflowError, match="上下文上限"):
        await middleware.awrap_model_call(request, handler)


def test_resolve_context_budget_uses_explicit_output_limit_without_exceeding_model_limit() -> None:
    request = SimpleNamespace(
        model=SimpleNamespace(
            profile={"max_input_tokens": 32768, "max_output_tokens": 4096, "context_safety_tokens": 512}
        ),
        model_settings={"max_tokens": 1024},
    )

    budget = resolve_context_budget(request)

    assert budget.context_window == 32768
    assert budget.max_completion_tokens == 4096
    assert budget.effective_output_reserve == 1024
    assert budget.context_safety_tokens == 512
    assert budget.prompt_budget == 31232


def test_resolve_context_budget_rejects_missing_model_output_limit() -> None:
    request = SimpleNamespace(model=SimpleNamespace(profile={"max_input_tokens": 32768}), model_settings={})

    with pytest.raises(ContextBudgetConfigurationError, match="max_completion_tokens"):
        resolve_context_budget(request)
