"""知识库工具模块"""

import inspect
import json
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.context import DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS
from yuxi.agents.toolkits.registry import tool
from yuxi.knowledge.base import KnowledgeBase
from yuxi.knowledge.schemas import (
    FindInputSchema,
    FindOutputSchema,
    OpenInputSchema,
    OpenOutputSchema,
    SearchInputSchema,
    SearchOutputSchema,
)
from yuxi.utils import logger

# ========== 通用知识库工具函数 ==========


def _get_knowledge_base():
    from yuxi import knowledge_base

    return knowledge_base


class ListKBsInput(BaseModel):
    """列出用户可访问的知识库输入模型"""

    # Langchain 的 runtime 注入机制要求必须有参数
    dummy: str = Field(default="", description="Dummy parameter - ignore")  # Add this


@tool(category="knowledge", tags=["知识库"], args_schema=ListKBsInput)
async def list_kbs(dummy: str, runtime: ToolRuntime) -> str:  # Now has 2 params
    """列出当前用户可访问的知识库列表

    返回用户基于权限可访问的知识库名称列表。这个列表是根据用户的角色和部门信息过滤后的结果，
    但不包括用户在当前对话中未启用的知识库。

    Returns:
        用户可访问的知识库名称列表（字符串格式）
    """
    # 从 runtime.context 获取用户信息
    runtime_context = runtime.context
    uid = getattr(runtime_context, "uid", None)
    if not uid:
        return "无法获取用户信息"

    # 打印 runtime—context 中的所有信息以进行调试
    logger.debug(f"Runtime context: {runtime_context.__dict__}")

    enabled_kb_names = getattr(runtime_context, "knowledges", None)

    try:
        from yuxi.agents.backends.knowledge_base_backend import resolve_visible_knowledge_bases_for_context

        available_kbs = await resolve_visible_knowledge_bases_for_context(runtime_context)
    except Exception as e:
        logger.error(f"获取用户知识库列表失败: {e}")
        return f"获取知识库列表失败: {str(e)}"

    all_kb_names = [kb["name"] for kb in available_kbs]

    logger.debug(f"用户 {uid} 可访问的知识库列表: {all_kb_names}")
    logger.debug(f"用户 {uid} 当前对话启用的知识库列表: {enabled_kb_names}")

    if not available_kbs:
        return "当前没有可访问的知识库"

    # 格式化输出（包含名称和描述）
    kb_list = []
    for kb in available_kbs:
        name = kb.get("name", "")
        desc = kb.get("description") or "无描述"
        kb_list.append({"kb_id": kb.get("kb_id"), "name": name, "description": desc})

    return kb_list


class GetMindmapInput(BaseModel):
    """获取思维导图输入模型"""

    kb_name: str = Field(description="知识库名称，用于指定要获取思维导图的知识库")


@tool(category="knowledge", tags=["知识库"], args_schema=GetMindmapInput)
async def get_mindmap(kb_name: str, runtime: ToolRuntime) -> str:
    """获取指定知识库的思维导图结构

    当用户想要了解知识库的整体结构、文件分类、知识架构时使用此工具。
    返回知识库的思维导图层级结构。

    Args:
        kb_name: 知识库名称

    Returns:
        知识库的思维导图结构（文本格式）
    """
    if not kb_name:
        return "请提供知识库名称"

    # 获取所有检索器
    knowledge_base = _get_knowledge_base()
    retrievers = knowledge_base.get_retrievers()

    # 查找对应的知识库
    target_kb_id = None
    target_info = None
    for kb_id, info in retrievers.items():
        if info["name"] == kb_name:
            target_kb_id = kb_id
            target_info = info
            break

    if not target_kb_id:
        return f"知识库 '{kb_name}' 不存在"

    try:
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_kb_id(target_kb_id)

        if kb is None:
            return f"知识库 {target_info['name']} 不存在"

        mindmap_data = kb.mindmap

        if not mindmap_data:
            return f"知识库 {target_info['name']} 还没有生成思维导图。"

        # 将思维导图数据转换为文本格式
        def mindmap_to_text(node, level=0):
            """递归将思维导图JSON转换为层级文本"""
            indent = "  " * level
            text = f"{indent}- {node.get('content', '')}\n"
            for child in node.get("children", []):
                text += mindmap_to_text(child, level + 1)
            return text

        mindmap_text = f"知识库 {target_info['name']} 的思维导图结构：\n\n"
        mindmap_text += mindmap_to_text(mindmap_data)

        return mindmap_text

    except Exception as e:
        logger.error(f"获取思维导图失败: {e}")
        return f"获取思维导图失败: {str(e)}"


QueryKBInput = SearchInputSchema
OpenKBDocumentInput = OpenInputSchema
FindKBDocumentInput = FindInputSchema


def _configured_tool_inline_limit(runtime: ToolRuntime | None) -> int:
    """源文件窗口不做二次落盘，但必须遵守 Agent 配置的单项内联上限。"""
    context = getattr(runtime, "context", None)
    raw_limit = getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS)
    try:
        limit_k = int(raw_limit)
    except (TypeError, ValueError):
        limit_k = DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS
    return max(limit_k, 1) * 1024


def _open_window_token_count(window: dict[str, Any]) -> int:
    content = json.dumps(window, ensure_ascii=False, separators=(",", ":"))
    message = ToolMessage(content=content, name="open_kb_document", tool_call_id="window")
    return int(count_tokens_approximately([message], use_usage_metadata_scaling=False))


def _bounded_open_window(
    window: dict[str, Any],
    *,
    kb_id: str,
    file_id: str,
    token_limit: int,
) -> dict[str, Any]:
    """保留完整行和 continuation，避免单个源文件窗口挤占后续模型请求。"""
    if _open_window_token_count(window) <= token_limit:
        return window

    offset = int(window.get("offset") or 0)
    total_lines = int(window.get("total_lines") or 0)
    lines = str(window.get("content") or "").splitlines()

    def candidate(line_count: int) -> dict[str, Any]:
        end = offset + line_count
        return {
            **window,
            "start_line": offset + 1 if line_count else 0,
            "end_line": end if line_count else 0,
            "offset": offset,
            "window_size": line_count,
            "has_more_before": offset > 0,
            "has_more_after": end < total_lines,
            "next_offset": end if end < total_lines else None,
            "content": "\n".join(lines[:line_count]),
        }

    low, high = 0, len(lines)
    while low < high:
        middle = (low + high + 1) // 2
        if _open_window_token_count(candidate(middle)) <= token_limit:
            low = middle
        else:
            high = middle - 1

    if low:
        return candidate(low)

    # 第一行本身已超限时不能返回原文，否则“源文件豁免”会破坏预算边界。
    # 该文件仍可通过更小的按行窗口继续读取；本轮只保留可恢复的定位信息。
    return {
        **candidate(0),
        "start_line": offset + 1 if offset < total_lines else 0,
        "content": (
            f"Source line {offset + 1} in knowledge document {kb_id}/{file_id} exceeds the "
            f"configured inline limit ({token_limit} tokens). Retry open_kb_document with a "
            "smaller window_size or use find_kb_document to locate a narrower range."
        ),
    }


async def _resolve_visible_knowledge_bases_for_query(runtime: ToolRuntime | None) -> list[dict[str, Any]]:
    if runtime is None:
        return []

    context = getattr(runtime, "context", None)
    if context is None:
        return []

    visible_kbs = getattr(context, "_visible_knowledge_bases", None)
    if isinstance(visible_kbs, list):
        return visible_kbs

    try:
        from yuxi.agents.backends.knowledge_base_backend import resolve_visible_knowledge_bases_for_context

        return await resolve_visible_knowledge_bases_for_context(context)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"解析会话可见知识库失败: {exc}")
        return []


def _find_query_target(
    *,
    kb_id: str,
    retrievers: dict[str, Any],
    visible_kbs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if not visible_kbs:
        return None, None, "无法获取当前会话可访问的知识库"

    normalized_kb_id = str(kb_id or "").strip()
    visible_kb_ids = {str(kb.get("kb_id") or "").strip() for kb in visible_kbs}
    if normalized_kb_id not in visible_kb_ids:
        return None, None, f"知识库资源 '{normalized_kb_id}' 不存在或当前会话未启用"

    target_info = retrievers.get(normalized_kb_id)
    if target_info is None:
        return None, None, f"知识库资源 '{normalized_kb_id}' 不存在"
    return target_info, normalized_kb_id, None


async def _build_query_output(target_kb_id: str, result: Any) -> Any:
    if isinstance(result, dict) and result.get("kb_id") == target_kb_id and isinstance(result.get("results"), list):
        return SearchOutputSchema(**result).model_dump()
    return KnowledgeBase.build_search_output(target_kb_id, result)


@tool(category="knowledge", tags=["知识库"], args_schema=QueryKBInput)
async def query_kb(kb_id: str, query_text: str, file_name: str | None = None, runtime: ToolRuntime = None) -> Any:
    """在指定知识库中检索内容

    当用户需要查询具体内容时使用此工具。kb_id 是知识库资源 ID，也就是 kb_id；返回结果中的
    file_id 可继续用于 find_kb_document 或 open_kb_document。
    """
    if not kb_id:
        return "请提供 kb_id"
    if not query_text:
        return "请提供查询内容"

    knowledge_base = _get_knowledge_base()
    retrievers = knowledge_base.get_retrievers()
    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    target_info, target_kb_id, target_error = _find_query_target(
        kb_id=kb_id,
        retrievers=retrievers,
        visible_kbs=visible_kbs,
    )
    if target_error:
        return target_error

    try:
        retriever = target_info["retriever"]
        kwargs = {}
        if file_name:
            kwargs["file_name"] = file_name

        if inspect.iscoroutinefunction(retriever):
            result = await retriever(query_text, **kwargs)
        else:
            result = retriever(query_text, **kwargs)

        return await _build_query_output(target_kb_id, result)

    except Exception as e:
        logger.error(f"检索失败: {e}")
        return f"检索失败: {str(e)}"


@tool(category="knowledge", tags=["知识库"], args_schema=OpenKBDocumentInput)
async def open_kb_document(
    kb_id: str,
    file_id: str,
    line: int | None = None,
    offset: int | None = None,
    window_size: int = 1800,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """按行窗口打开知识库文档原文

    当 query_kb 返回的片段不足以回答问题，或需要查看某个文档的上下文时使用。
    kb_id 是知识库资源 ID，也就是 kb_id；file_id 是知识库文件 ID。
    """
    normalized_kb_id = str(kb_id or "").strip()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_kb_id:
        return "请提供 kb_id"
    if not normalized_file_id:
        return "请提供 file_id"

    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    if not visible_kbs:
        return "无法获取当前会话可访问的知识库"

    visible_kb_ids = {str(kb.get("kb_id") or "").strip() for kb in visible_kbs}
    if normalized_kb_id not in visible_kb_ids:
        return f"知识库资源 '{normalized_kb_id}' 不存在或当前会话未启用"

    knowledge_base = _get_knowledge_base()
    retrievers = knowledge_base.get_retrievers()
    target_info = retrievers.get(normalized_kb_id)
    if target_info is None:
        return f"知识库资源 '{normalized_kb_id}' 不存在"

    metadata = target_info.get("metadata") if isinstance(target_info, dict) else None
    kb_type = str((metadata or {}).get("kb_type") or "").strip().lower()
    if kb_type == "dify":
        return "Dify 知识库为外部只读检索源，当前不支持通过 Open 打开全文"

    try:
        start_offset = int(line) - 1 if line is not None else int(offset or 0)
        window = await knowledge_base.open_file_content(
            normalized_kb_id,
            normalized_file_id,
            offset=start_offset,
            limit=window_size,
        )
        window = _bounded_open_window(
            window,
            kb_id=normalized_kb_id,
            file_id=normalized_file_id,
            token_limit=_configured_tool_inline_limit(runtime),
        )
        return OpenOutputSchema(kb_id=normalized_kb_id, file_id=normalized_file_id, **window).model_dump()

    except Exception as e:
        logger.error(f"打开知识库文档失败: {e}")
        return f"打开知识库文档失败: {str(e)}"


@tool(category="knowledge", tags=["知识库"], args_schema=FindKBDocumentInput)
async def find_kb_document(
    kb_id: str,
    file_id: str,
    patterns: list[str],
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_windows: int = 5,
    window_size: int = 80,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """在已知知识库文件内做关键词或正则定位。

    当 query_kb 已找到候选文件，但需要在该文件内定位术语、指标、章节或实体时使用。
    """
    normalized_kb_id = str(kb_id or "").strip()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_kb_id:
        return "请提供 kb_id"
    if not normalized_file_id:
        return "请提供 file_id"
    if not patterns:
        return "请提供 patterns"

    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    if not visible_kbs:
        return "无法获取当前会话可访问的知识库"

    visible_kb_ids = {str(kb.get("kb_id") or "").strip() for kb in visible_kbs}
    if normalized_kb_id not in visible_kb_ids:
        return f"知识库资源 '{normalized_kb_id}' 不存在或当前会话未启用"

    knowledge_base = _get_knowledge_base()
    retrievers = knowledge_base.get_retrievers()
    target_info = retrievers.get(normalized_kb_id)
    if target_info is None:
        return f"知识库资源 '{normalized_kb_id}' 不存在"

    metadata = target_info.get("metadata") if isinstance(target_info, dict) else None
    kb_type = str((metadata or {}).get("kb_type") or "").strip().lower()
    if kb_type == "dify":
        return "Dify 知识库为外部只读检索源，当前不支持通过 Find 检索全文"

    try:
        result = await knowledge_base.find_file_content(
            normalized_kb_id,
            normalized_file_id,
            patterns,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_windows=max_windows,
            window_size=window_size,
        )
        return FindOutputSchema(kb_id=normalized_kb_id, file_id=normalized_file_id, **result).model_dump()
    except Exception as e:
        logger.error(f"知识库文档内检索失败: {e}")
        return f"知识库文档内检索失败: {str(e)}"


class SearchFileInput(BaseModel):
    """搜索文件输入模型"""

    kb_name: str | None = Field(default=None, description="知识库名称，为空时搜索所有知识库")
    query: str | None = Field(default=None, description="搜索关键词，为空时返回所有文件")
    offset: int = Field(default=0, ge=0, description="偏移量，从 0 开始")
    limit: int = Field(default=300, ge=1, le=5000, description="返回数量限制，默认 300")


@tool(category="knowledge", tags=["知识库"], args_schema=SearchFileInput)
async def search_file(
    kb_name: str | None = None,
    query: str | None = None,
    offset: int = 0,
    limit: int = 300,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """搜索知识库中的文件

    当用户需要查找特定文件时使用此工具。可以指定知识库名称和搜索关键词。
    如果不指定知识库，将搜索所有可访问的知识库。
    如果不指定搜索关键词，将返回所有文件。

    Args:
        kb_name: 知识库名称，为空时搜索所有知识库
        query: 搜索关键词，为空时返回所有文件
        offset: 偏移量，从 0 开始
        limit: 返回数量限制，默认 300

    Returns:
        匹配的文件列表和分页信息
    """
    if not kb_name and not query:
        return "请提供知识库名称或搜索关键词，不能同时为空"

    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    if not visible_kbs:
        return "无法获取当前会话可访问的知识库"

    if kb_name:
        target_kbs = [kb for kb in visible_kbs if kb.get("name") == kb_name]
        if not target_kbs:
            return f"知识库 '{kb_name}' 不存在或当前会话未启用"
    else:
        target_kbs = visible_kbs

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    repo = KnowledgeFileRepository()

    normalized_offset = max(int(offset or 0), 0)
    normalized_limit = min(max(int(limit or 300), 1), 5000)
    use_sql_pagination = len(target_kbs) == 1
    candidate_limit = normalized_limit if use_sql_pagination else normalized_offset + normalized_limit
    query_offset = normalized_offset if use_sql_pagination else 0

    all_files = []
    total = 0
    for kb in target_kbs:
        kb_id = kb.get("kb_id")
        if not kb_id:
            continue

        files, kb_total = await repo.search_files(
            kb_id=kb_id,
            filename_query=query,
            offset=query_offset,
            limit=candidate_limit,
            files_only=True,
        )
        total += kb_total

        for f in files:
            all_files.append(
                {
                    "kb_id": kb_id,
                    "kb_name": kb.get("name"),
                    "file_id": f.file_id,
                    "filename": f.filename,
                    "file_type": f.file_type,
                    "status": f.status,
                    "created_at": str(f.created_at) if f.created_at else None,
                    "updated_at": str(f.updated_at) if f.updated_at else None,
                    "file_size": f.file_size,
                }
            )

    if not use_sql_pagination:
        # 跨知识库时才需要在内存中按更新时间归并；单库结果已由数据库完成排序与分页。
        all_files.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    paginated_files = (
        all_files if use_sql_pagination else all_files[normalized_offset : normalized_offset + normalized_limit]
    )

    return {
        "files": paginated_files,
        "total": total,
        "offset": normalized_offset,
        "limit": normalized_limit,
        "has_more": normalized_offset + normalized_limit < total,
    }


def get_common_kb_tools() -> list:
    """获取通用知识库工具列表

    返回 6 个通用工具：
    - list_kbs: 列出用户可访问的知识库
    - get_mindmap: 获取指定知识库的思维导图
    - query_kb: 在指定知识库中检索
    - find_kb_document: 在指定文件内定位关键词或正则模式
    - open_kb_document: 按 file_id 分段打开知识库文档
    - search_file: 搜索知识库中的文件
    """
    return [list_kbs, get_mindmap, query_kb, find_kb_document, open_kb_document, search_file]
