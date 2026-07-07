from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from yuxi.agents.backends.sandbox import ensure_thread_dirs, sandbox_uploads_dir, virtual_path_for_thread_file
from yuxi.config import config as app_config
from yuxi.knowledge.parser import Parser

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


def _render_file(file_info: dict[str, Any]) -> str:
    name = _clean_text(file_info.get("name")) or "未命名附件"
    match_status = _clean_text(file_info.get("matchStatus"))
    extraction_status = _clean_text(file_info.get("extractionStatus"))
    kb_id = _clean_text(file_info.get("kbId"))
    file_id = _clean_text(file_info.get("fileId"))
    has_parsed = bool(file_info.get("hasParsedMarkdown"))
    lines = [f"- {name}"]

    summary = _summary_from_file(file_info)
    if summary:
        summary, _ = _truncate(summary, IFRAME_FILE_SUMMARY_CHARS)
        lines.extend(["  状态：已有摘要", f"  摘要：{summary}"])
    elif match_status == "multiple":
        lines.append("  状态：匹配到多个候选文件，需要先明确具体附件。")
    elif match_status == "matched" and has_parsed:
        lines.append("  状态：已解析，暂无结构化摘要。")
    elif match_status in {"pending_sync", "ingesting", "parsing"} or not (kb_id and file_id):
        lines.append("  状态：正在入库或解析，当前不能读取全文。不要猜测该附件内容；如果问题依赖它，请说明需要等待解析完成。")
    else:
        lines.append(f"  状态：{extraction_status or match_status or '未知'}")

    # 只有 KB 中已有可读 markdown 时才暴露工具指针，避免模型拿不存在的路径硬读。
    if kb_id and file_id and (summary or has_parsed):
        lines.append(f'  全文读取：open_kb_document(kb_id="{kb_id}", file_id="{file_id}")')
    return "\n".join(lines)


def _render_files(files: list[Any]) -> str:
    file_prompts = [_render_file(item) for item in files if isinstance(item, dict)]
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
        files_prompt = _render_files(files)
        if files_prompt:
            sections.append(files_prompt)

    if len(sections) <= 2:
        return ""

    prompt = "\n\n".join(sections)
    prompt, _ = _truncate(prompt, IFRAME_CONTEXT_TOTAL_CHARS)
    return prompt
