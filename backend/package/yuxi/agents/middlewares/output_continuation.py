"""模型明确耗尽输出预算后的有界恢复控制器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import math
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    hook_config,
)
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.types import Command

from yuxi.agents.middlewares.token_usage import (
    ModelOutputIncompleteError,
    fixed_context_overhead,
    resolve_context_budget,
    visible_output_exhaustion,
)
from yuxi.agents.internal_messages import (
    INTERNAL_OUTPUT_CONTINUATION_KEY,
    is_internal_output_continuation,
)

_CONTINUATION_INSTRUCTION = "继续上一条回答，从截断处直接续写。不要道歉，不要复述已经输出的内容，不要改变用户原始任务。"
_MAX_RECOVERY_ATTEMPTS = 2
_MAX_CONTINUATIONS = 1
_OUTPUT_LIMIT_QUANTUM = 1_024
_MIN_STAGE_OUTPUT_LIMIT = 8 * 1_024
_MAX_STAGE_OUTPUT_LIMIT = 16 * 1_024


class OutputRecoveryState(TypedDict):
    """单个用户请求内的恢复进度；成功结束后会被清空。"""

    attempts: int
    continuations: int
    output_limit: int
    active: bool


class OutputContinuationState(AgentState):
    # 这是请求内编排状态，不属于业务 Agent state 或前端展示数据。
    output_recovery: NotRequired[Annotated[OutputRecoveryState | None, PrivateStateAttr]]


@dataclass(frozen=True)
class RecoveryOutputLimit:
    previous: int
    target: int
    prompt_budget: int


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    return None


def _configured_output_limit(request: ModelRequest) -> int | None:
    """尽量读取部署当前上限；读取不到时由最小输出预留提供保守基线。"""
    settings = request.model_settings if isinstance(request.model_settings, Mapping) else {}
    for field_name in ("max_completion_tokens", "max_tokens"):
        if (value := _positive_int(settings.get(field_name))) is not None:
            return value

    model = request.model
    for field_name in ("max_completion_tokens", "max_tokens", "max_output_tokens"):
        if (value := _positive_int(getattr(model, field_name, None))) is not None:
            return value
    model_kwargs = getattr(model, "model_kwargs", None)
    if isinstance(model_kwargs, Mapping):
        for field_name in ("max_completion_tokens", "max_tokens", "max_output_tokens"):
            if (value := _positive_int(model_kwargs.get(field_name))) is not None:
                return value
    return None


def _output_limit_field(request: ModelRequest) -> str:
    # 保留调用方已经选择的 OpenAI 参数名；本地兼容服务默认沿用项目现有的 max_tokens。
    if isinstance(request.model_settings, Mapping) and "max_completion_tokens" in request.model_settings:
        return "max_completion_tokens"
    return "max_tokens"


def resolve_recovery_output_limit(
    request: ModelRequest,
    token_usage: Mapping[str, Any],
) -> RecoveryOutputLimit | None:
    """计算一次可实际准入的恢复上限；没有提升空间时返回 ``None``。"""
    budget = resolve_context_budget(request)
    observed_output = _positive_int(token_usage.get("provider_output_tokens")) or 0
    previous = max(
        _configured_output_limit(request) or 0,
        observed_output,
        budget.min_output_reserve_tokens,
    )
    desired = math.ceil(previous * 2 / _OUTPUT_LIMIT_QUANTUM) * _OUTPUT_LIMIT_QUANTUM
    stage_cap = min(
        max(budget.context_window // 4, _MIN_STAGE_OUTPUT_LIMIT),
        _MAX_STAGE_OUTPUT_LIMIT,
    )
    overhead = fixed_context_overhead(request)
    hard_ceiling = (
        budget.context_window
        - budget.context_safety_tokens
        - overhead.fixed_overhead
        - overhead.minimum_current_input_receipt
    )
    # 输出限额按 1K 对齐。硬上限向下取整，避免对齐后反而超过固定上下文可用空间。
    stage_cap = stage_cap // _OUTPUT_LIMIT_QUANTUM * _OUTPUT_LIMIT_QUANTUM
    hard_ceiling = hard_ceiling // _OUTPUT_LIMIT_QUANTUM * _OUTPUT_LIMIT_QUANTUM
    target = min(desired, stage_cap, hard_ceiling)
    if target <= previous:
        return None

    settings = {**request.model_settings, _output_limit_field(request): target}
    prompt_budget = resolve_context_budget(request.override(model_settings=settings)).prompt_budget
    return RecoveryOutputLimit(previous=previous, target=target, prompt_budget=prompt_budget)


def _model_response_and_update(
    response: ModelResponse | ExtendedModelResponse,
) -> tuple[ModelResponse, dict[str, Any]]:
    if isinstance(response, ExtendedModelResponse):
        return response.model_response, dict((response.command.update if response.command else {}) or {})
    return response, {}


def _with_recovery_update(
    response: ModelResponse | ExtendedModelResponse,
    recovery: OutputRecoveryState | None,
    *,
    include_clear: bool,
) -> ModelResponse | ExtendedModelResponse:
    model_response, update = _model_response_and_update(response)
    if "output_recovery" in update:
        raise RuntimeError("输出恢复状态与内层中间件发生未定义的更新冲突")
    if recovery is not None or include_clear:
        update["output_recovery"] = recovery
    if not update and not isinstance(response, ExtendedModelResponse):
        return response
    return ExtendedModelResponse(model_response=model_response, command=Command(update=update))


def _response_outcome(response: ModelResponse | ExtendedModelResponse) -> tuple[str | None, Mapping[str, Any]]:
    model_response, update = _model_response_and_update(response)
    token_usage = update.get("token_usage")
    if isinstance(token_usage, Mapping):
        outcome = token_usage.get("response_outcome")
        return (outcome if isinstance(outcome, str) else None), token_usage
    exhausted, output_tokens = visible_output_exhaustion(model_response)
    if exhausted:
        return "output_exhausted", {"provider_output_tokens": output_tokens}
    return None, {}


def _has_visible_content(response: ModelResponse | ExtendedModelResponse) -> bool:
    model_response, _update = _model_response_and_update(response)
    return any(isinstance(message, AIMessage) and bool(message.content) for message in model_response.result)


def _valid_recovery(value: Any) -> OutputRecoveryState | None:
    if not isinstance(value, Mapping) or value.get("active") is not True:
        return None
    attempts = _positive_int(value.get("attempts"))
    continuations = _positive_int(value.get("continuations"))
    output_limit = _positive_int(value.get("output_limit"))
    if attempts is None or continuations is None or output_limit is None:
        return None
    return {
        "attempts": attempts,
        "continuations": continuations,
        "output_limit": output_limit,
        "active": True,
    }


def _latest_real_message(messages: list[AnyMessage]) -> AnyMessage | None:
    return next((message for message in reversed(messages) if not is_internal_output_continuation(message)), None)


class OutputContinuationMiddleware(AgentMiddleware[OutputContinuationState]):
    """只在 Provider 明确输出耗尽后提高额度，普通请求保持原模型配置。"""

    state_schema = OutputContinuationState

    @staticmethod
    def _emit(
        request: ModelRequest,
        *,
        status: str,
        mode: str,
        attempt: int,
        limit: RecoveryOutputLimit | None,
    ) -> None:
        writer = getattr(request.runtime, "stream_writer", None)
        if not callable(writer):
            return
        event = {
            "type": "output_recovery",
            "status": status,
            "mode": mode,
            "attempt": attempt,
        }
        if limit is not None:
            event.update(
                {
                    "previous_output_tokens": limit.previous,
                    "target_output_tokens": limit.target,
                    "prompt_budget": limit.prompt_budget,
                }
            )
        writer(event)

    @staticmethod
    def _prepare_request(request: ModelRequest) -> tuple[ModelRequest, OutputRecoveryState | None, bool]:
        stored = request.state.get("output_recovery")
        recovery = _valid_recovery(stored)
        latest = _latest_real_message(list(request.messages))
        # 新 HumanMessage 表示用户已经开始另一轮请求。异常中断遗留的恢复状态必须在
        # 此处失效，不能把旧回答的“继续”指令带入新问题。
        if recovery is not None and getattr(latest, "type", None) == "human":
            return request, None, True
        if recovery is None:
            return request, None, stored is not None

        internal_message = HumanMessage(
            content=_CONTINUATION_INSTRUCTION,
            additional_kwargs={INTERNAL_OUTPUT_CONTINUATION_KEY: True},
        )
        settings = {**request.model_settings, _output_limit_field(request): recovery["output_limit"]}
        return (
            request.override(messages=[*request.messages, internal_message], model_settings=settings),
            recovery,
            False,
        )

    def _handle_response(
        self,
        request: ModelRequest,
        response: ModelResponse | ExtendedModelResponse,
        *,
        attempts: int,
        continuations: int,
        had_recovery: bool,
    ) -> ModelResponse | ExtendedModelResponse:
        outcome, token_usage = _response_outcome(response)
        if outcome == "output_exhausted" and _has_visible_content(response):
            if attempts < _MAX_RECOVERY_ATTEMPTS and continuations < _MAX_CONTINUATIONS:
                limit = resolve_recovery_output_limit(request, token_usage)
                if limit is not None:
                    next_recovery: OutputRecoveryState = {
                        "attempts": attempts + 1,
                        "continuations": continuations + 1,
                        "output_limit": limit.target,
                        "active": True,
                    }
                    self._emit(
                        request,
                        status="started",
                        mode="continuation",
                        attempt=next_recovery["attempts"],
                        limit=limit,
                    )
                    return _with_recovery_update(response, next_recovery, include_clear=False)
            self._emit(
                request,
                status="exhausted",
                mode="continuation",
                attempt=attempts,
                limit=None,
            )
            return _with_recovery_update(response, None, include_clear=had_recovery)

        if had_recovery:
            self._emit(
                request,
                status="finished",
                mode="continuation",
                attempt=attempts,
                limit=None,
            )
            return _with_recovery_update(response, None, include_clear=True)
        return response

    def _empty_retry(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
        error: ModelOutputIncompleteError,
        *,
        attempts: int,
        continuations: int,
        had_recovery: bool,
    ) -> ModelResponse | ExtendedModelResponse:
        usage = error.token_usage
        if usage.get("response_outcome") != "output_exhausted" or attempts >= _MAX_RECOVERY_ATTEMPTS:
            raise error
        limit = resolve_recovery_output_limit(request, usage)
        if limit is None:
            raise error

        attempt = attempts + 1
        self._emit(request, status="started", mode="empty_retry", attempt=attempt, limit=limit)
        settings = {**request.model_settings, _output_limit_field(request): limit.target}
        try:
            response = handler(request.override(model_settings=settings))
        except ModelOutputIncompleteError:
            self._emit(request, status="failed", mode="empty_retry", attempt=attempt, limit=limit)
            raise
        self._emit(request, status="finished", mode="empty_retry", attempt=attempt, limit=limit)
        return self._handle_response(
            request.override(model_settings=settings),
            response,
            attempts=attempt,
            continuations=continuations,
            had_recovery=had_recovery,
        )

    async def _aempty_retry(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        error: ModelOutputIncompleteError,
        *,
        attempts: int,
        continuations: int,
        had_recovery: bool,
    ) -> ModelResponse | ExtendedModelResponse:
        usage = error.token_usage
        if usage.get("response_outcome") != "output_exhausted" or attempts >= _MAX_RECOVERY_ATTEMPTS:
            raise error
        limit = resolve_recovery_output_limit(request, usage)
        if limit is None:
            raise error

        attempt = attempts + 1
        self._emit(request, status="started", mode="empty_retry", attempt=attempt, limit=limit)
        settings = {**request.model_settings, _output_limit_field(request): limit.target}
        try:
            response = await handler(request.override(model_settings=settings))
        except ModelOutputIncompleteError:
            self._emit(request, status="failed", mode="empty_retry", attempt=attempt, limit=limit)
            raise
        self._emit(request, status="finished", mode="empty_retry", attempt=attempt, limit=limit)
        return self._handle_response(
            request.override(model_settings=settings),
            response,
            attempts=attempt,
            continuations=continuations,
            had_recovery=had_recovery,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        prepared, recovery, stale = self._prepare_request(request)
        attempts = recovery["attempts"] if recovery else 0
        continuations = recovery["continuations"] if recovery else 0
        try:
            response = handler(prepared)
        except ModelOutputIncompleteError as error:
            return self._empty_retry(
                prepared,
                handler,
                error,
                attempts=attempts,
                continuations=continuations,
                had_recovery=recovery is not None,
            )
        result = self._handle_response(
            prepared,
            response,
            attempts=attempts,
            continuations=continuations,
            had_recovery=recovery is not None,
        )
        if stale and result is response:
            return _with_recovery_update(result, None, include_clear=True)
        return result

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        prepared, recovery, stale = self._prepare_request(request)
        attempts = recovery["attempts"] if recovery else 0
        continuations = recovery["continuations"] if recovery else 0
        try:
            response = await handler(prepared)
        except ModelOutputIncompleteError as error:
            return await self._aempty_retry(
                prepared,
                handler,
                error,
                attempts=attempts,
                continuations=continuations,
                had_recovery=recovery is not None,
            )
        result = self._handle_response(
            prepared,
            response,
            attempts=attempts,
            continuations=continuations,
            had_recovery=recovery is not None,
        )
        if stale and result is response:
            return _with_recovery_update(result, None, include_clear=True)
        return result

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: OutputContinuationState, runtime: Any) -> dict[str, Any] | None:  # noqa: ARG002
        recovery = _valid_recovery(state.get("output_recovery"))
        if recovery is not None:
            return {"jump_to": "model"}
        return None
