"""Agent 交付物协议与发布辅助函数。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command

from yuxi.utils.paths import CONVERSATION_HISTORY_DIR_NAME, LARGE_TOOL_RESULTS_DIR_NAME

ARTIFACT_DELIVERY_SCHEMA = "yuxi.artifact-delivery/v1"

_INTERNAL_OUTPUT_DIR_NAMES = frozenset(
    {CONVERSATION_HISTORY_DIR_NAME, LARGE_TOOL_RESULTS_DIR_NAME, "large_tool_history"}
)


def artifact_delivery_payload(filepaths: list[str]) -> dict[str, Any]:
    """构造统一交付协议载荷；调用方必须先完成路径边界校验。"""
    normalized_paths = list(dict.fromkeys(path.strip() for path in filepaths if path.strip()))
    if not normalized_paths:
        raise ValueError("交付物路径不能为空")
    return {
        "schema": ARTIFACT_DELIVERY_SCHEMA,
        "paths": normalized_paths,
    }


def normalize_artifact_path(filepath: str, runtime: ToolRuntime) -> str:
    """把当前线程 outputs 下的真实或虚拟文件路径规范化为用户可见虚拟路径。"""
    from yuxi.agents.backends.sandbox.paths import (
        VIRTUAL_PATH_PREFIX,
        ensure_thread_dirs,
        resolve_virtual_path,
        sandbox_outputs_dir,
    )

    outputs_virtual_prefix = f"{VIRTUAL_PATH_PREFIX}/outputs"
    runtime_context = runtime.context
    thread_id = getattr(runtime_context, "file_thread_id", None) or getattr(runtime_context, "thread_id", None)
    if not thread_id:
        raise ValueError("当前运行时缺少 thread_id")
    uid = getattr(runtime_context, "uid", None)
    if not uid:
        raise ValueError("当前运行时缺少 uid")

    ensure_thread_dirs(thread_id, str(uid))
    outputs_dir = sandbox_outputs_dir(thread_id).resolve()
    normalized_input = str(filepath or "").strip()
    if not normalized_input:
        raise ValueError("文件路径不能为空")

    stripped = normalized_input.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
    if stripped == virtual_prefix or stripped.startswith(f"{virtual_prefix}/"):
        actual_path = resolve_virtual_path(thread_id, normalized_input, uid=str(uid))
    else:
        actual_path = Path(normalized_input).expanduser().resolve()

    if not actual_path.exists() or not actual_path.is_file():
        raise ValueError(f"文件不存在或不是普通文件: {normalized_input}")

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"只允许展示 {outputs_virtual_prefix}/ 下的文件: {normalized_input}") from exc

    if relative_path.parts and relative_path.parts[0] in _INTERNAL_OUTPUT_DIR_NAMES:
        raise ValueError(f"不允许展示工具调用阶段文件: {outputs_virtual_prefix}/{relative_path.as_posix()}")

    return f"{outputs_virtual_prefix}/{relative_path.as_posix()}"


def deliver_artifacts(
    *,
    filepaths: list[str],
    runtime: ToolRuntime,
    tool_call_id: str,
    content: str | dict[str, Any],
) -> Command:
    """构造一次原子交付更新，同时服务实时状态和本轮历史归属。"""
    normalized_paths = list(dict.fromkeys(normalize_artifact_path(filepath, runtime) for filepath in filepaths))
    artifact = artifact_delivery_payload(normalized_paths)

    # content 供模型理解工具结果；artifact 是不进入模型上下文的控制面协议，
    # chat_service 只按此协议提取本轮交付物，不再识别工具名或文件类型。
    message_content = (
        content
        if isinstance(content, str)
        else json.dumps(content, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    return Command(
        update={
            "artifacts": normalized_paths,
            "messages": [
                ToolMessage(
                    content=message_content,
                    tool_call_id=tool_call_id,
                    artifact=artifact,
                )
            ],
        }
    )


def delivered_artifact_paths(message: dict[str, Any]) -> list[str]:
    """从成功 ToolMessage 的标准协议中提取已明确交付的路径。"""
    if message.get("type") != "tool" or message.get("status") == "error":
        return []

    artifact = message.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("schema") != ARTIFACT_DELIVERY_SCHEMA:
        return []

    paths = artifact.get("paths")
    if not isinstance(paths, list):
        return []
    return list(dict.fromkeys(path.strip() for path in paths if isinstance(path, str) and path.strip()))
