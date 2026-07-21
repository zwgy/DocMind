from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from yuxi.agents.backends.sandbox import ensure_thread_dirs, sandbox_uploads_dir, virtual_path_for_thread_file
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
    parts: list[str] = []
    if isinstance(categories, dict):
        for name, value in categories.items():
            if isinstance(value, dict) and value.get("matched"):
                parts.append(str(name))
    return "\n".join(parts)


def _business_items_text(file_info: dict[str, Any]) -> str:
    items = file_info.get("items")
    if not isinstance(items, list):
        return ""
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = _clean_text(item.get("item_type")) or "unknown"
        data = item.get("data") or {}
        evidence = item.get("evidence")
        parts = [f"- {item_type}"]
        if isinstance(data, dict) and data:
            # 参考片段可能是模型概括，普通问答不把它当作原文引用；需要细节时按来源定位回读附件。
            visible_data = {key: value for key, value in data.items() if key != "source_quote"}
            if visible_data:
                parts.append(json.dumps(visible_data, ensure_ascii=False))
        if isinstance(evidence, list):
            sources = []
            for entry in evidence:
                if not isinstance(entry, dict):
                    continue
                source_file_id = _clean_text(entry.get("source_file_id"))
                file_name = _clean_text(entry.get("file_name"))
                source_location = _clean_text(entry.get("source_location"))
                source = "，".join(
                    value
                    for value in (
                        f"附件名={file_name}" if file_name else "",
                        f"位置={source_location}" if source_location else "",
                        f"source_file_id={source_file_id}" if source_file_id else "",
                    )
                    if value
                )
                if source and source not in sources:
                    sources.append(source)
            if sources:
                parts.append(f"原文定位：{' | '.join(sources)}")
        lines.append("；".join(parts))
    return "\n".join(lines)


async def _render_file(thread_id: str, uid: str, file_info: dict[str, Any]) -> str:
    name = _clean_text(file_info.get("name")) or "未命名附件"
    match_status = _clean_text(file_info.get("matchStatus"))
    extraction_status = _clean_text(file_info.get("extractionStatus"))
    kb_id = _clean_text(file_info.get("kbId") or file_info.get("linkedKbId"))
    file_id = _clean_text(file_info.get("fileId") or file_info.get("linkedFileId"))
    incoming_id = _clean_text(file_info.get("incomingId"))
    selected_source_file_id = _clean_text(file_info.get("source_file_id") or file_info.get("incomingFileId"))
    has_parsed = bool(file_info.get("hasParsedMarkdown") or file_info.get("hasMarkdown"))
    lines = [f"- {name}"]

    summary = _summary_from_file(file_info)
    if summary:
        summary, _ = _truncate(summary, IFRAME_FILE_SUMMARY_CHARS)
        lines.extend(["  状态：已有摘要", f"  摘要：{summary}"])

    classification = _clean_text(file_info.get("classificationLabel") or file_info.get("classification"))
    if classification:
        lines.append(f"  主分类：{classification}")

    additional_classifications = file_info.get("additionalClassifications")
    if isinstance(additional_classifications, list) and additional_classifications:
        lines.append("  附加分类：")
        for item in additional_classifications:
            if not isinstance(item, dict):
                continue
            classification = _clean_text(item.get("classificationLabel") or item.get("classification"))
            confidence = item.get("confidence")
            if classification:
                lines.append(f"    - {classification}（置信度 {confidence}）")

    business_items = _business_items_text(file_info)
    if business_items:
        lines.extend(["  结构化信息：", business_items])

    document_files = file_info.get("documentFiles")
    if isinstance(document_files, list) and document_files:
        lines.append("  附件清单：")
        for document_file in document_files:
            if not isinstance(document_file, dict):
                continue
            filename = _clean_text(document_file.get("filename")) or "未命名附件"
            role = "主文件" if document_file.get("isMainFile") else "附件"
            status = _clean_text(document_file.get("status")) or "未知"
            listed_source_file_id = _clean_text(document_file.get("sourceFileId"))
            lines.append(f"    - {filename}（{role}，{status}，source_file_id={listed_source_file_id}）")

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

    if incoming_id and selected_source_file_id and has_parsed:
        lines.append(
            "  原文定位参数："
            f"incoming_id={json.dumps(incoming_id, ensure_ascii=False)}，"
            f"source_file_id={json.dumps(selected_source_file_id, ensure_ascii=False)}。"
        )
    elif kb_id and file_id and (summary or has_parsed):
        lines.append(f'  全文读取：open_kb_document(kb_id="{kb_id}", file_id="{file_id}")')
    return "\n".join(lines)


async def _render_files(thread_id: str, uid: str, files: list[Any]) -> str:
    file_items = [item for item in files if isinstance(item, dict)]
    file_prompts = [await _render_file(thread_id, uid, item) for item in file_items]
    if not file_prompts:
        return ""
    lines = ["【当前来文】"]
    lines.extend(file_prompts)
    return "\n".join(lines)


async def render_iframe_context_prompt(thread_id: str, uid: str, iframe_context: dict[str, Any] | None) -> str:
    if not isinstance(iframe_context, dict):
        return ""

    sections = [
        "### iframe 页面与附件上下文",
        "用户问题可能与当前嵌入页和选中附件有关。优先依据下列摘要回答；不要编造尚未解析完成的附件内容。",
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
