"""Token usage observation middleware for Yuxi agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command

from yuxi.config import config as system_config


class ContextBudgetConfigurationError(ValueError):
    """模型限制不完整或互相冲突时，在发送请求前阻止不可恢复的调用。"""


@dataclass(frozen=True)
class ResolvedContextBudget:
    """一次模型调用使用的标准化上下文预算。"""

    context_window: int
    max_completion_tokens: int
    effective_output_reserve: int
    context_safety_tokens: int
    prompt_budget: int
    counter: str = "approximate"


class TokenUsagePayload(TypedDict, total=False):
    """Serializable token usage snapshot stored in LangGraph state."""

    state_message_count: int
    state_message_count_before_call: int
    state_messages_tokens: int
    state_messages_tokens_before_call: int
    llm_message_count: int
    llm_messages_tokens: int
    llm_input_tokens: int
    system_tokens: int
    tools_tokens: int
    tool_count: int
    context_window: int | None
    context_usage_ratio: float | None
    max_completion_tokens: int | None
    context_safety_tokens: int | None
    prompt_budget: int | None
    prompt_tokens: int
    fixed_overhead_tokens: int
    working_messages_tokens: int
    remaining_input_tokens: int | None
    prompt_deficit_tokens: int
    summary_active: bool
    summary_message_tokens: int
    model_usage: dict[str, int]
    counter: str
    estimate: bool
    measured_at: str


class TokenUsageState(AgentState):
    """Agent state extension with the latest token usage snapshot."""

    token_usage: NotRequired[TokenUsagePayload]


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _profile_positive_int(model: Any, field_name: str) -> int | None:
    profile = getattr(model, "profile", None)
    if not isinstance(profile, Mapping):
        return None
    return _safe_int(profile.get(field_name)) if (_safe_int(profile.get(field_name)) or 0) > 0 else None


def _request_output_limit(model_settings: Any) -> int | None:
    if not isinstance(model_settings, Mapping):
        return None
    for field_name in ("max_completion_tokens", "max_tokens"):
        value = model_settings.get(field_name)
        if value is None:
            continue
        normalized = _safe_int(value)
        if normalized is None or normalized <= 0:
            raise ContextBudgetConfigurationError(f"本次调用的 {field_name} 必须是正整数")
        return normalized
    return None


def resolve_context_budget(request: ModelRequest | Any) -> ResolvedContextBudget:
    """解析完整窗口、输出预留和缓冲，作为所有调用前预算判断的唯一入口。"""
    model = getattr(request, "model", None)
    context_window = _profile_positive_int(model, "max_input_tokens")
    max_completion_tokens = _profile_positive_int(model, "max_output_tokens")
    context_safety_tokens = _profile_positive_int(model, "context_safety_tokens")
    if context_safety_tokens is None:
        context_safety_tokens = int(system_config.context_safety_tokens)

    missing = [
        field_name
        for field_name, value in (
            ("context_window", context_window),
            ("max_completion_tokens", max_completion_tokens),
        )
        if value is None
    ]
    if missing:
        model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
        raise ContextBudgetConfigurationError(
            f"模型 {model_name} 缺少上下文预算配置: {', '.join(missing)}"
        )

    explicit_output_limit = _request_output_limit(getattr(request, "model_settings", None))
    effective_output_reserve = (
        min(explicit_output_limit, max_completion_tokens) if explicit_output_limit else max_completion_tokens
    )
    prompt_budget = context_window - effective_output_reserve - context_safety_tokens
    if prompt_budget <= 0:
        model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
        raise ContextBudgetConfigurationError(
            f"模型 {model_name} 的 context_window 必须大于 max_completion_tokens 与 context_safety_tokens 之和"
        )
    return ResolvedContextBudget(
        context_window=context_window,
        max_completion_tokens=max_completion_tokens,
        effective_output_reserve=effective_output_reserve,
        context_safety_tokens=context_safety_tokens,
        prompt_budget=prompt_budget,
    )


def _raise_on_empty_length_response(response: ModelResponse) -> None:
    for message in reversed(response.result):
        if not isinstance(message, AIMessage):
            continue
        if (
            message.response_metadata.get("finish_reason") == "length"
            and not message.content
            and not message.tool_calls
        ):
            # OpenAI 兼容服务可能用空响应表示上下文耗尽；转成标准异常后，现有摘要中间件会压缩并重试一次。
            raise ContextOverflowError("模型在生成可见正文前已达到上下文上限")
        return


def _is_summary_message(message: AnyMessage) -> bool:
    return getattr(message, "additional_kwargs", {}).get("lc_source") == "summarization"


def _model_usage_from_response(response: ModelResponse) -> dict[str, int]:
    for message in reversed(response.result):
        if not isinstance(message, AIMessage):
            continue
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, Mapping):
            continue
        return {str(key): value for key, value in usage.items() if isinstance(value, int)}
    return {}


class TokenUsageMiddleware(AgentMiddleware[TokenUsageState]):
    """Record approximate context token usage for the current model request."""

    state_schema = TokenUsageState

    def __init__(self, token_counter=count_tokens_approximately) -> None:
        super().__init__()
        self.token_counter = token_counter

    def _count_tokens(self, messages: Iterable[Any], *, tools: list[Any] | None = None) -> int:
        message_list = list(messages)
        if tools is not None:
            return int(self.token_counter(message_list, tools=tools))
        return int(self.token_counter(message_list))

    def _build_snapshot(self, request: ModelRequest, response: ModelResponse) -> TokenUsagePayload:
        _raise_on_empty_length_response(response)
        state_messages = list(request.state.get("messages") or [])
        llm_messages = list(request.messages or [])
        system_messages = [request.system_message] if request.system_message is not None else []
        tools = list(request.tools or [])
        response_messages = list(response.result or [])

        state_tokens_before_call = self._count_tokens(state_messages)
        next_state_messages = [*state_messages, *response_messages]
        state_messages_tokens = self._count_tokens(next_state_messages)
        llm_messages_tokens = self._count_tokens(llm_messages)
        system_tokens = self._count_tokens(system_messages)
        tools_tokens = self._count_tokens([], tools=tools) if tools else 0
        llm_input_tokens = self._count_tokens([*system_messages, *llm_messages], tools=tools)

        budget = resolve_context_budget(request)
        context_usage_ratio = min(1.0, round(llm_input_tokens / budget.prompt_budget, 4))

        summary_message = llm_messages[0] if llm_messages and _is_summary_message(llm_messages[0]) else None
        return {
            "state_message_count": len(next_state_messages),
            "state_message_count_before_call": len(state_messages),
            "state_messages_tokens": state_messages_tokens,
            "state_messages_tokens_before_call": state_tokens_before_call,
            "llm_message_count": len(llm_messages),
            "llm_messages_tokens": llm_messages_tokens,
            "llm_input_tokens": llm_input_tokens,
            "system_tokens": system_tokens,
            "tools_tokens": tools_tokens,
            "tool_count": len(tools),
            "context_window": budget.context_window,
            "context_usage_ratio": context_usage_ratio,
            "max_completion_tokens": budget.max_completion_tokens,
            "context_safety_tokens": budget.context_safety_tokens,
            "prompt_budget": budget.prompt_budget,
            "prompt_tokens": llm_input_tokens,
            "fixed_overhead_tokens": system_tokens + tools_tokens,
            "working_messages_tokens": llm_messages_tokens,
            "remaining_input_tokens": max(budget.prompt_budget - llm_input_tokens, 0),
            "prompt_deficit_tokens": max(llm_input_tokens - budget.prompt_budget, 0),
            "summary_active": summary_message is not None,
            "summary_message_tokens": self._count_tokens([summary_message]) if summary_message else 0,
            "model_usage": _model_usage_from_response(response),
            "counter": "langchain.count_tokens_approximately",
            "estimate": True,
            "measured_at": datetime.now(UTC).isoformat(),
        }

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ExtendedModelResponse:
        resolve_context_budget(request)
        response = handler(request)
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"token_usage": self._build_snapshot(request, response)}),
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ExtendedModelResponse:
        resolve_context_budget(request)
        response = await handler(request)
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"token_usage": self._build_snapshot(request, response)}),
        )
