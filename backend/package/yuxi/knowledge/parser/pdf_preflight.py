"""PDF 页面树解析前置校验。"""

from __future__ import annotations

from pathlib import Path

import fitz

from yuxi.knowledge.parser.base import DocumentParserException


def validate_pdf_page_tree_loadable(file_path: str | Path) -> None:
    """确认 PDF 声明的每个页槽都能加载为页面对象。"""

    path = Path(file_path)
    try:
        document = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001
        raise DocumentParserException(
            f"PDF 文件结构异常，无法打开页面目录: {exc}",
            "pdf_preflight",
            "invalid_pdf_structure",
        ) from exc

    try:
        if document.is_encrypted or document.needs_pass:
            raise DocumentParserException(
                "PDF 文件已加密或需要密码，无法进入文档解析流程",
                "pdf_preflight",
                "encrypted_pdf",
            )

        page_count = document.page_count
        if page_count <= 0:
            raise DocumentParserException(
                "PDF 文件没有可解析页面",
                "pdf_preflight",
                "empty_pdf",
            )

        issues: list[tuple[int, str]] = []
        for page_index in range(page_count):
            try:
                page = document.load_page(page_index)
                # 仅取得对象不一定触发坏页槽解析，访问 rect 才能在进入耗时 OCR 前暴露页树异常。
                _ = page.rect
            except Exception as exc:  # noqa: BLE001
                issues.append((page_index + 1, str(exc)))

        if issues:
            visible_pages = "、".join(str(page_number) for page_number, _ in issues[:8])
            if len(issues) > 8:
                visible_pages = f"{visible_pages} 等 {len(issues)} 个"
            first_error = issues[0][1] or "页面对象无法加载"
            raise DocumentParserException(
                "PDF 页面结构异常："
                f"声明页数为 {page_count}，但第 {visible_pages} 个页槽不是可加载页面对象。"
                f"底层错误：{first_error}。请先用 Acrobat、打印为 PDF、qpdf 或 mutool 等工具重写 PDF 后再上传。",
                "pdf_preflight",
                "invalid_pdf_page_tree",
            )
    finally:
        document.close()
