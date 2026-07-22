from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from deepagents.backends.composite import (
    CompositeBackend,
    _remap_file_info_path,
    _route_for_path,
    _strip_route_from_pattern,
)
from deepagents.backends.protocol import FileInfo, GlobResult
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command

from yuxi.agents.skills.service import normalize_string_list
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY, VIRTUAL_PATH_LARGE_TOOL_RESULTS, VIRTUAL_PATH_OUTPUTS

from .sandbox import ProvisionerSandboxBackend
from .skills_backend import SelectedSkillsReadonlyBackend

_TOOL_RESULT_EVICTION_EXEMPT_TOOLS = frozenset({"read_file", "open_kb_document"})
_TOOL_RESULT_SAVED_MARKER = "yuxi_tool_result_saved"


def _tool_result_text(message: ToolMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    if isinstance(message.content, list):
        return "\n".join(
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in message.content
            if isinstance(item, str) or isinstance(item, dict)
        )
    return str(message.content)


def _tool_result_path(tool_call_id: str, content: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", tool_call_id).strip(".-") or "unknown"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{VIRTUAL_PATH_LARGE_TOOL_RESULTS}/{safe_id}-{digest}.txt"


def _path_matches_content_hash(path: str, content: str) -> bool:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return path.endswith(f"-{digest}.txt")


def write_text_idempotently(backend, path: str, content: str) -> bool:
    """内容哈希路径已存在时，只有内容完全一致才可视为此前写入成功。"""
    result = backend.write(path, content)
    if result is not None and not getattr(result, "error", None):
        return True
    error = str(getattr(result, "error", "")).lower()
    return "already exists" in error and _path_matches_content_hash(path, content)


async def awrite_text_idempotently(backend, path: str, content: str) -> bool:
    result = await backend.awrite(path, content)
    if result is not None and not getattr(result, "error", None):
        return True
    error = str(getattr(result, "error", "")).lower()
    return "already exists" in error and _path_matches_content_hash(path, content)


def _tool_result_receipt(message: ToolMessage, path: str, tokens: int) -> ToolMessage:
    additional_kwargs = {**message.additional_kwargs, _TOOL_RESULT_SAVED_MARKER: True}
    return message.model_copy(
        update={
            "content": (
                "[Tool result saved]\n"
                f"Tool: {message.name or 'unknown'}\n"
                f"Approx tokens: {tokens}\n"
                f"Full output path: {path}\n"
                "Use read_file with offset and limit when the full result is needed."
            ),
            "additional_kwargs": additional_kwargs,
        }
    )


def _tool_result_persistence_error(message: ToolMessage) -> ToolMessage:
    # The raw result has not been made recoverable.  Returning it would put an
    # unbounded payload back into checkpoint state and silently violate the budget.
    return message.model_copy(
        update={
            "content": "Tool result was too large and could not be persisted. Retry the tool call.",
            "status": "error",
        }
    )


def _source_window_retry(message: ToolMessage, tool_call: dict, token_limit: int) -> ToolMessage:
    """源文件已有权威副本，超限时要求缩小原有窗口，不能再写出一份副本。"""
    tokens = int(count_tokens_approximately([message], use_usage_metadata_scaling=False))
    if tokens <= token_limit:
        return message

    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
    try:
        requested_limit = max(int(args.get("limit") or 1), 1)
    except (TypeError, ValueError):
        requested_limit = 1
    suggested_limit = max(1, requested_limit * token_limit // tokens)
    return message.model_copy(
        update={
            "content": (
                f"Source file window is about {tokens} tokens, above the configured inline limit "
                f"({token_limit} tokens). Retry read_file with the same path and offset, using "
                f"limit no greater than {suggested_limit}."
            ),
            "status": "error",
        }
    )


def _coerce_glob_result(result) -> GlobResult:
    if isinstance(result, GlobResult):
        return result
    return GlobResult(matches=result or [])


class CustomCompositeBackend(CompositeBackend):
    """修复 glob 路由逻辑的 CompositeBackend。"""

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        backend, backend_path, route_prefix = _route_for_path(
            default=self.default,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            result = _coerce_glob_result(backend.glob(pattern, backend_path))
            if result.error:
                return result
            return GlobResult(matches=[_remap_file_info_path(fi, route_prefix) for fi in (result.matches or [])])

        if path is None or path == "/":
            results: list[FileInfo] = []
            default_result = _coerce_glob_result(self.default.glob(pattern, path))
            if default_result.error:
                return default_result
            results.extend(default_result.matches or [])
            for route_prefix, backend in self.routes.items():
                route_pattern = _strip_route_from_pattern(pattern, route_prefix)
                result = _coerce_glob_result(backend.glob(route_pattern, "/"))
                if result.error:
                    return result
                results.extend(_remap_file_info_path(fi, route_prefix) for fi in (result.matches or []))
            results.sort(key=lambda x: x.get("path", ""))
            return GlobResult(matches=results)

        return _coerce_glob_result(self.default.glob(pattern, path))

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        backend, backend_path, route_prefix = _route_for_path(
            default=self.default,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            result = _coerce_glob_result(await backend.aglob(pattern, backend_path))
            if result.error:
                return result
            return GlobResult(matches=[_remap_file_info_path(fi, route_prefix) for fi in (result.matches or [])])

        if path is None or path == "/":
            results: list[FileInfo] = []
            default_result = _coerce_glob_result(await self.default.aglob(pattern, path))
            if default_result.error:
                return default_result
            results.extend(default_result.matches or [])
            for route_prefix, backend in self.routes.items():
                route_pattern = _strip_route_from_pattern(pattern, route_prefix)
                result = _coerce_glob_result(await backend.aglob(route_pattern, "/"))
                if result.error:
                    return result
                results.extend(_remap_file_info_path(fi, route_prefix) for fi in (result.matches or []))
            results.sort(key=lambda x: x.get("path", ""))
            return GlobResult(matches=results)

        return _coerce_glob_result(await self.default.aglob(pattern, path))


class YuxiFilesystemMiddleware(FilesystemMiddleware):
    """Filesystem middleware that budgets large tool outputs before they hit model context."""

    def _should_offload(self, message: ToolMessage) -> tuple[str, int] | None:
        if self._tool_token_limit_before_evict is None:
            return None
        if message.additional_kwargs.get(_TOOL_RESULT_SAVED_MARKER):
            return None
        content = _tool_result_text(message)
        tokens = int(count_tokens_approximately([message], use_usage_metadata_scaling=False))
        return (content, tokens) if tokens > self._tool_token_limit_before_evict else None

    def _offload_message(self, message: ToolMessage, backend) -> ToolMessage:
        candidate = self._should_offload(message)
        if candidate is None:
            return message
        content, tokens = candidate
        path = _tool_result_path(message.tool_call_id or "unknown", content)
        if not write_text_idempotently(backend, path, content):
            return _tool_result_persistence_error(message)
        return _tool_result_receipt(message, path, tokens)

    async def _aoffload_message(self, message: ToolMessage, backend) -> ToolMessage:
        candidate = self._should_offload(message)
        if candidate is None:
            return message
        content, tokens = candidate
        path = _tool_result_path(message.tool_call_id or "unknown", content)
        if not await awrite_text_idempotently(backend, path, content):
            return _tool_result_persistence_error(message)
        return _tool_result_receipt(message, path, tokens)

    def _process_result(self, tool_result, runtime):
        backend = self._get_backend(runtime)
        if isinstance(tool_result, ToolMessage):
            return self._offload_message(tool_result, backend)
        if not isinstance(tool_result, Command) or tool_result.update is None:
            return tool_result
        messages = [
            self._offload_message(message, backend) if isinstance(message, ToolMessage) else message
            for message in tool_result.update.get("messages", [])
        ]
        return Command(
            goto=tool_result.goto,
            graph=tool_result.graph,
            update={**tool_result.update, "messages": messages},
        )

    async def _aprocess_result(self, tool_result, runtime):
        backend = self._get_backend(runtime)
        if isinstance(tool_result, ToolMessage):
            return await self._aoffload_message(tool_result, backend)
        if not isinstance(tool_result, Command) or tool_result.update is None:
            return tool_result
        messages = [
            await self._aoffload_message(message, backend) if isinstance(message, ToolMessage) else message
            for message in tool_result.update.get("messages", [])
        ]
        return Command(
            goto=tool_result.goto,
            graph=tool_result.graph,
            update={**tool_result.update, "messages": messages},
        )

    def wrap_tool_call(self, request, handler):
        tool_result = handler(request)

        if self._tool_token_limit_before_evict is None:
            return tool_result
        if request.tool_call["name"] == "read_file" and isinstance(tool_result, ToolMessage):
            return _source_window_retry(tool_result, request.tool_call, self._tool_token_limit_before_evict)
        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return tool_result

        return self._process_result(tool_result, request.runtime)

    async def awrap_tool_call(self, request, handler):
        tool_result = await handler(request)

        if self._tool_token_limit_before_evict is None:
            return tool_result
        if request.tool_call["name"] == "read_file" and isinstance(tool_result, ToolMessage):
            return _source_window_retry(tool_result, request.tool_call, self._tool_token_limit_before_evict)
        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return tool_result

        return await self._aprocess_result(tool_result, request.runtime)


@dataclass(frozen=True)
class _BackendScope:
    thread_id: str
    uid: str
    readable_skills: list[str]
    file_thread_id: str
    skills_thread_id: str

    @classmethod
    def from_runtime(cls, runtime) -> _BackendScope:
        config = getattr(runtime, "config", None)
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        context = getattr(runtime, "context", None)
        state = getattr(runtime, "state", None)
        return cls.from_sources(
            configurable if isinstance(configurable, dict) else {},
            context,
            state if isinstance(state, dict) else {},
            readable_skills_source=context,
            error_context="runtime configurable context",
        )

    @classmethod
    def from_sources(cls, *sources, readable_skills_source, error_context: str) -> _BackendScope:
        def string_value(key: str) -> str | None:
            for source in sources:
                value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        thread_id = string_value("thread_id")
        if not thread_id:
            raise ValueError(f"thread_id is required in {error_context}")

        uid = string_value("uid")
        if not uid:
            raise ValueError(f"uid is required in {error_context}")

        selected = getattr(readable_skills_source, "_readable_skills", [])
        return cls(
            thread_id=thread_id,
            uid=uid,
            readable_skills=normalize_string_list(selected if isinstance(selected, list) else []),
            file_thread_id=string_value("file_thread_id") or thread_id,
            skills_thread_id=string_value("skills_thread_id") or thread_id,
        )

    def create_backend(self) -> CompositeBackend:
        return CustomCompositeBackend(
            default=ProvisionerSandboxBackend(
                thread_id=self.thread_id,
                uid=self.uid,
                readable_skills=self.readable_skills,
                file_thread_id=self.file_thread_id,
                skills_thread_id=self.skills_thread_id,
            ),
            routes={
                "/skills/": SelectedSkillsReadonlyBackend(selected_slugs=self.readable_skills),
            },
            artifacts_root=VIRTUAL_PATH_OUTPUTS,
        )


def create_agent_composite_backend(runtime) -> CompositeBackend:
    return _BackendScope.from_runtime(runtime).create_backend()


def create_agent_filesystem_middleware(
    tool_token_limit_before_evict: int | None = None,
    *,
    context=None,
) -> FilesystemMiddleware:
    backend = create_agent_composite_backend
    if context is not None:
        backend = _BackendScope.from_sources(
            context,
            readable_skills_source=context,
            error_context="runtime context",
        ).create_backend()
    middleware = YuxiFilesystemMiddleware(
        backend=backend,
        tool_token_limit_before_evict=tool_token_limit_before_evict,
    )
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    middleware._conversation_history_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    return middleware
