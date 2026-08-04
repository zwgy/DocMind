"""Token usage observation and calibrated context admission for Yuxi agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
from types import SimpleNamespace
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
_REQUEST_TEMPLATE_VERSION = "yuxi-openai-compatible-template-v1"
ACTIVE_CONTEXT_SUMMARY_STATE_KEY = "_active_context_summary"
BASE_SYSTEM_MESSAGE_STATE_KEY = "_base_system_message"
_RESOLVED_TOOL_TOKEN_LIMIT_ATTR = "_resolved_tool_token_limit_tokens"
_TOOL_TOKEN_LIMIT_DIVISOR = 16
_MIN_TOOL_TOKEN_LIMIT = 3 * 1_024
_MAX_TOOL_TOKEN_LIMIT = 16 * 1_024
_CALIBRATION_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("small", 8_000),
    ("medium", 32_000),
    ("large", 128_000),
    ("xlarge", None),
)
_MINIMUM_CURRENT_INPUT_RECEIPT = "Current user input is retained outside the active context."


class ContextBudgetConfigurationError(ValueError):
    """模型限制不完整或互相冲突时，在发送请求前阻止不可恢复的调用。"""


class ContextWindowExceededError(ContextOverflowError):
    """携带本次 usage 快照，让外层摘要中间件可以按实测误差恢复。"""

    def __init__(self, message: str, token_usage: TokenUsagePayload) -> None:
        super().__init__(message)
        self.token_usage = token_usage


class ModelOutputIncompleteError(RuntimeError):
    """阻止截断或不可验证的模型输出作为正常 AIMessage 提交。"""

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
    max_positive_gap_by_bucket: dict[str, int]
    calibration_samples: int
    calibration_key: str
    request_size_bucket: str
    tool_schema_hash: str
    system_prompt_hash: str
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
    response_outcome: Literal[
        "completed",
        "input_overflow",
        "output_exhausted",
        "tool_call_truncated",
        "length_unverified",
    ]
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
    max_positive_gap_by_bucket: dict[str, int]
    request_size_bucket: str
    calibration_samples: int


@dataclass(frozen=True)
class FixedContextOverhead:
    """Tokens that history compaction cannot reclaim from a final model request."""

    fixed_overhead: int
    minimum_current_input_receipt: int
    system: int
    tools: int
    protocol_correction_envelope: int
    tool_count: int
    largest_tool_schema_name: str | None
    largest_tool_schema_tokens: int


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
    for field_name in ("max_completion_tokens", "max_tokens", "num_predict"):
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


def resolve_tool_token_limit(context: Any, *, model: Any | None = None) -> int:
    """按模型提示预算解析单个工具结果的统一内联上限。"""
    if model is None:
        cached = _safe_int(getattr(context, _RESOLVED_TOOL_TOKEN_LIMIT_ATTR, None))
        if cached is not None and cached > 0:
            return cached
        # 工具执行阶段不应再次加载模型；主代理和子代理建图时必须先解析并缓存阈值。
        raise ContextBudgetConfigurationError("自动工具内联上限尚未按模型预算解析")

    budget = resolve_context_budget(SimpleNamespace(model=model, model_settings={}))
    # Claude Code 的 microcompact 仍以整体上下文压力和工具调用新旧边界为准，并没有可直接复用的
    # “单结果占窗口比例”。这里保留 DocMind 已验证的 3K 下限，同时让常见的四个近期大结果最多
    # 占约四分之一提示预算；最终请求仍由 L1/L2/L3/L5 的 prompt_budget 门禁兜底。
    limit = min(
        max(budget.prompt_budget // _TOOL_TOKEN_LIMIT_DIVISOR, _MIN_TOOL_TOKEN_LIMIT),
        _MAX_TOOL_TOKEN_LIMIT,
    )
    setattr(context, _RESOLVED_TOOL_TOKEN_LIMIT_ATTR, limit)
    return limit


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


def _stable_hash(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _calibration_key(request: ModelRequest) -> str:
    model = request.model
    model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
    base_url = getattr(model, "openai_api_base", None) or getattr(model, "base_url", None) or ""
    descriptor = {
        "model": str(model_name),
        "model_type": f"{type(model).__module__}.{type(model).__qualname__}",
        "base_url": str(base_url).rstrip("/"),
        "protocol": _REQUEST_PROTOCOL_VERSION,
        "template": _REQUEST_TEMPLATE_VERSION,
    }
    # 工具 schema 和 System 提示词会随着 Skill 激活频繁变化；它们的真实开销已由当前 fallback
    # 计入。若把它们放进 key，会在最需要历史误差保护的时刻清空校准包络。
    return _stable_hash(descriptor)


def _request_size_bucket(tokens: int) -> str:
    for name, upper_bound in _CALIBRATION_BUCKETS:
        if upper_bound is None or tokens <= upper_bound:
            return name
    raise AssertionError("calibration buckets must include an unbounded final bucket")


def _positive_gap_by_bucket(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    # Checkpoint state is persisted as JSON.  Only accept the known non-negative integer values so a
    # malformed provider or an old state cannot make admission unexpectedly less conservative.
    return {
        bucket: gap
        for bucket, upper_bound in _CALIBRATION_BUCKETS
        if (gap := _safe_int(value.get(bucket))) is not None and gap >= 0
    }


def _largest_tool_schema(converted_tools: list[dict[str, Any]]) -> tuple[str | None, int]:
    largest_name: str | None = None
    largest_tokens = 0
    for tool in converted_tools:
        token_count = estimate_messages_tokens([], tools=[tool])
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if token_count > largest_tokens:
            largest_name = str(name) if name else None
            largest_tokens = token_count
    return largest_name, largest_tokens


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


def fixed_context_overhead(request: ModelRequest) -> FixedContextOverhead:
    """Calculate the request material that history compaction cannot reclaim."""
    system_messages = [request.system_message] if request.system_message is not None else []
    tools = list(request.tools or [])
    system_tokens = estimate_messages_tokens(system_messages) if system_messages else 0
    tool_tokens = estimate_messages_tokens([], tools=tools) if tools else 0
    minimum_receipt_tokens = estimate_messages_tokens([SystemMessage(content=_MINIMUM_CURRENT_INPUT_RECEIPT)])
    estimate = estimate_model_request(request)
    fixed_request_size = system_tokens + tool_tokens + minimum_receipt_tokens
    protocol_correction_envelope = estimate.max_positive_gap_by_bucket.get(_request_size_bucket(fixed_request_size), 0)
    _, converted_tools = _serialized_request([], tools)
    largest_name, largest_tokens = _largest_tool_schema(converted_tools)
    return FixedContextOverhead(
        fixed_overhead=system_tokens + tool_tokens + protocol_correction_envelope,
        minimum_current_input_receipt=minimum_receipt_tokens,
        system=system_tokens,
        tools=tool_tokens,
        protocol_correction_envelope=protocol_correction_envelope,
        tool_count=len(tools),
        largest_tool_schema_name=largest_name,
        largest_tool_schema_tokens=largest_tokens,
    )


def ensure_fixed_context_fits(request: ModelRequest) -> None:
    """Reject an impossible deployment before L1/L5 spend time on archive or summary calls."""
    budget = resolve_context_budget(request)
    overhead = fixed_context_overhead(request)
    if overhead.fixed_overhead + overhead.minimum_current_input_receipt <= budget.prompt_budget:
        return
    largest_schema = overhead.largest_tool_schema_name or "<unknown>"
    raise ContextBudgetConfigurationError(
        "固定上下文开销超过可用输入预算："
        f"context_window={budget.context_window}, "
        f"output_reserve={budget.effective_output_reserve}, "
        f"safety_tokens={budget.context_safety_tokens}, "
        f"system_tokens={overhead.system}, tools_tokens={overhead.tools}, "
        f"protocol_correction_tokens={overhead.protocol_correction_envelope}, "
        f"tool_count={overhead.tool_count}, largest_tool_schema={largest_schema}, "
        f"largest_tool_schema_tokens={overhead.largest_tool_schema_tokens}。"
        "请减少同时激活的工具或 Skill，核对部署窗口配置，或改用更大上下文窗口模型。"
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
    calibration_key = _calibration_key(request)
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
    gap_by_bucket = _positive_gap_by_bucket(previous.get("max_positive_gap_by_bucket")) if valid_previous else {}
    request_size_bucket = _request_size_bucket(fallback)
    bucket_gap = gap_by_bucket.get(request_size_bucket, 0)
    # 旧版 ratio 和跨规模 gap 只保留诊断价值；仅同规模桶的最大正误差可以安全参与本次准入。
    admission = fallback + bucket_gap
    return RequestTokenEstimate(
        baseline=baseline,
        fallback=fallback,
        admission=admission,
        source="calibrated_estimate" if bucket_gap > 0 else "fallback_estimate",
        breakdown=_breakdown(request, token_counter),
        calibration_key=calibration_key,
        max_positive_error=max_positive_error,
        max_ratio=max_ratio,
        max_positive_gap_by_bucket=gap_by_bucket,
        request_size_bucket=request_size_bucket,
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


def message_finish_reason(message: AIMessage) -> str | None:
    """标准化 OpenAI 与 Ollama 的完成原因，避免 Provider 差异泄漏到预算策略。"""
    metadata = message.response_metadata
    for field_name in ("finish_reason", "done_reason"):
        value = metadata.get(field_name)
        if isinstance(value, str):
            return value
    return None


def _finish_reason(response: ModelResponse) -> str | None:
    for message in reversed(response.result):
        if isinstance(message, AIMessage):
            return message_finish_reason(message)
    return None


def visible_output_exhaustion(response: ModelResponse) -> tuple[bool, int | None]:
    """识别可安全提交正文的明确输出耗尽，并返回 Provider 输出 usage。

    LangChain 会在 wrap 中间件组合边界解包内层 ``ExtendedModelResponse``，因此外层
    恢复控制器看不到 TokenUsage command。本函数让分类规则仍由 Token 模块统一拥有，
    避免恢复层自行解释截断工具调用或空正文。
    """
    for message in reversed(response.result):
        if not isinstance(message, AIMessage):
            continue
        exhausted = (
            message_finish_reason(message) == "length"
            and bool(message.content)
            and not message.tool_calls
            and not message.invalid_tool_calls
        )
        if not exhausted:
            return False, None
        _input_tokens, output_tokens = _model_usage_from_response(response)
        return True, output_tokens
    return False, None


def _length_response_outcome(response: ModelResponse, snapshot: TokenUsagePayload) -> str | None:
    """Classify `finish_reason=length` without confusing output limits with prompt overflow."""
    visible_exhausted, _output_tokens = visible_output_exhaustion(response)
    if visible_exhausted:
        return "output_exhausted"
    for message in reversed(response.result):
        if not isinstance(message, AIMessage):
            continue
        if message_finish_reason(message) != "length":
            return None
        if message.tool_calls or message.invalid_tool_calls:
            # A length stop while emitting a tool call is unsafe to execute.  P3 will protect the
            # ToolNode boundary; P1 records the distinct outcome instead of trying history compaction.
            return "tool_call_truncated"
        provider_input = snapshot.get("provider_input_tokens")
        provider_output = snapshot.get("provider_output_tokens")
        prompt_budget = snapshot.get("prompt_budget")
        if isinstance(provider_input, int) and isinstance(prompt_budget, int) and provider_input > prompt_budget:
            return "input_overflow"
        if isinstance(provider_output, int) and provider_output > 0:
            # 本地 Qwen 可能把额度消耗在 reasoning token；provider 的正数输出 usage
            # 足以证明输出耗尽，但空正文仍不能作为一次正常回答提交。
            return "output_exhausted"
        # 没有可见正文和输出 usage 时无法区分兼容层丢字段、输出耗尽或其他服务异常。
        # 明确标记不可验证，避免把它伪装成输入溢出后无意义地压缩历史。
        return "length_unverified"
    return None


def _raise_for_noncommittable_output(response: ModelResponse, snapshot: TokenUsagePayload) -> None:
    """在 LangGraph 看到模型结果前阻止不完整输出进入消息或 ToolNode。"""
    outcome = snapshot["response_outcome"]
    if outcome == "input_overflow":
        raise ContextWindowExceededError(
            "模型在生成可见正文前已达到上下文上限",
            snapshot,
        )
    if outcome == "tool_call_truncated":
        raise ModelOutputIncompleteError(
            "模型工具调用在输出上限处截断，未执行工具；请提高模型输出上限后重试",
            snapshot,
        )
    if outcome == "length_unverified":
        raise ModelOutputIncompleteError(
            "模型在输出上限处停止且未返回可校验的 usage，未自动压缩或重试",
            snapshot,
        )
    if outcome == "output_exhausted" and not any(
        isinstance(message, AIMessage) and bool(message.content) for message in response.result
    ):
        raise ModelOutputIncompleteError(
            "模型已耗尽输出预算但未生成可见内容；请提高模型输出上限后重试",
            snapshot,
        )


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
        gap_by_bucket = dict(estimate.max_positive_gap_by_bucket)
        if provider_input is not None and estimate.baseline > 0:
            max_positive_error = max(max_positive_error, provider_input - estimate.baseline, 0)
            max_ratio = max(max_ratio, provider_input / estimate.baseline, 1.0)
            # 只学习当前请求规模的低估绝对值。模型服务偶发的 usage 缺失或旧 ratio 不能被
            # 伪造成样本，否则后续压缩时机会变得不可解释。
            observed_gap = max(provider_input - estimate.fallback, 0)
            gap_by_bucket[estimate.request_size_bucket] = max(
                gap_by_bucket.get(estimate.request_size_bucket, 0), observed_gap
            )
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
        snapshot: TokenUsagePayload = {
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
            "max_positive_gap_by_bucket": gap_by_bucket,
            "calibration_samples": calibration_samples,
            "calibration_key": estimate.calibration_key,
            "request_size_bucket": estimate.request_size_bucket,
            "tool_schema_hash": _stable_hash(_openai_tools(request.tools or [])),
            "system_prompt_hash": _stable_hash(
                request.system_message.text if request.system_message is not None else ""
            ),
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
        outcome = _length_response_outcome(response, snapshot)
        snapshot["response_outcome"] = outcome or "completed"
        return snapshot

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ExtendedModelResponse:
        resolve_context_budget(request)
        response = handler(request)
        # 必须先生成快照再判断空正文 length，否则本次真实 usage 会随异常一起丢失。
        snapshot = self._build_snapshot(request, response)
        _raise_for_noncommittable_output(response, snapshot)
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
        _raise_for_noncommittable_output(response, snapshot)
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"token_usage": snapshot}),
        )
