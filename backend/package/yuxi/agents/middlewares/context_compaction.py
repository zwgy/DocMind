"""Budget-driven private conversation compaction for Yuxi agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib
import json
import re
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain.chat_models import BaseChatModel
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import get_buffer_string
from langgraph.types import Command, Overwrite

from yuxi.agents.middlewares.token_usage import (
    ACTIVE_CONTEXT_SUMMARY_STATE_KEY,
    BASE_SYSTEM_MESSAGE_STATE_KEY,
    ContextBudgetConfigurationError,
    ContextWindowExceededError,
    ResolvedContextBudget,
    ensure_fixed_context_fits,
    estimate_messages_tokens,
    estimate_model_request,
    resolve_context_budget,
)
from yuxi.agents.middlewares.context_projection import projectable_rounds
from yuxi.agents.backends.composite import (
    _TOOL_RESULT_SAVED_MARKER,
    _tool_result_path,
    _tool_result_persistence_error,
    _tool_result_receipt,
    _tool_result_text,
    awrite_text_idempotently,
    create_agent_composite_backend,
    write_text_idempotently,
)
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY

_SOURCE_WINDOW_TOOL_NAMES = frozenset({"read_file", "open_kb_document"})
_TOOL_CALL_ARGUMENTS_SAVED_KEY = "_yuxi_saved_arguments_path"
_TOOL_CALL_ARGUMENTS_MIN_REDUCTION_TOKENS = 128
# 反问参数体积很小且属于中断协议；保留结构化历史可避免本地模型把归档回执仿写成下一次调用参数。
_TOOL_CALL_ARGUMENTS_ARCHIVE_EXCLUDED_TOOL_NAMES = frozenset({"ask_user_question"})
# 管理端可能已经保存旧版或自定义摘要提示词，因此持久事实合并不能只写进默认模板。
# 固定协议刻意保持很短，避免为了提升摘要质量反而挤占小上下文模型的摘要输入预算。
_SUMMARY_UPDATE_PROTOCOL = (
    "<summary_update>Replace and merge previous_summary; keep verified exact facts unless corrected; "
    "discard narration first.</summary_update>"
)
# 有限摘要无法永久容纳无限历史的每个细节；缺失时回查不可变归档，才能避免模型按相似条目猜测。
_ARCHIVE_RECOVERY_INSTRUCTION = (
    "For missing facts, inspect /outputs/conversation_history/ with ls/read_file; never guess."
)


class ContextCompactionState(AgentState):
    """Only private working-context metadata is stored in the graph state."""

    context_summary: NotRequired[str]
    context_summary_quality: NotRequired[str]
    context_compacted_through: NotRequired[str]
    context_archive_path: NotRequired[str]
    context_revision: NotRequired[int]


class _CompactionPlan(TypedDict):
    request: ModelRequest
    summary: str
    survivors: list[AnyMessage]
    compacted_through: str
    archive_path: str
    previous_revision: int
    summary_updated: bool
    summary_quality: str


class _ToolCallArgumentsArchiveCandidate(TypedDict):
    reduction: int
    message_index: int
    call_index: int
    content: str
    path: str
    replacement: AIMessage


def _message_identifier(message: AnyMessage, fallback_index: int) -> str:
    identifier = getattr(message, "id", None)
    if isinstance(identifier, str) and identifier:
        return identifier
    # LangGraph messages created by external providers occasionally lack an id.  The
    # fallback is only a state watermark, never an array index used for a later delete.
    return f"message-{fallback_index}"


def _message_segments(messages: list[AnyMessage]) -> list[list[AnyMessage]]:
    """Split history into protocol-safe turns while keeping tool-call/result pairs together."""
    if not messages:
        return []

    segments: list[list[AnyMessage]] = []
    current: list[AnyMessage] = []
    for message in messages:
        # A new human message starts a new turn.  Everything after it, including
        # tool calls and their results, must move as a unit to avoid broken protocol pairs.
        if getattr(message, "type", None) == "human" and current:
            segments.append(current)
            current = []
        current.append(message)
    if current:
        segments.append(current)
    return segments


def _summary_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    content = getattr(response, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    return str(response).strip()


def _messages_safe_for_summary(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Do not stringify image/Base64 blocks into a text-only summary request."""
    sanitized: list[AnyMessage] = []
    for message in messages:
        if not isinstance(message.content, list):
            sanitized.append(message)
            continue
        parts: list[str] = []
        removed_media = False
        for block in message.content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            else:
                removed_media = True
        if removed_media:
            parts.append("[Multimodal content is preserved in the private archive, not in this summary prompt.]")
        sanitized.append(message.model_copy(update={"content": "\n".join(parts)}))
    return sanitized


def _tool_result_tokens(message: ToolMessage) -> int:
    return estimate_messages_tokens([message])


def _source_window_receipt(messages: list[AnyMessage], message: ToolMessage) -> ToolMessage:
    """源文件已有权威副本，最终预检只能收缩回执，不能复制到 large_tool_results。"""
    args: dict[str, Any] = {}
    for candidate in reversed(messages):
        for tool_call in getattr(candidate, "tool_calls", []) or []:
            if tool_call.get("id") == message.tool_call_id:
                args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
                break
        if args:
            break

    request_hint = json.dumps(args, ensure_ascii=False, separators=(",", ":")) if args else "the original arguments"
    return message.model_copy(
        update={
            "content": (
                f"The {message.name or 'source'} window was removed from the active context to fit the model "
                f"input budget. Re-run {message.name or 'the source tool'} with a narrower offset/limit window. "
                f"Original arguments: {request_hint}"
            ),
            "additional_kwargs": {**message.additional_kwargs, _TOOL_RESULT_SAVED_MARKER: True},
        }
    )


def _planned_tool_receipt(messages: list[AnyMessage], message: ToolMessage) -> ToolMessage:
    if message.name in _SOURCE_WINDOW_TOOL_NAMES:
        return _source_window_receipt(messages, message)
    content = _tool_result_text(message)
    return _tool_result_receipt(
        message,
        _tool_result_path(message.tool_call_id or "unknown", content),
        _tool_result_tokens(message),
    )


def _tool_call_arguments_text(tool_call: dict[str, Any]) -> str:
    """序列化已执行工具的参数，供最终预算收纳后按需追溯。"""
    return json.dumps(tool_call.get("args") or {}, ensure_ascii=False, default=str, separators=(",", ":"))


def _tool_call_arguments_receipt(tool_call: dict[str, Any], path: str) -> dict[str, Any]:
    """保留工具协议所需的 ID 和名称，只收纳已执行调用的大参数。"""
    return {
        **tool_call,
        "args": {
            _TOOL_CALL_ARGUMENTS_SAVED_KEY: path,
            "note": "该工具调用已完成；完整参数已因上下文预算收纳。",
        },
    }


def _message_content_text(message: AnyMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return json.dumps(message.content, ensure_ascii=False, default=str)


def _conversation_history_path(message: AnyMessage, content: str) -> str:
    raw_id = str(getattr(message, "id", "") or "input")
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_id).strip(".-") or "input"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{VIRTUAL_PATH_CONVERSATION_HISTORY}/{safe_id}-{digest}.txt"


def _archive_manifest_path(messages: list[AnyMessage], revision: int, content: str | None = None) -> str:
    first = _message_identifier(messages[0], 0)
    last = _message_identifier(messages[-1], len(messages) - 1)
    safe_first = re.sub(r"[^A-Za-z0-9_.-]+", "-", first).strip(".-") or "first"
    safe_last = re.sub(r"[^A-Za-z0-9_.-]+", "-", last).strip(".-") or "last"
    # The caller first needs a stable-length placeholder to reserve the path in the
    # summary budget. Delay serializing a growing history until it is actually chosen.
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16] if content is not None else "0" * 16
    return f"{VIRTUAL_PATH_CONVERSATION_HISTORY}/archive-r{revision}-{safe_first}-{safe_last}-{digest}.jsonl"


def _archive_manifest_content(messages: list[AnyMessage], revision: int) -> str:
    records: list[str] = []
    for index, message in enumerate(messages):
        content_digest = hashlib.sha256(_message_content_text(message).encode("utf-8")).hexdigest()[:16]
        record = {
            "archive_id": f"r{revision}-{index}-{content_digest}",
            "message_id": _message_identifier(message, index),
            "type": getattr(message, "type", "unknown"),
            "content": message.content,
            "name": getattr(message, "name", None),
            "tool_call_id": getattr(message, "tool_call_id", None),
            "tool_calls": getattr(message, "tool_calls", None),
            "additional_kwargs": getattr(message, "additional_kwargs", {}),
        }
        records.append(json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":")))
    return "\n".join(records)


def _archive_summary_prefix(path: str) -> str:
    return (
        "<private_context_archive>\n"
        f"Latest compacted-history manifest: {path}\n"
        "Older manifests: /outputs/conversation_history/. Use ls/read_file; never guess.\n"
        "</private_context_archive>\n"
    )


def _input_receipt(message: AnyMessage, path: str, tokens: int) -> AnyMessage:
    return message.model_copy(
        update={
            "content": (
                "The current user input is stored outside the active context because it exceeds the model input "
                f"budget (about {tokens} tokens). Read {path} with offset and limit before answering."
            )
        }
    )


class ContextCompactionMiddleware(AgentMiddleware[ContextCompactionState]):
    """Keep the model working set inside the resolved input budget.

    The middleware deliberately owns its compaction control flow instead of extending
    DeepAgents' implementation.  That implementation exposes only preconfigured
    triggers and private helper state, so it cannot validate the final system prompt,
    tool schemas and messages as one request.
    """

    state_schema = ContextCompactionState

    def __init__(self, model: BaseChatModel, *, summary_prompt: str) -> None:
        super().__init__()
        # 摘要调用会作为嵌套模型事件出现在同一条流中。显式标记来源后，统一流出口
        # 才能过滤其内部文本，避免私有摘要被误当作用户可见回复发送到前端。
        self.model = model.with_config(metadata={"lc_source": "summarization"})
        self.summary_prompt = summary_prompt

    @staticmethod
    def _request_tokens(request: ModelRequest) -> int:
        return estimate_model_request(request).admission

    @staticmethod
    def _summary_system_message(system_message: SystemMessage | None, summary: str) -> SystemMessage | None:
        if not summary:
            return system_message
        private_context = (
            "\n\n<private_conversation_context>\n"
            f"{summary}\n"
            f"{_ARCHIVE_RECOVERY_INSTRUCTION}\n"
            "</private_conversation_context>\n"
            "Use this private context to continue the task. Do not mention or reproduce it verbatim."
        )
        if system_message is None:
            return SystemMessage(content=private_context.strip())
        return SystemMessage(content=f"{system_message.text}{private_context}")

    def _request_with_summary(
        self,
        request: ModelRequest,
        *,
        messages: list[AnyMessage],
        summary: str,
    ) -> ModelRequest:
        system_message = self._summary_system_message(request.system_message, summary)
        state = dict(request.state)
        if summary:
            # 这两个键只在本次中间件调用链中传递，用于把私有摘要从系统提示词估算中拆出；
            # 它们不会写入返回 Command，因此不会污染持久化图状态。
            state[ACTIVE_CONTEXT_SUMMARY_STATE_KEY] = summary
            state[BASE_SYSTEM_MESSAGE_STATE_KEY] = request.system_message
        else:
            state.pop(ACTIVE_CONTEXT_SUMMARY_STATE_KEY, None)
            state.pop(BASE_SYSTEM_MESSAGE_STATE_KEY, None)
        return request.override(messages=messages, system_message=system_message, state=state)

    def _render_summary_prompt(self, previous_summary: str, messages: list[AnyMessage], target_tokens: int) -> str:
        history = get_buffer_string(_messages_safe_for_summary(messages))
        return (
            self.summary_prompt.format(messages=history)
            + "\n\n<previous_summary>\n"
            + (previous_summary or "None")
            + "\n</previous_summary>\n"
            + _SUMMARY_UPDATE_PROTOCOL
            + "\n"
            + f"Keep the replacement summary within {max(target_tokens, 1)} tokens."
        )

    def _summary_target_tokens(
        self,
        request: ModelRequest,
        budget: ResolvedContextBudget,
        survivors: list[AnyMessage],
        previous_summary: str,
    ) -> int:
        # Reserve room for the final request first.  The remaining share is a hard
        # request-specific target, not a percentage of any model window.
        fixed_request = self._request_with_summary(request, messages=survivors, summary=previous_summary)
        fixed_tokens = self._request_tokens(fixed_request)
        return budget.prompt_budget - fixed_tokens

    def _fit_summary_to_budget(
        self,
        request: ModelRequest,
        *,
        survivors: list[AnyMessage],
        prefix: str,
        generated: str,
        budget: ResolvedContextBudget,
    ) -> tuple[str, str]:
        """Keep a non-cooperative summary model from expanding the working checkpoint."""
        summary = f"{prefix}{generated}".strip()
        prepared = self._request_with_summary(request, messages=survivors, summary=summary)
        if self._request_tokens(prepared) <= budget.prompt_budget:
            return summary, "semantic"

        low, high = 0, len(generated)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            candidate = generated[:middle].rstrip()
            candidate_summary = f"{prefix}{candidate}".strip()
            candidate_request = self._request_with_summary(request, messages=survivors, summary=candidate_summary)
            if self._request_tokens(candidate_request) <= budget.prompt_budget:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        if not best:
            raise ContextBudgetConfigurationError("上下文归档回执本身已超出可用输入预算")
        return f"{prefix}{best}".strip(), "degraded"

    @staticmethod
    def _archive_compacted_messages(
        request: ModelRequest,
        *,
        messages: list[AnyMessage],
        revision: int,
    ) -> str:
        content = _archive_manifest_content(messages, revision)
        path = _archive_manifest_path(messages, revision, content)
        if not write_text_idempotently(create_agent_composite_backend(request.runtime), path, content):
            raise ContextBudgetConfigurationError("历史上下文无法安全归档到线程文件")
        return path

    @staticmethod
    async def _aarchive_compacted_messages(
        request: ModelRequest,
        *,
        messages: list[AnyMessage],
        revision: int,
    ) -> str:
        content = _archive_manifest_content(messages, revision)
        path = _archive_manifest_path(messages, revision, content)
        if not await awrite_text_idempotently(create_agent_composite_backend(request.runtime), path, content):
            raise ContextBudgetConfigurationError("历史上下文无法安全归档到线程文件")
        return path

    @staticmethod
    def _trim_summary_text(summary: str, target_tokens: int) -> tuple[str, bool]:
        if target_tokens <= 0:
            raise ContextBudgetConfigurationError("没有可用于滚动摘要的输入预算")
        if estimate_messages_tokens([SystemMessage(content=summary)]) <= target_tokens:
            return summary, False

        low, high = 0, len(summary)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_messages_tokens([SystemMessage(content=summary[:middle])]) <= target_tokens:
                low = middle
            else:
                high = middle - 1
        return summary[:low].rstrip(), True

    def _largest_summary_piece(
        self,
        previous_summary: str,
        segment: list[AnyMessage],
        target_tokens: int,
        budget: ResolvedContextBudget,
    ) -> tuple[str, str]:
        text = get_buffer_string(_messages_safe_for_summary(segment))
        low, high = 1, len(text)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            prompt = self._render_summary_prompt(
                previous_summary,
                [SystemMessage(content=text[:middle])],
                target_tokens,
            )
            if estimate_messages_tokens([SystemMessage(content=prompt)]) <= budget.prompt_budget:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise ContextBudgetConfigurationError("摘要提示词和私有摘要已耗尽摘要模型输入预算")
        return text[:best], text[best:]

    def _create_summary(
        self,
        previous_summary: str,
        messages: list[AnyMessage],
        target_tokens: int,
        budget: ResolvedContextBudget,
    ) -> tuple[str, bool]:
        """Merge protocol-safe segments without letting the summary request exceed its own budget."""
        summary = previous_summary
        degraded = False
        pending: list[AnyMessage] = []
        for segment in _message_segments(messages):
            candidate = [*pending, *segment]
            prompt = self._render_summary_prompt(summary, candidate, target_tokens)
            if estimate_messages_tokens([SystemMessage(content=prompt)]) <= budget.prompt_budget:
                pending = candidate
                continue
            if pending:
                response = self.model.invoke(self._render_summary_prompt(summary, pending, target_tokens))
                summary = _summary_text(response)
                if not summary:
                    raise RuntimeError("摘要模型返回空内容，未提交上下文裁剪")
                summary, shortened = self._trim_summary_text(summary, target_tokens)
                degraded = degraded or shortened
                pending = []
                prompt = self._render_summary_prompt(summary, segment, target_tokens)
                if estimate_messages_tokens([SystemMessage(content=prompt)]) <= budget.prompt_budget:
                    pending = list(segment)
                    continue
            remaining = list(segment)
            while remaining:
                piece, rest = self._largest_summary_piece(summary, remaining, target_tokens, budget)
                response = self.model.invoke(
                    self._render_summary_prompt(summary, [SystemMessage(content=piece)], target_tokens)
                )
                summary = _summary_text(response)
                if not summary:
                    raise RuntimeError("摘要模型返回空内容，未提交上下文裁剪")
                summary, shortened = self._trim_summary_text(summary, target_tokens)
                degraded = degraded or shortened
                if not rest:
                    remaining = []
                else:
                    remaining = [SystemMessage(content=rest)]
        if pending:
            response = self.model.invoke(self._render_summary_prompt(summary, pending, target_tokens))
            summary = _summary_text(response)
            if not summary:
                raise RuntimeError("摘要模型返回空内容，未提交上下文裁剪")
            summary, shortened = self._trim_summary_text(summary, target_tokens)
            degraded = degraded or shortened
        return summary, degraded

    async def _acreate_summary(
        self,
        previous_summary: str,
        messages: list[AnyMessage],
        target_tokens: int,
        budget: ResolvedContextBudget,
    ) -> tuple[str, bool]:
        summary = previous_summary
        degraded = False
        pending: list[AnyMessage] = []
        for segment in _message_segments(messages):
            candidate = [*pending, *segment]
            prompt = self._render_summary_prompt(summary, candidate, target_tokens)
            if estimate_messages_tokens([SystemMessage(content=prompt)]) <= budget.prompt_budget:
                pending = candidate
                continue
            if pending:
                response = await self.model.ainvoke(self._render_summary_prompt(summary, pending, target_tokens))
                summary = _summary_text(response)
                if not summary:
                    raise RuntimeError("摘要模型返回空内容，未提交上下文裁剪")
                summary, shortened = self._trim_summary_text(summary, target_tokens)
                degraded = degraded or shortened
                pending = []
                prompt = self._render_summary_prompt(summary, segment, target_tokens)
                if estimate_messages_tokens([SystemMessage(content=prompt)]) <= budget.prompt_budget:
                    pending = list(segment)
                    continue
            remaining = list(segment)
            while remaining:
                piece, rest = self._largest_summary_piece(summary, remaining, target_tokens, budget)
                response = await self.model.ainvoke(
                    self._render_summary_prompt(summary, [SystemMessage(content=piece)], target_tokens)
                )
                summary = _summary_text(response)
                if not summary:
                    raise RuntimeError("摘要模型返回空内容，未提交上下文裁剪")
                summary, shortened = self._trim_summary_text(summary, target_tokens)
                degraded = degraded or shortened
                if not rest:
                    remaining = []
                else:
                    remaining = [SystemMessage(content=rest)]
        if pending:
            response = await self.model.ainvoke(self._render_summary_prompt(summary, pending, target_tokens))
            summary = _summary_text(response)
            if not summary:
                raise RuntimeError("摘要模型返回空内容，未提交上下文裁剪")
            summary, shortened = self._trim_summary_text(summary, target_tokens)
            degraded = degraded or shortened
        return summary, degraded

    @staticmethod
    def _current_human_input_index(messages: list[AnyMessage]) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            if getattr(messages[index], "type", None) == "human":
                return index
        return None

    @classmethod
    def _current_turn_message_indexes(cls, messages: list[AnyMessage]) -> set[int]:
        """L1 只收敛当前请求内的超大载荷，历史交给有协议校验的 L2。"""
        current_human_index = cls._current_human_input_index(messages)
        if current_human_index is None:
            return set()
        return set(range(current_human_index, len(messages)))

    @classmethod
    def _historical_projectable_message_indexes(cls, messages: list[AnyMessage]) -> set[int]:
        """仅选择最新用户请求之前、闭合且可安全回看的工具轮次。

        这里先做完整 transcript 校验，原因是历史归档回执仍会随原 AI/Tool
        消息发送给主模型；若静默跳过坏配对，后续请求依然会被兼容 provider
        拒绝，且无法定位已经损坏的 checkpoint。
        """
        current_human_index = cls._current_human_input_index(messages)
        if current_human_index is None:
            return set()
        return {
            message_index
            for round_ in projectable_rounds(messages, scope_end=current_human_index)
            for message_index in range(round_.start, round_.end)
        }

    def _externalize_current_input(
        self,
        request: ModelRequest,
        *,
        budget: ResolvedContextBudget,
    ) -> tuple[list[AnyMessage], bool]:
        messages = list(request.messages)
        index = self._current_human_input_index(messages)
        if index is None:
            return messages, False
        message = messages[index]
        tokens = estimate_messages_tokens([message])
        if tokens <= budget.prompt_budget:
            return messages, False

        content = _message_content_text(message)
        path = _conversation_history_path(message, content)
        if not write_text_idempotently(create_agent_composite_backend(request.runtime), path, content):
            raise ContextBudgetConfigurationError("当前用户输入超出可用输入预算，且无法安全归档到线程文件")
        messages[index] = _input_receipt(message, path, tokens)
        return messages, True

    async def _aexternalize_current_input(
        self,
        request: ModelRequest,
        *,
        budget: ResolvedContextBudget,
    ) -> tuple[list[AnyMessage], bool]:
        messages = list(request.messages)
        index = self._current_human_input_index(messages)
        if index is None:
            return messages, False
        message = messages[index]
        tokens = estimate_messages_tokens([message])
        if tokens <= budget.prompt_budget:
            return messages, False

        content = _message_content_text(message)
        path = _conversation_history_path(message, content)
        if not await awrite_text_idempotently(create_agent_composite_backend(request.runtime), path, content):
            raise ContextBudgetConfigurationError("当前用户输入超出可用输入预算，且无法安全归档到线程文件")
        messages[index] = _input_receipt(message, path, tokens)
        return messages, True

    def _shrink_tool_results(
        self,
        request: ModelRequest,
        *,
        messages: list[AnyMessage],
        summary: str,
        budget: ResolvedContextBudget,
        allowed_message_indexes: set[int] | None = None,
        oldest_first: bool = False,
    ) -> tuple[list[AnyMessage], bool]:
        """按最终请求的实际缺口逐个收缩结果，优先处理 Token 最大的项。"""
        messages = list(messages)
        changed = False
        backend = None
        while True:
            prepared = self._request_with_summary(request, messages=messages, summary=summary)
            if self._request_tokens(prepared) <= budget.prompt_budget:
                break
            candidates = []
            for index, message in enumerate(messages):
                if allowed_message_indexes is not None and index not in allowed_message_indexes:
                    continue
                if not isinstance(message, ToolMessage) or message.additional_kwargs.get(_TOOL_RESULT_SAVED_MARKER):
                    continue
                receipt = _planned_tool_receipt(messages, message)
                reduction = _tool_result_tokens(message) - _tool_result_tokens(receipt)
                if reduction > 0:
                    candidates.append((reduction, index, message, receipt))
            if not candidates:
                return messages, changed

            _, index, message, replacement = (
                min(candidates, key=lambda item: item[1]) if oldest_first else max(candidates, key=lambda item: item[0])
            )
            if message.name in _SOURCE_WINDOW_TOOL_NAMES:
                pass
            else:
                if backend is None:
                    backend = create_agent_composite_backend(request.runtime)
                content = _tool_result_text(message)
                path = _tool_result_path(message.tool_call_id or "unknown", content)
                replacement = (
                    _tool_result_persistence_error(message)
                    if not write_text_idempotently(backend, path, content)
                    else _tool_result_receipt(message, path, _tool_result_tokens(message))
                )
            messages[index] = replacement
            changed = True
        return messages, changed

    async def _ashrink_tool_results(
        self,
        request: ModelRequest,
        *,
        messages: list[AnyMessage],
        summary: str,
        budget: ResolvedContextBudget,
        allowed_message_indexes: set[int] | None = None,
        oldest_first: bool = False,
    ) -> tuple[list[AnyMessage], bool]:
        messages = list(messages)
        changed = False
        backend = None
        while True:
            prepared = self._request_with_summary(request, messages=messages, summary=summary)
            if self._request_tokens(prepared) <= budget.prompt_budget:
                break
            candidates = []
            for index, message in enumerate(messages):
                if allowed_message_indexes is not None and index not in allowed_message_indexes:
                    continue
                if not isinstance(message, ToolMessage) or message.additional_kwargs.get(_TOOL_RESULT_SAVED_MARKER):
                    continue
                receipt = _planned_tool_receipt(messages, message)
                reduction = _tool_result_tokens(message) - _tool_result_tokens(receipt)
                if reduction > 0:
                    candidates.append((reduction, index, message, receipt))
            if not candidates:
                return messages, changed

            _, index, message, replacement = (
                min(candidates, key=lambda item: item[1]) if oldest_first else max(candidates, key=lambda item: item[0])
            )
            if message.name in _SOURCE_WINDOW_TOOL_NAMES:
                pass
            else:
                if backend is None:
                    backend = create_agent_composite_backend(request.runtime)
                content = _tool_result_text(message)
                path = _tool_result_path(message.tool_call_id or "unknown", content)
                replacement = (
                    _tool_result_persistence_error(message)
                    if not await awrite_text_idempotently(backend, path, content)
                    else _tool_result_receipt(message, path, _tool_result_tokens(message))
                )
            messages[index] = replacement
            changed = True
        return messages, changed

    def _next_completed_tool_call_arguments_candidate(
        self,
        request: ModelRequest,
        *,
        messages: list[AnyMessage],
        summary: str,
        budget: ResolvedContextBudget,
        failed: set[tuple[int, int]],
        allowed_message_indexes: set[int] | None = None,
        oldest_first: bool = False,
    ) -> _ToolCallArgumentsArchiveCandidate | None:
        prepared = self._request_with_summary(request, messages=messages, summary=summary)
        if self._request_tokens(prepared) <= budget.prompt_budget:
            return None

        completed_call_ids = {
            str(message.tool_call_id)
            for message in messages
            if isinstance(message, ToolMessage) and message.tool_call_id
        }
        candidates: list[_ToolCallArgumentsArchiveCandidate] = []
        for message_index, message in enumerate(messages):
            if allowed_message_indexes is not None and message_index not in allowed_message_indexes:
                continue
            if not isinstance(message, AIMessage):
                continue
            tool_calls = list(message.tool_calls or [])
            for call_index, tool_call in enumerate(tool_calls):
                if (message_index, call_index) in failed or not isinstance(tool_call, dict):
                    continue
                call_id = str(tool_call.get("id") or "").strip()
                args = tool_call.get("args")
                if (
                    not call_id
                    or call_id not in completed_call_ids
                    or not isinstance(args, dict)
                    or _TOOL_CALL_ARGUMENTS_SAVED_KEY in args
                    or tool_call.get("name") in _TOOL_CALL_ARGUMENTS_ARCHIVE_EXCLUDED_TOOL_NAMES
                ):
                    continue
                content = _tool_call_arguments_text(tool_call)
                path = _tool_result_path(f"{call_id}-arguments", content)
                replaced_calls = list(tool_calls)
                replaced_calls[call_index] = _tool_call_arguments_receipt(tool_call, path)
                replacement = message.model_copy(update={"tool_calls": replaced_calls})
                reduction = estimate_messages_tokens([message]) - estimate_messages_tokens([replacement])
                # 小参数替换为归档占位只能节省少量上下文，却会给本地模型提供一个不属于
                # 任何工具 schema 的错误示例；仅归档大正文、大 JSON 等有实质收益的参数。
                if reduction >= _TOOL_CALL_ARGUMENTS_MIN_REDUCTION_TOKENS:
                    candidates.append(
                        {
                            "reduction": reduction,
                            "message_index": message_index,
                            "call_index": call_index,
                            "content": content,
                            "path": path,
                            "replacement": replacement,
                        }
                    )

        if not candidates:
            return None
        # 同步和异步入口必须使用完全相同的收益排序，否则同一段历史可能因调用方式不同
        # 生成不同的归档路径和活动上下文；这里只做确定性的候选选择，不执行任何 IO。
        if oldest_first:
            return min(candidates, key=lambda item: (item["message_index"], item["call_index"]))
        return max(candidates, key=lambda item: item["reduction"])

    def _shrink_completed_tool_call_arguments(
        self,
        request: ModelRequest,
        *,
        messages: list[AnyMessage],
        summary: str,
        budget: ResolvedContextBudget,
        allowed_message_indexes: set[int] | None = None,
        oldest_first: bool = False,
    ) -> tuple[list[AnyMessage], bool]:
        """收纳已完成调用的大参数，避免 write_file 等工具把当前轮撑满。

        当前轮不能整体归档，否则会破坏 assistant tool_call 与 ToolMessage 的配对协议。
        但只要相同 call ID 已有工具结果，历史参数已不再参与执行；保留 ID、工具名和
        可读取的收纳路径即可让后续模型理解这次调用，同时释放写入大 JSON/CSV 的空间。
        """
        messages = list(messages)
        changed = False
        backend = None
        failed: set[tuple[int, int]] = set()

        while True:
            candidate = self._next_completed_tool_call_arguments_candidate(
                request,
                messages=messages,
                summary=summary,
                budget=budget,
                failed=failed,
                allowed_message_indexes=allowed_message_indexes,
                oldest_first=oldest_first,
            )
            if candidate is None:
                return messages, changed
            if backend is None:
                backend = create_agent_composite_backend(request.runtime)
            if not write_text_idempotently(backend, candidate["path"], candidate["content"]):
                failed.add((candidate["message_index"], candidate["call_index"]))
                continue
            messages[candidate["message_index"]] = candidate["replacement"]
            changed = True

    async def _ashrink_completed_tool_call_arguments(
        self,
        request: ModelRequest,
        *,
        messages: list[AnyMessage],
        summary: str,
        budget: ResolvedContextBudget,
        allowed_message_indexes: set[int] | None = None,
        oldest_first: bool = False,
    ) -> tuple[list[AnyMessage], bool]:
        messages = list(messages)
        changed = False
        backend = None
        failed: set[tuple[int, int]] = set()

        while True:
            candidate = self._next_completed_tool_call_arguments_candidate(
                request,
                messages=messages,
                summary=summary,
                budget=budget,
                failed=failed,
                allowed_message_indexes=allowed_message_indexes,
                oldest_first=oldest_first,
            )
            if candidate is None:
                return messages, changed
            if backend is None:
                backend = create_agent_composite_backend(request.runtime)
            if not await awrite_text_idempotently(backend, candidate["path"], candidate["content"]):
                failed.add((candidate["message_index"], candidate["call_index"]))
                continue
            messages[candidate["message_index"]] = candidate["replacement"]
            changed = True

    def _build_plan(
        self,
        request: ModelRequest,
        *,
        force_compaction: bool = False,
        compact_all_history: bool = False,
    ) -> _CompactionPlan | None:
        budget = resolve_context_budget(request)
        # 历史归档和摘要都不能释放 system、当前工具 schema 或协议预留；在做任何 IO/模型调用前
        # 先明确报告部署配置问题，避免错误地归因于会话历史并消耗一次摘要请求。
        ensure_fixed_context_fits(request)
        state = request.state
        summary = str(state.get("context_summary") or "").strip()
        survivors, input_externalized = self._externalize_current_input(request, budget=budget)
        current_turn_indexes = self._current_turn_message_indexes(survivors)
        survivors, tool_results_shrunk = self._shrink_tool_results(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
            allowed_message_indexes=current_turn_indexes,
        )
        survivors, tool_arguments_shrunk = self._shrink_completed_tool_call_arguments(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
            allowed_message_indexes=current_turn_indexes,
        )
        # L2 不能按 Human turn 整段删除：每个历史 API round 都保留，只把已归档的
        # 大载荷替换成短回执。这样单轮工具协议仍是完整的，必要时模型也有路径回读。
        historical_indexes = self._historical_projectable_message_indexes(survivors)
        survivors, historical_tool_results_shrunk = self._shrink_tool_results(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
            allowed_message_indexes=historical_indexes,
            oldest_first=True,
        )
        survivors, historical_tool_arguments_shrunk = self._shrink_completed_tool_call_arguments(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
            allowed_message_indexes=historical_indexes,
            oldest_first=True,
        )
        prepared = self._request_with_summary(request, messages=survivors, summary=summary)
        if self._request_tokens(prepared) <= budget.prompt_budget and not force_compaction:
            if not (
                input_externalized
                or tool_results_shrunk
                or tool_arguments_shrunk
                or historical_tool_results_shrunk
                or historical_tool_arguments_shrunk
            ):
                return None
            return {
                "request": prepared,
                "summary": summary,
                "survivors": survivors,
                "compacted_through": str(state.get("context_compacted_through") or ""),
                "archive_path": str(state.get("context_archive_path") or ""),
                "previous_revision": int(state.get("context_revision") or 0),
                "summary_updated": False,
                "summary_quality": str(state.get("context_summary_quality") or "semantic"),
            }

        compacted: list[AnyMessage] = []
        remaining_segments = _message_segments(survivors)
        # The latest segment contains the current user turn or an unfinished protocol;
        # retain it intact and compact only completed earlier segments.
        while len(remaining_segments) > 1:
            if compact_all_history:
                compacted = [message for segment in remaining_segments[:-1] for message in segment]
                remaining_segments = remaining_segments[-1:]
            else:
                compacted.extend(remaining_segments.pop(0))
            survivors = [message for segment in remaining_segments for message in segment]
            next_revision = int(state.get("context_revision") or 0) + 1
            archive_path = _archive_manifest_path(compacted, next_revision)
            archive_prefix = _archive_summary_prefix(archive_path)
            target_tokens = self._summary_target_tokens(request, budget, survivors, archive_prefix)
            if target_tokens <= 0:
                continue
            # 归档也是压缩过程的一部分。状态必须先于归档发布，否则归档较慢或失败时，
            # 用户只会一直看到“正在生成回复”，无法理解当前实际阶段。
            request.runtime.stream_writer({"type": "context_compaction", "status": "started"})
            try:
                archive_path = self._archive_compacted_messages(
                    request,
                    messages=compacted,
                    revision=next_revision,
                )
                archive_prefix = _archive_summary_prefix(archive_path)
                try:
                    generated_summary, generated_degraded = self._create_summary(
                        summary, compacted, target_tokens, budget
                    )
                    summary, summary_quality = self._fit_summary_to_budget(
                        request,
                        survivors=survivors,
                        prefix=archive_prefix,
                        generated=generated_summary,
                        budget=budget,
                    )
                except ContextBudgetConfigurationError:
                    raise
                except Exception:
                    # 归档已成功且前缀本身通过预算计算；摘要模型不可用时保留旧摘要的可容纳部分，
                    # 使主模型仍可借助归档索引继续，而不是把一次压缩失败扩大成整轮对话失败。
                    logger.exception("摘要模型失败，改用归档回执继续本次对话")
                    summary, _ = self._fit_summary_to_budget(
                        request,
                        survivors=survivors,
                        prefix=archive_prefix,
                        generated=summary,
                        budget=budget,
                    )
                    generated_degraded = True
                    summary_quality = "degraded"
            finally:
                request.runtime.stream_writer({"type": "context_compaction", "status": "finished"})
            prepared = self._request_with_summary(request, messages=survivors, summary=summary)
            if self._request_tokens(prepared) <= budget.prompt_budget:
                last_compacted = compacted[-1]
                return {
                    "request": prepared,
                    "summary": summary,
                    "survivors": survivors,
                    "compacted_through": _message_identifier(last_compacted, len(compacted) - 1),
                    "archive_path": archive_path,
                    "previous_revision": int(state.get("context_revision") or 0),
                    "summary_updated": True,
                    "summary_quality": "degraded" if generated_degraded else summary_quality,
                }
            if compact_all_history:
                break

        raise ContextBudgetConfigurationError("最终请求仍超过模型可用输入预算，且不存在可安全压缩的完整历史交互段")

    async def _abuild_plan(
        self,
        request: ModelRequest,
        *,
        force_compaction: bool = False,
        compact_all_history: bool = False,
    ) -> _CompactionPlan | None:
        budget = resolve_context_budget(request)
        ensure_fixed_context_fits(request)
        state = request.state
        summary = str(state.get("context_summary") or "").strip()
        survivors, input_externalized = await self._aexternalize_current_input(request, budget=budget)
        current_turn_indexes = self._current_turn_message_indexes(survivors)
        survivors, tool_results_shrunk = await self._ashrink_tool_results(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
            allowed_message_indexes=current_turn_indexes,
        )
        survivors, tool_arguments_shrunk = await self._ashrink_completed_tool_call_arguments(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
            allowed_message_indexes=current_turn_indexes,
        )
        historical_indexes = self._historical_projectable_message_indexes(survivors)
        survivors, historical_tool_results_shrunk = await self._ashrink_tool_results(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
            allowed_message_indexes=historical_indexes,
            oldest_first=True,
        )
        survivors, historical_tool_arguments_shrunk = await self._ashrink_completed_tool_call_arguments(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
            allowed_message_indexes=historical_indexes,
            oldest_first=True,
        )
        prepared = self._request_with_summary(request, messages=survivors, summary=summary)
        if self._request_tokens(prepared) <= budget.prompt_budget and not force_compaction:
            if not (
                input_externalized
                or tool_results_shrunk
                or tool_arguments_shrunk
                or historical_tool_results_shrunk
                or historical_tool_arguments_shrunk
            ):
                return None
            return {
                "request": prepared,
                "summary": summary,
                "survivors": survivors,
                "compacted_through": str(state.get("context_compacted_through") or ""),
                "archive_path": str(state.get("context_archive_path") or ""),
                "previous_revision": int(state.get("context_revision") or 0),
                "summary_updated": False,
                "summary_quality": str(state.get("context_summary_quality") or "semantic"),
            }

        compacted: list[AnyMessage] = []
        remaining_segments = _message_segments(survivors)
        while len(remaining_segments) > 1:
            if compact_all_history:
                compacted = [message for segment in remaining_segments[:-1] for message in segment]
                remaining_segments = remaining_segments[-1:]
            else:
                compacted.extend(remaining_segments.pop(0))
            survivors = [message for segment in remaining_segments for message in segment]
            next_revision = int(state.get("context_revision") or 0) + 1
            archive_path = _archive_manifest_path(compacted, next_revision)
            archive_prefix = _archive_summary_prefix(archive_path)
            target_tokens = self._summary_target_tokens(request, budget, survivors, archive_prefix)
            if target_tokens <= 0:
                continue
            # 异步路径保持与同步路径相同的公开状态契约，并覆盖归档与摘要两个阶段。
            request.runtime.stream_writer({"type": "context_compaction", "status": "started"})
            try:
                archive_path = await self._aarchive_compacted_messages(
                    request,
                    messages=compacted,
                    revision=next_revision,
                )
                archive_prefix = _archive_summary_prefix(archive_path)
                try:
                    generated_summary, generated_degraded = await self._acreate_summary(
                        summary,
                        compacted,
                        target_tokens,
                        budget,
                    )
                    summary, summary_quality = self._fit_summary_to_budget(
                        request,
                        survivors=survivors,
                        prefix=archive_prefix,
                        generated=generated_summary,
                        budget=budget,
                    )
                except ContextBudgetConfigurationError:
                    raise
                except Exception:
                    # 同步路径相同：只有归档与预算收敛已成立，才允许不依赖摘要模型继续。
                    logger.exception("摘要模型失败，改用归档回执继续本次对话")
                    summary, _ = self._fit_summary_to_budget(
                        request,
                        survivors=survivors,
                        prefix=archive_prefix,
                        generated=summary,
                        budget=budget,
                    )
                    generated_degraded = True
                    summary_quality = "degraded"
            finally:
                request.runtime.stream_writer({"type": "context_compaction", "status": "finished"})
            prepared = self._request_with_summary(request, messages=survivors, summary=summary)
            if self._request_tokens(prepared) <= budget.prompt_budget:
                last_compacted = compacted[-1]
                return {
                    "request": prepared,
                    "summary": summary,
                    "survivors": survivors,
                    "compacted_through": _message_identifier(last_compacted, len(compacted) - 1),
                    "archive_path": archive_path,
                    "previous_revision": int(state.get("context_revision") or 0),
                    "summary_updated": True,
                    "summary_quality": "degraded" if generated_degraded else summary_quality,
                }
            if compact_all_history:
                break

        raise ContextBudgetConfigurationError("最终请求仍超过模型可用输入预算，且不存在可安全压缩的完整历史交互段")

    @staticmethod
    def _response_and_update(response: ModelResponse | ExtendedModelResponse) -> tuple[ModelResponse, dict[str, Any]]:
        if isinstance(response, ExtendedModelResponse):
            return response.model_response, dict((response.command.update if response.command else {}) or {})
        return response, {}

    def _commit_plan(
        self,
        response: ModelResponse | ExtendedModelResponse,
        plan: _CompactionPlan | None,
    ) -> ModelResponse | ExtendedModelResponse:
        if plan is None:
            return response
        model_response, update = self._response_and_update(response)
        owned_state_keys = {
            "messages",
            "context_summary",
            "context_summary_quality",
            "context_compacted_through",
            "context_archive_path",
            "context_revision",
        }
        conflicts = owned_state_keys & set(update)
        if conflicts:
            raise RuntimeError(f"摘要状态与内层中间件发生未定义的更新冲突: {sorted(conflicts)}")
        # The model result is appended before this command is applied.  Overwrite
        # therefore commits one coherent bounded checkpoint instead of keeping
        # discarded messages or superseded tool outputs alongside the new answer.
        update["messages"] = Overwrite([*plan["survivors"], *model_response.result])
        if plan["summary_updated"]:
            update.update(
                {
                    "context_summary": plan["summary"],
                    "context_summary_quality": plan["summary_quality"],
                    "context_compacted_through": plan["compacted_through"],
                    "context_archive_path": plan["archive_path"],
                    "context_revision": plan["previous_revision"] + 1,
                }
            )
        return ExtendedModelResponse(model_response=model_response, command=Command(update=update))

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        plan = self._build_plan(request)
        prepared = (
            plan["request"]
            if plan
            else self._request_with_summary(
                request,
                messages=list(request.messages),
                summary=str(request.state.get("context_summary") or "").strip(),
            )
        )
        try:
            return self._commit_plan(handler(prepared), plan)
        except ContextWindowExceededError as exc:
            # 空正文 length 已携带真实 usage；本次重试先应用校准包络，避免再按旧估算只压缩一段。
            recovery_request = request.override(
                state={**request.state, "token_usage": exc.token_usage},
            )
            recovery_plan = self._build_plan(recovery_request, force_compaction=True)
            if recovery_plan is None:
                raise
            return self._commit_plan(handler(recovery_plan["request"]), recovery_plan)
        except ContextOverflowError:
            # 无 usage 时无法推断误差，只能压缩全部已完成历史并重试一次。
            recovery_plan = self._build_plan(
                request,
                force_compaction=True,
                compact_all_history=True,
            )
            if recovery_plan is None:
                raise
            return self._commit_plan(handler(recovery_plan["request"]), recovery_plan)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        plan = await self._abuild_plan(request)
        prepared = (
            plan["request"]
            if plan
            else self._request_with_summary(
                request,
                messages=list(request.messages),
                summary=str(request.state.get("context_summary") or "").strip(),
            )
        )
        try:
            return self._commit_plan(await handler(prepared), plan)
        except ContextWindowExceededError as exc:
            recovery_request = request.override(
                state={**request.state, "token_usage": exc.token_usage},
            )
            recovery_plan = await self._abuild_plan(recovery_request, force_compaction=True)
            if recovery_plan is None:
                raise
            return self._commit_plan(await handler(recovery_plan["request"]), recovery_plan)
        except ContextOverflowError:
            recovery_plan = await self._abuild_plan(
                request,
                force_compaction=True,
                compact_all_history=True,
            )
            if recovery_plan is None:
                raise
            return self._commit_plan(await handler(recovery_plan["request"]), recovery_plan)


def create_context_compaction_middleware(
    model: BaseChatModel,
    *,
    summary_prompt: str,
) -> ContextCompactionMiddleware:
    """Create the single project-owned budget-driven context compaction middleware."""
    return ContextCompactionMiddleware(model=model, summary_prompt=summary_prompt)
