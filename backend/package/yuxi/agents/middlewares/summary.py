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
from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately, get_buffer_string
from langgraph.types import Command, Overwrite

from yuxi.agents.middlewares.token_usage import (
    ContextBudgetConfigurationError,
    ResolvedContextBudget,
    resolve_context_budget,
)
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
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY

_SOURCE_WINDOW_TOOL_NAMES = frozenset({"read_file", "open_kb_document"})


class ContextSummaryState(AgentState):
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
    return int(count_tokens_approximately([message], use_usage_metadata_scaling=False))


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
        "Use read_file with offset and limit only when an earlier detail is needed.\n"
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


class YuxiSummarizationMiddleware(AgentMiddleware[ContextSummaryState]):
    """Keep the model working set inside the resolved input budget.

    The middleware deliberately owns its compaction control flow instead of extending
    DeepAgents' implementation.  That implementation exposes only preconfigured
    triggers and private helper state, so it cannot validate the final system prompt,
    tool schemas and messages as one request.
    """

    state_schema = ContextSummaryState

    def __init__(self, model: str | BaseChatModel, *, summary_prompt: str) -> None:
        super().__init__()
        self.model = model
        self.summary_prompt = summary_prompt

    @staticmethod
    def _request_tokens(request: ModelRequest) -> int:
        system_messages = [request.system_message] if request.system_message is not None else []
        return int(count_tokens_approximately([*system_messages, *request.messages], tools=request.tools or []))

    @staticmethod
    def _summary_system_message(system_message: SystemMessage | None, summary: str) -> SystemMessage | None:
        if not summary:
            return system_message
        private_context = (
            "\n\n<private_conversation_context>\n"
            f"{summary}\n"
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
        return request.override(messages=messages, system_message=system_message)

    def _render_summary_prompt(self, previous_summary: str, messages: list[AnyMessage], target_tokens: int) -> str:
        history = get_buffer_string(_messages_safe_for_summary(messages))
        return (
            self.summary_prompt.format(messages=history)
            + "\n\n<previous_summary>\n"
            + (previous_summary or "None")
            + "\n</previous_summary>\n"
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
        if int(count_tokens_approximately([SystemMessage(content=summary)])) <= target_tokens:
            return summary, False

        low, high = 0, len(summary)
        while low < high:
            middle = (low + high + 1) // 2
            if int(count_tokens_approximately([SystemMessage(content=summary[:middle])])) <= target_tokens:
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
            if int(count_tokens_approximately([SystemMessage(content=prompt)])) <= budget.prompt_budget:
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
            if int(count_tokens_approximately([SystemMessage(content=prompt)])) <= budget.prompt_budget:
                pending = candidate
                continue
            if pending:
                response = self.model.invoke(self._render_summary_prompt(summary, pending, target_tokens))
                summary = _summary_text(response)
                if not summary:
                    raise RuntimeError("摘要模型返回空内容，未提交上下文裁剪")
                summary, shortened = self._trim_summary_text(summary, target_tokens)
                degraded = degraded or shortened
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
            if int(count_tokens_approximately([SystemMessage(content=prompt)])) <= budget.prompt_budget:
                pending = candidate
                continue
            if pending:
                response = await self.model.ainvoke(self._render_summary_prompt(summary, pending, target_tokens))
                summary = _summary_text(response)
                if not summary:
                    raise RuntimeError("摘要模型返回空内容，未提交上下文裁剪")
                summary, shortened = self._trim_summary_text(summary, target_tokens)
                degraded = degraded or shortened
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
        tokens = int(count_tokens_approximately([message], use_usage_metadata_scaling=False))
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
        tokens = int(count_tokens_approximately([message], use_usage_metadata_scaling=False))
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
                if not isinstance(message, ToolMessage) or message.additional_kwargs.get(_TOOL_RESULT_SAVED_MARKER):
                    continue
                receipt = _planned_tool_receipt(messages, message)
                reduction = _tool_result_tokens(message) - _tool_result_tokens(receipt)
                if reduction > 0:
                    candidates.append((reduction, index, message, receipt))
            if not candidates:
                return messages, changed

            _, index, message, replacement = max(candidates, key=lambda item: item[0])
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
                if not isinstance(message, ToolMessage) or message.additional_kwargs.get(_TOOL_RESULT_SAVED_MARKER):
                    continue
                receipt = _planned_tool_receipt(messages, message)
                reduction = _tool_result_tokens(message) - _tool_result_tokens(receipt)
                if reduction > 0:
                    candidates.append((reduction, index, message, receipt))
            if not candidates:
                return messages, changed

            _, index, message, replacement = max(candidates, key=lambda item: item[0])
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

    def _build_plan(self, request: ModelRequest, *, force_compaction: bool = False) -> _CompactionPlan | None:
        budget = resolve_context_budget(request)
        state = request.state
        summary = str(state.get("context_summary") or "").strip()
        survivors, input_externalized = self._externalize_current_input(request, budget=budget)
        survivors, tool_results_shrunk = self._shrink_tool_results(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
        )
        prepared = self._request_with_summary(request, messages=survivors, summary=summary)
        if self._request_tokens(prepared) <= budget.prompt_budget and not force_compaction:
            if not (input_externalized or tool_results_shrunk):
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
            compacted.extend(remaining_segments.pop(0))
            survivors = [message for segment in remaining_segments for message in segment]
            next_revision = int(state.get("context_revision") or 0) + 1
            archive_path = _archive_manifest_path(compacted, next_revision)
            archive_prefix = _archive_summary_prefix(archive_path)
            target_tokens = self._summary_target_tokens(request, budget, survivors, archive_prefix)
            if target_tokens <= 0:
                continue
            archive_path = self._archive_compacted_messages(
                request,
                messages=compacted,
                revision=next_revision,
            )
            generated_summary, generated_degraded = self._create_summary(summary, compacted, target_tokens, budget)
            summary, summary_quality = self._fit_summary_to_budget(
                request,
                survivors=survivors,
                prefix=_archive_summary_prefix(archive_path),
                generated=generated_summary,
                budget=budget,
            )
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

        raise ContextBudgetConfigurationError(
            "最终请求仍超过模型可用输入预算，且不存在可安全压缩的完整历史交互段"
        )

    async def _abuild_plan(self, request: ModelRequest, *, force_compaction: bool = False) -> _CompactionPlan | None:
        budget = resolve_context_budget(request)
        state = request.state
        summary = str(state.get("context_summary") or "").strip()
        survivors, input_externalized = await self._aexternalize_current_input(request, budget=budget)
        survivors, tool_results_shrunk = await self._ashrink_tool_results(
            request,
            messages=survivors,
            summary=summary,
            budget=budget,
        )
        prepared = self._request_with_summary(request, messages=survivors, summary=summary)
        if self._request_tokens(prepared) <= budget.prompt_budget and not force_compaction:
            if not (input_externalized or tool_results_shrunk):
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
            compacted.extend(remaining_segments.pop(0))
            survivors = [message for segment in remaining_segments for message in segment]
            next_revision = int(state.get("context_revision") or 0) + 1
            archive_path = _archive_manifest_path(compacted, next_revision)
            archive_prefix = _archive_summary_prefix(archive_path)
            target_tokens = self._summary_target_tokens(request, budget, survivors, archive_prefix)
            if target_tokens <= 0:
                continue
            archive_path = await self._aarchive_compacted_messages(
                request,
                messages=compacted,
                revision=next_revision,
            )
            generated_summary, generated_degraded = await self._acreate_summary(
                summary,
                compacted,
                target_tokens,
                budget,
            )
            summary, summary_quality = self._fit_summary_to_budget(
                request,
                survivors=survivors,
                prefix=_archive_summary_prefix(archive_path),
                generated=generated_summary,
                budget=budget,
            )
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

        raise ContextBudgetConfigurationError(
            "最终请求仍超过模型可用输入预算，且不存在可安全压缩的完整历史交互段"
        )

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
        prepared = plan["request"] if plan else self._request_with_summary(
            request,
            messages=list(request.messages),
            summary=str(request.state.get("context_summary") or "").strip(),
        )
        try:
            return self._commit_plan(handler(prepared), plan)
        except ContextOverflowError:
            # A provider can reject a request despite a conservative count.  Retry once
            # through the same planner; no state is committed until a model call succeeds.
            recovery_plan = self._build_plan(request, force_compaction=True)
            if recovery_plan is None:
                raise
            return self._commit_plan(handler(recovery_plan["request"]), recovery_plan)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        plan = await self._abuild_plan(request)
        prepared = plan["request"] if plan else self._request_with_summary(
            request,
            messages=list(request.messages),
            summary=str(request.state.get("context_summary") or "").strip(),
        )
        try:
            return self._commit_plan(await handler(prepared), plan)
        except ContextOverflowError:
            recovery_plan = await self._abuild_plan(request, force_compaction=True)
            if recovery_plan is None:
                raise
            return self._commit_plan(await handler(recovery_plan["request"]), recovery_plan)


def create_summary_middleware(
    model: str | BaseChatModel,
    *,
    summary_prompt: str,
) -> YuxiSummarizationMiddleware:
    """Create the single project-owned budget-driven summarization middleware."""
    return YuxiSummarizationMiddleware(model=model, summary_prompt=summary_prompt)
