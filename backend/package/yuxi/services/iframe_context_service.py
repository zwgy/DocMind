from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from yuxi.agents.backends.sandbox import ensure_thread_dirs, sandbox_uploads_dir, virtual_path_for_thread_file
from yuxi.config import config as app_config
from yuxi.knowledge.parser import Parser
from yuxi.knowledge.utils.kb_utils import parse_minio_url
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.storage.minio import get_minio_client

IFRAME_PAGE_INLINE_CHARS = 8000
IFRAME_PAGE_PREVIEW_CHARS = 2000
IFRAME_FILE_SUMMARY_CHARS = 1200
IFRAME_CONTEXT_TOTAL_CHARS = 8000
TRUNCATED_NOTICE = "[已截断，更多内容请使用给定工具读取]"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _truncate(text: str, limit: int, notice: str = TRUNCATED_NOTICE) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return f"{text[:limit].rstrip()}\n{notice}", True


async def _page_markdown(page: dict[str, Any]) -> str:
    text = _clean_text(page.get("text"))
    if text:
        return text

    html = _clean_text(page.get("html"))
    if not html:
        return ""

    # 复用现有 Parser，避免 iframe 链路维护另一套 HTML 清洗规则。
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", delete=False) as temp_file:
        temp_file.write(html)
        temp_path = temp_file.name
    try:
        return await Parser.aparse(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _context_file_path(thread_id: str, uid: str) -> tuple[Path, str]:
    ensure_thread_dirs(thread_id, uid)
    host_dir = sandbox_uploads_dir(thread_id) / "iframe-context"
    host_dir.mkdir(parents=True, exist_ok=True)
    host_path = host_dir / "page.md"
    return host_path, virtual_path_for_thread_file(thread_id, host_path, uid=uid)


def _incoming_context_file_path(thread_id: str, uid: str, incoming_id: str) -> tuple[Path, str]:
    ensure_thread_dirs(thread_id, uid)
    host_dir = sandbox_uploads_dir(thread_id) / "iframe-context" / "incoming"
    host_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in incoming_id) or "incoming"
    host_path = host_dir / f"{safe_id}.md"
    return host_path, virtual_path_for_thread_file(thread_id, host_path, uid=uid)


async def _render_page(thread_id: str, uid: str, page: dict[str, Any]) -> str:
    markdown = await _page_markdown(page)
    title = _clean_text(page.get("title"))
    url = _clean_text(page.get("url"))
    if not any((title, url, markdown)):
        return ""

    lines = ["【当前网页】"]
    if title:
        lines.append(f"标题：{title}")
    if url:
        lines.append(f"地址：{url}")

    if len(markdown) > IFRAME_PAGE_INLINE_CHARS:
        host_path, virtual_path = _context_file_path(thread_id, uid)
        host_path.write_text(markdown, encoding="utf-8")
        preview, _ = _truncate(markdown, IFRAME_PAGE_PREVIEW_CHARS)
        lines.extend(
            [
                "内容预览：",
                preview,
                f"[已截断，完整网页内容请使用 read_file 读取：{virtual_path}]",
            ]
        )
    elif markdown:
        lines.extend(["内容：", markdown])

    return "\n".join(lines)


def _summary_from_file(file_info: dict[str, Any]) -> str:
    summary = _clean_text(file_info.get("summary"))
    if summary:
        return summary

    categories = file_info.get("categories")
    items = file_info.get("items")
    parts: list[str] = []
    if isinstance(categories, dict):
        for name, value in categories.items():
            if isinstance(value, dict) and value.get("matched"):
                evidence = _clean_text(value.get("evidence"))
                parts.append(f"{name}：{evidence}" if evidence else str(name))
    if isinstance(items, list):
        for item in items[:5]:
            if isinstance(item, dict):
                quote = _clean_text(item.get("source_quote"))
                if quote:
                    parts.append(quote)
    return "\n".join(parts)


def _business_items_text(file_info: dict[str, Any]) -> str:
    items = file_info.get("items")
    if not isinstance(items, list):
        return ""
    lines: list[str] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        item_type = _clean_text(item.get("item_type")) or "unknown"
        data = item.get("confirmed_data") or item.get("data") or {}
        source_quote = _clean_text(item.get("source_quote"))
        parts = [f"- {item_type}"]
        if isinstance(data, dict) and data:
            parts.append(json.dumps(data, ensure_ascii=False))
        if source_quote:
            parts.append(f"依据：{source_quote}")
        lines.append("；".join(parts))
    return "\n".join(lines)


async def _read_incoming_markdown(incoming_id: str) -> str:
    record = await IncomingDocumentRepository().get_by_incoming_id(incoming_id)
    markdown_url = getattr(record, "markdown_file_url", None) if record is not None else None
    if not markdown_url:
        return ""
    bucket_name, object_name = parse_minio_url(markdown_url)
    return (await get_minio_client().adownload_file(bucket_name, object_name)).decode("utf-8")


async def _render_file(thread_id: str, uid: str, file_info: dict[str, Any]) -> str:
    name = _clean_text(file_info.get("name")) or "未命名附件"
    match_status = _clean_text(file_info.get("matchStatus"))
    extraction_status = _clean_text(file_info.get("extractionStatus"))
    kb_id = _clean_text(file_info.get("kbId") or file_info.get("linkedKbId"))
    file_id = _clean_text(file_info.get("fileId") or file_info.get("linkedFileId"))
    incoming_id = _clean_text(file_info.get("incomingId"))
    has_parsed = bool(file_info.get("hasParsedMarkdown") or file_info.get("hasMarkdown"))
    lines = [f"- {name}"]

    summary = _summary_from_file(file_info)
    if summary:
        summary, _ = _truncate(summary, IFRAME_FILE_SUMMARY_CHARS)
        lines.extend(["  状态：已有摘要", f"  摘要：{summary}"])

    business_items = _business_items_text(file_info)
    if business_items:
        lines.extend(["  结构化信息：", business_items])

    if not summary:
        if match_status == "multiple":
            lines.append("  状态：匹配到多个候选文件，需要先明确具体附件。")
        elif match_status == "matched" and has_parsed:
            lines.append("  状态：已解析，暂无结构化摘要。")
        elif match_status in {"pending_sync", "ingesting", "parsing"} or not (kb_id or incoming_id):
            lines.append(
                "  状态：正在同步或解析，当前不能读取全文。不要猜测该附件内容；如果问题依赖它，请说明需要等待解析完成。"
            )
        else:
            lines.append(f"  状态：{extraction_status or match_status or '未知'}")

    # KB 文件走现有知识库工具；未入库来文只暴露当前 thread sandbox 内的临时 markdown 路径。
    if kb_id and file_id and (summary or has_parsed):
        lines.append(f'  全文读取：open_kb_document(kb_id="{kb_id}", file_id="{file_id}")')
    elif incoming_id and has_parsed:
        markdown = await _read_incoming_markdown(incoming_id)
        if markdown:
            host_path, virtual_path = _incoming_context_file_path(thread_id, uid, incoming_id)
            host_path.write_text(markdown, encoding="utf-8")
            lines.append(f"  全文读取：请使用 read_file 读取 {virtual_path}")
    return "\n".join(lines)


async def _render_files(thread_id: str, uid: str, files: list[Any]) -> str:
    file_prompts = [await _render_file(thread_id, uid, item) for item in files if isinstance(item, dict)]
    if not file_prompts:
        return ""
    return "【选中附件】\n" + "\n".join(file_prompts)


async def render_iframe_context_prompt(thread_id: str, uid: str, iframe_context: dict[str, Any] | None) -> str:
    if not isinstance(iframe_context, dict):
        return ""

    sections = [
        "### iframe 页面与附件上下文",
        "用户问题可能与当前嵌入页和选中附件有关。优先依据下列摘要回答；摘要不足时按给定工具读取全文。不要编造尚未解析完成的附件内容。",
    ]
    page = iframe_context.get("page")
    if isinstance(page, dict):
        page_prompt = await _render_page(thread_id, uid, page)
        if page_prompt:
            sections.append(page_prompt)

    files = iframe_context.get("files")
    if isinstance(files, list):
        files_prompt = await _render_files(thread_id, uid, files)
        if files_prompt:
            sections.append(files_prompt)

    if len(sections) <= 2:
        return ""

    prompt = "\n\n".join(sections)
    prompt, _ = _truncate(prompt, IFRAME_CONTEXT_TOTAL_CHARS)
    return prompt
