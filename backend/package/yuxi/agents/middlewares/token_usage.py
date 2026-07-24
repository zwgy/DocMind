"""Token usage observation and calibrated context admission for Yuxi agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
from typing import Any, Literal, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage, convert_to_openai_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.types import Command

from yuxi.config import config as system_config
from yuxi.agents.backends.composite import _TOOL_RESULT_SAVED_MARKER

_REQUEST_PROTOCOL_VERSION = "langchain-openai-messages-v1"
ACTIVE_CONTEXT_SUMMARY_STATE_KEY = "_active_context_summary"
BASE_SYSTEM_MESSAGE_STATE_KEY = "_base_system_message"


class ContextBudgetConfigurationError(ValueError):
    """模型限制不完整或互相冲突时，在发送请求前阻止不可恢复的调用。"""


class ContextWindowExceededError(ContextOverflowError):
    """携带本次 usage 快照，让外层摘要中间件可以按实测误差恢复。"""

    def __init__(self, message: str, token_usage: TokenUsagePayload) -> None:
        super().__init__(message)
        self.token_usage = token_usage


@dataclass(frozen=True)
class ResolvedContextBudget:
    """一次模型调用使用的标准化上下文预算。"""

    context_window: int
    min_output_reserve_tokens: int
    effective_output_reserve: int
    context_safety_tokens: int
    prompt_budget: int
    counter: str = "calibrated_approximate"


class TokenBreakdown(TypedDict):
    """仅用于解释输入构成的本地近似值。"""

    messages: int
    private_summary: int
    system: int
    tools: int


class TokenUsagePayload(TypedDict, total=False):
    """存入 LangGraph state 的单一 Token 使用口径。"""

    input_tokens: int
    input_source: Literal["provider_usage", "calibrated_estimate", "fallback_estimate"]
    provider_input_tokens: int | None
    provider_output_tokens: int | None
    baseline_input_tokens: int
    fallback_input_tokens: int
    estimated_input_tokens: int
    breakdown_estimate: TokenBreakdown
    protocol_correction_tokens: int | None
    max_positive_error: int
    max_ratio: float
    calibration_samples: int
    calibration_key: str
    context_window: int
    context_usage_ratio: float
    min_output_reserve_tokens: int
    effective_output_reserve: int
    context_safety_tokens: int
    prompt_budget: int
    input_budget_delta: int
    context_remaining_after_input: int
    tool_count: int
    tool_results_externalized: int
    summary_active: bool
    near_context_limit: bool
    measured_at: str


class TokenUsageState(AgentState):
    """Agent state extension with the latest token usage snapshot."""

    token_usage: NotRequired[TokenUsagePayload]


@dataclass(frozen=True)
class RequestTokenEstimate:
    """一次最终模型请求的本地估算与当前校准包络。"""

    baseline: int
    fallback: int
    admission: int
    source: Literal["calibrated_estimate", "fallback_estimate"]
    breakdown: TokenBreakdown
    calibration_key: str
    max_positive_error: int
    max_ratio: float
    calibration_samples: int


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
    value = _safe_int(profile.get(field_name))
    return value if (value or 0) > 0 else None


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
    min_output_reserve_tokens = _profile_positive_int(model, "min_output_reserve_tokens")
    if min_output_reserve_tokens is None:
        min_output_reserve_tokens = int(system_config.min_output_reserve_tokens)
    context_safety_tokens = _profile_positive_int(model, "context_safety_tokens")
    if context_safety_tokens is None:
        context_safety_tokens = int(system_config.context_safety_tokens)

    if context_window is None:
        model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
        raise ContextBudgetConfigurationError(f"模型 {model_name} 缺少上下文预算配置: context_window")

    explicit_output_limit = _request_output_limit(getattr(request, "model_settings", None))
    effective_output_reserve = max(min_output_reserve_tokens, explicit_output_limit or 0)
    prompt_budget = context_window - effective_output_reserve - context_safety_tokens
    if prompt_budget <= 0:
        model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
        raise ContextBudgetConfigurationError(
            f"模型 {model_name} 的 context_window 必须大于 min_output_reserve_tokens 与 context_safety_tokens 之和"
        )
    return ResolvedContextBudget(
        context_window=context_window,
        min_output_reserve_tokens=min_output_reserve_tokens,
        effective_output_reserve=effective_output_reserve,
        context_safety_tokens=context_safety_tokens,
        prompt_budget=prompt_budget,
    )


def _without_inline_images(value: Any) -> Any:
    """序列化估算不能把图片 Base64 字节误当作文本 Token。"""
    if isinstance(value, list):
        return [_without_inline_images(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("type") in {"image", "image_url"}:
        return {"type": value.get("type"), "image": "<inline-image>"}
    return {key: _without_inline_images(item) for key, item in value.items()}


def _openai_tools(tools: Iterable[Any]) -> list[dict[str, Any]]:
    return [convert_to_openai_tool(tool) for tool in tools]


def _serialized_request(messages: list[AnyMessage], tools: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    converted_tools = _openai_tools(tools)
    payload = {
        "messages": _without_inline_images(convert_to_openai_messages(messages)),
        "tools": converted_tools,
    }
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str),
        converted_tools,
    )


def estimate_messages_tokens(
    messages: Iterable[Any],
    *,
    tools: list[Any] | None = None,
    token_counter: Callable[..., int] = count_tokens_approximately,
) -> int:
    """对任意消息片段做无网络、Unicode/JSON 感知的保守估算。"""
    message_list = list(messages)
    tool_list = list(tools or [])
    baseline = int(token_counter(message_list, tools=tool_list))
    serialized, _ = _serialized_request(message_list, tool_list)
    ascii_chars = sum(character.isascii() for character in serialized)
    non_ascii_chars = len(serialized) - ascii_chars
    # 英文按四字符估算会严重低估中文和 JSON 协议；这里取三种启发式的最大值，
    # 只用于安全准入，不把它伪装成模型 tokenizer 的精确结果。
    unicode_estimate = math.ceil(ascii_chars / 4 + non_ascii_chars)
    serialization_estimate = math.ceil(len(serialized) / 2)
    return max(baseline, unicode_estimate, serialization_estimate)


def _calibration_key(request: ModelRequest, converted_tools: list[dict[str, Any]]) -> str:
    model = request.model
    model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
    base_url = getattr(model, "openai_api_base", None) or getattr(model, "base_url", None) or ""
    descriptor = {
        "model": str(model_name),
        "model_type": f"{type(model).__module__}.{type(model).__qualname__}",
        "base_url": str(base_url).rstrip("/"),
        "protocol": _REQUEST_PROTOCOL_VERSION,
        "tools": converted_tools,
    }
    serialized = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _breakdown(
    request: ModelRequest,
    token_counter: Callable[..., int],
) -> TokenBreakdown:
    state = request.state
    private_summary = str(state.get(ACTIVE_CONTEXT_SUMMARY_STATE_KEY) or "")
    base_system_message = state.get(BASE_SYSTEM_MESSAGE_STATE_KEY)
    system_messages = [base_system_message] if isinstance(base_system_message, SystemMessage) else []
    if not system_messages and not private_summary and request.system_message is not None:
        system_messages = [request.system_message]
    tools = list(request.tools or [])
    return {
        "messages": int(token_counter(list(request.messages or []))),
        "private_summary": (int(token_counter([SystemMessage(content=private_summary)])) if private_summary else 0),
        "system": int(token_counter(system_messages)) if system_messages else 0,
        "tools": int(token_counter([], tools=tools)) if tools else 0,
    }


def _externalized_tool_result_count(messages: Iterable[AnyMessage]) -> int:
    """只统计已持久化并以回执替代正文的工具结果，避免把正常工具调用误报为收纳。"""
    return sum(
        isinstance(message, ToolMessage) and bool(message.additional_kwargs.get(_TOOL_RESULT_SAVED_MARKER))
        for message in messages
    )


def estimate_model_request(
    request: ModelRequest,
    *,
    token_counter: Callable[..., int] = count_tokens_approximately,
) -> RequestTokenEstimate:
    """估算最终请求，并应用同一会话、同一请求结构的历史 usage 校准。"""
    messages = [
        *([request.system_message] if request.system_message is not None else []),
        *list(request.messages or []),
    ]
    tools = list(request.tools or [])
    baseline = int(token_counter(messages, tools=tools))
    serialized, converted_tools = _serialized_request(messages, tools)
    ascii_chars = sum(character.isascii() for character in serialized)
    non_ascii_chars = len(serialized) - ascii_chars
    fallback = max(
        baseline,
        math.ceil(ascii_chars / 4 + non_ascii_chars),
        math.ceil(len(serialized) / 2),
    )
    calibration_key = _calibration_key(request, converted_tools)
    previous = request.state.get("token_usage")
    valid_previous = (
        isinstance(previous, Mapping)
        and previous.get("calibration_key") == calibration_key
        and (_safe_int(previous.get("calibration_samples")) or 0) > 0
    )
    max_positive_error = max(_safe_int(previous.get("max_positive_error")) or 0, 0) if valid_previous else 0
    previous_ratio = previous.get("max_ratio") if valid_previous else None
    max_ratio = float(previous_ratio) if isinstance(previous_ratio, int | float) else 1.0
    max_ratio = max(max_ratio, 1.0)
    calibration_samples = _safe_int(previous.get("calibration_samples")) or 0 if valid_previous else 0
    # 绝对误差保护较固定的模板/工具开销，倍率保护随正文增长的分词误差；取最大值
    # 可以避免一次较小的新样本把已经观察到的高风险低估重新放行。
    admission = max(
        fallback,
        baseline + max_positive_error,
        math.ceil(baseline * max_ratio),
    )
    return RequestTokenEstimate(
        baseline=baseline,
        fallback=fallback,
        admission=admission,
        source="calibrated_estimate" if valid_previous else "fallback_estimate",
        breakdown=_breakdown(request, token_counter),
        calibration_key=calibration_key,
        max_positive_error=max_positive_error,
        max_ratio=max_ratio,
        calibration_samples=calibration_samples,
    )


def _model_usage_from_response(response: ModelResponse) -> tuple[int | None, int | None]:
    for message in reversed(response.result):
        if not isinstance(message, AIMessage) or not isinstance(message.usage_metadata, Mapping):
            continue
        input_tokens = _safe_int(message.usage_metadata.get("input_tokens"))
        output_tokens = _safe_int(message.usage_metadata.get("output_tokens"))
        total_tokens = _safe_int(message.usage_metadata.get("total_tokens"))
        if (input_tokens or 0) <= 0:
            return None, output_tokens if (output_tokens or 0) >= 0 else None
        if total_tokens is not None and output_tokens is not None and total_tokens < input_tokens + output_tokens:
            return None, None
        return input_tokens, output_tokens if (output_tokens or 0) >= 0 else None
    return None, None


def _finish_reason(response: ModelResponse) -> str | None:
    for message in reversed(response.result):
        if isinstance(message, AIMessage):
            value = message.response_metadata.get("finish_reason")
            return value if isinstance(value, str) else None
    return None


def _is_empty_length_response(response: ModelResponse) -> bool:
    for message in reversed(response.result):
        if not isinstance(message, AIMessage):
            continue
        return (
            message.response_metadata.get("finish_reason") == "length"
            and not message.content
            and not message.tool_calls
        )
    return False


class TokenUsageMiddleware(AgentMiddleware[TokenUsageState]):
    """Record provider usage and calibrate the next request without a preflight network call."""

    state_schema = TokenUsageState

    def __init__(self, token_counter: Callable[..., int] = count_tokens_approximately) -> None:
        super().__init__()
        self.token_counter = token_counter

    def _build_snapshot(self, request: ModelRequest, response: ModelResponse) -> TokenUsagePayload:
        estimate = estimate_model_request(request, token_counter=self.token_counter)
        provider_input, provider_output = _model_usage_from_response(response)
        max_positive_error = estimate.max_positive_error
        max_ratio = estimate.max_ratio
        calibration_samples = estimate.calibration_samples
        if provider_input is not None and estimate.baseline > 0:
            max_positive_error = max(max_positive_error, provider_input - estimate.baseline, 0)
            max_ratio = max(max_ratio, provider_input / estimate.baseline, 1.0)
            calibration_samples += 1

        input_tokens = provider_input if provider_input is not None else estimate.admission
        input_source: Literal["provider_usage", "calibrated_estimate", "fallback_estimate"] = (
            "provider_usage" if provider_input is not None else estimate.source
        )
        budget = resolve_context_budget(request)
        estimated_input_tokens = sum(estimate.breakdown.values())
        near_context_limit = (
            _finish_reason(response) == "length"
            and provider_input is not None
            and provider_output is not None
            and provider_input + provider_output >= budget.context_window - budget.context_safety_tokens
        )
        return {
            "input_tokens": input_tokens,
            "input_source": input_source,
            "provider_input_tokens": provider_input,
            "provider_output_tokens": provider_output,
            "baseline_input_tokens": estimate.baseline,
            "fallback_input_tokens": estimate.fallback,
            "estimated_input_tokens": estimated_input_tokens,
            "breakdown_estimate": estimate.breakdown,
            "protocol_correction_tokens": (
                provider_input - estimated_input_tokens if provider_input is not None else None
            ),
            "max_positive_error": max_positive_error,
            "max_ratio": round(max_ratio, 6),
            "calibration_samples": calibration_samples,
            "calibration_key": estimate.calibration_key,
            "context_window": budget.context_window,
            "context_usage_ratio": round(input_tokens / budget.context_window, 4),
            "min_output_reserve_tokens": budget.min_output_reserve_tokens,
            "effective_output_reserve": budget.effective_output_reserve,
            "context_safety_tokens": budget.context_safety_tokens,
            "prompt_budget": budget.prompt_budget,
            "input_budget_delta": budget.prompt_budget - input_tokens,
            "context_remaining_after_input": budget.context_window - input_tokens,
            "tool_count": len(request.tools or []),
            "tool_results_externalized": _externalized_tool_result_count(request.messages or []),
            "summary_active": bool(request.state.get(ACTIVE_CONTEXT_SUMMARY_STATE_KEY)),
            "near_context_limit": near_context_limit,
            "measured_at": datetime.now(UTC).isoformat(),
        }

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ExtendedModelResponse:
        resolve_context_budget(request)
        response = handler(request)
        # 必须先生成快照再判断空正文 length，否则本次真实 usage 会随异常一起丢失。
        snapshot = self._build_snapshot(request, response)
        if _is_empty_length_response(response):
            raise ContextWindowExceededError(
                "模型在生成可见正文前已达到上下文上限",
                snapshot,
            )
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"token_usage": snapshot}),
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ExtendedModelResponse:
        resolve_context_budget(request)
        response = await handler(request)
        # 异步链路与同步链路保持相同顺序，恢复逻辑才能使用同一份实测校准。
        snapshot = self._build_snapshot(request, response)
        if _is_empty_length_response(response):
            raise ContextWindowExceededError(
                "模型在生成可见正文前已达到上下文上限",
                snapshot,
            )
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"token_usage": snapshot}),
        )
