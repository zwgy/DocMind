from __future__ import annotations

import pytest

from yuxi.services import iframe_context_service as svc
from yuxi.agents.backends.sandbox import paths as sandbox_paths


def test_business_items_keeps_type_for_mixed_schemas():
    heading, text = svc._business_items_text(
        {
            "display": {
                "schemaLabels": {"risk_item": "风险事项", "general_item": "通用事项"},
                "fieldLabels": {
                    "risk_item": {"risk_name": "风险名称"},
                    "general_item": {"content": "内容"},
                },
            },
            "items": [
                {"item_type": "risk_item", "data": {"risk_name": "超期"}},
                {"item_type": "general_item", "data": {"content": "补充说明"}},
            ],
        }
    )

    assert heading == "结构化信息"
    assert "1. 风险事项：风险名称：超期" in text
    assert "2. 通用事项：内容：补充说明" in text


@pytest.mark.asyncio
async def test_render_iframe_context_inlines_short_page_and_kb_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))

    prompt = await svc.render_iframe_context_prompt(
        thread_id="thread-1",
        uid="user-1",
        iframe_context={
            "page": {"title": "Detail page", "url": "https://oa.example.test/doc/1", "text": "Page body"},
            "files": [
                {
                    "name": "contract.docx",
                    "matchStatus": "matched",
                    "extractionStatus": "ready",
                    "hasParsedMarkdown": True,
                    "kbId": "kb1",
                    "fileId": "file1",
                    "summary": "Risk summary",
                }
            ],
        },
    )

    assert "Detail page" in prompt
    assert "Page body" in prompt
    assert "Risk summary" in prompt
    assert '知识库文档定位参数：kb_id="kb1"，file_id="file1"。' in prompt
    assert "open_kb_document" not in prompt


@pytest.mark.asyncio
async def test_render_iframe_context_writes_long_page_to_thread_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    monkeypatch.setattr(svc, "IFRAME_PAGE_INLINE_CHARS", 20)
    monkeypatch.setattr(svc, "IFRAME_PAGE_PREVIEW_CHARS", 10)

    prompt = await svc.render_iframe_context_prompt(
        thread_id="thread-1",
        uid="user-1",
        iframe_context={"page": {"title": "Long page", "text": "abcdefghijklmnopqrstuvwxyz"}},
    )

    host_path = tmp_path / "threads" / "thread-1" / "user-data" / "uploads" / "iframe-context" / "page.md"
    assert host_path.read_text(encoding="utf-8") == "abcdefghijklmnopqrstuvwxyz"
    assert "/home/gem/user-data/uploads/iframe-context/page.md" in prompt
    assert "已截断" in prompt


@pytest.mark.asyncio
async def test_render_iframe_context_marks_unready_files_without_tool_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))

    prompt = await svc.render_iframe_context_prompt(
        thread_id="thread-1",
        uid="user-1",
        iframe_context={
            "files": [
                {
                    "name": "pending.docx",
                    "matchStatus": "pending_sync",
                    "extractionStatus": "not_found",
                }
            ]
        },
    )

    assert "pending.docx" in prompt
    assert "不要猜测" in prompt
    assert "open_kb_document" not in prompt


@pytest.mark.asyncio
async def test_render_iframe_context_keeps_business_items_until_total_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    monkeypatch.setattr(svc, "IFRAME_CONTEXT_TOTAL_CHARS", 4000)

    prompt = await svc.render_iframe_context_prompt(
        thread_id="thread-1",
        uid="user-1",
        iframe_context={
            "files": [
                {
                    "name": "requirements.pdf",
                    "matchStatus": "matched",
                    "extractionStatus": "ready",
                    "hasParsedMarkdown": True,
                    "kbId": "kb1",
                    "fileId": "file1",
                    "items": [
                        {
                            "item_type": "management_requirement_item",
                            "data": {"requirement": f"requirement-{index}"},
                            "source_quote": f"source quote {index}",
                        }
                        for index in range(8)
                    ],
                }
            ]
        },
    )

    assert "requirement-0" in prompt
    assert "requirement-7" in prompt
    assert "source quote 7" not in prompt


@pytest.mark.asyncio
async def test_render_iframe_context_keeps_source_file_id_without_injecting_evidence_text():
    prompt = await svc.render_iframe_context_prompt(
        thread_id="thread-1",
        uid="user-1",
        iframe_context={
            "files": [
                {
                    "name": "client-review.pdf",
                    "incomingId": "inc_1",
                    "source_file_id": "main",
                    "matchStatus": "matched",
                    "processingStatus": "ready",
                    "extractionStatus": "ready",
                    "hasMarkdown": True,
                    "summary": "客户审查摘要",
                    "classificationLabel": "考评类",
                    "incoming_type": "审查文件",
                    "source_unit": "审查部",
                    "incoming_date": "2026-07-21",
                    "display": {
                        "schemaLabels": {"risk_item": "风险事项"},
                        "fieldLabels": {
                            "risk_item": {"risk_name": "风险名称", "department": "责任部门", "period_type": "周期类型"}
                        },
                    },
                    "items": [
                        {
                            "item_type": "risk_item",
                            "data": {
                                "risk_name": "资质待核验",
                                "department": "审查部",
                                "period_type": "未明确",
                                "source_quote": "需复核 Global Finance 的资质。",
                            },
                            "source_quote": "需复核 Global Finance 的资质。",
                            "evidence": [
                                {
                                    "source_file_id": "main",
                                    "file_name": "client-review.pdf",
                                    "source_location": "全文",
                                },
                                {
                                    "source_file_id": "attachment",
                                    "file_name": "资质附件.xlsx",
                                    "source_location": "分块 2",
                                    "quote": "需复核 Global Finance 的资质。",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )

    assert "客户审查摘要" in prompt
    assert "分类：考评类" in prompt
    assert "来文类型：审查文件；发文单位：审查部；时间：2026-07-21" in prompt
    assert "##### 风险事项（附件）" in prompt
    assert "1. 风险名称：资质待核验；责任部门：审查部" in prompt
    assert "周期类型" not in prompt
    assert "资质待核验" in prompt
    assert "Global Finance" not in prompt
    assert "位置=全文" not in prompt
    assert "来源：来源附件=资质附件.xlsx（source_file_id=attachment）；位置=分块 2" in prompt
    assert "附件清单" not in prompt
    assert "incoming-document" not in prompt
    assert "SKILL.md" not in prompt
    assert "incoming_id=inc_1" in prompt
    assert "##### 附件：client-review.pdf（source_file_id=main）" in prompt
    assert "Phase 1" not in prompt


@pytest.mark.asyncio
async def test_render_iframe_context_groups_selected_files_from_one_incoming_document():
    prompt = await svc.render_iframe_context_prompt(
        thread_id="thread-1",
        uid="user-1",
        iframe_context={
            "files": [
                {
                    "name": "main.docx",
                    "incomingId": "inc_1",
                    "source_file_id": "main",
                    "documentFiles": [
                        {"sourceFileId": "main", "filename": "main.docx", "isMainFile": True, "status": "parsed"},
                        {
                            "sourceFileId": "attachment",
                            "filename": "attachment.pdf",
                            "isMainFile": False,
                            "status": "parsed",
                        },
                    ],
                    "selectedFiles": [
                        {
                            "name": "main.docx",
                            "incomingId": "inc_1",
                            "source_file_id": "main",
                            "is_main_file": True,
                            "summary": "主附件摘要",
                            "display": {"schemaLabels": {"general_item": "通用事项"}},
                            "items": [{"item_type": "general_item", "data": {"content": "主附件事项"}}],
                        },
                        {
                            "name": "attachment.pdf",
                            "incomingId": "inc_1",
                            "source_file_id": "attachment",
                            "summary": "副附件摘要",
                        },
                    ],
                },
            ]
        },
    )

    assert prompt.count("#### 来文：main.docx") == 1
    assert "附件清单：" not in prompt
    assert prompt.count("##### 主附件：main.docx") == 1
    assert prompt.count("##### 附件：attachment.pdf") == 1
    assert "主附件摘要" in prompt
    assert "副附件摘要" in prompt
    assert "##### 通用事项（主附件）" in prompt
    assert "1. content：主附件事项" in prompt
    assert prompt.index("副附件摘要") < prompt.index("主附件事项")


@pytest.mark.asyncio
async def test_render_iframe_context_separates_different_incoming_documents():
    prompt = await svc.render_iframe_context_prompt(
        thread_id="thread-1",
        uid="user-1",
        iframe_context={
            "files": [
                {"name": "first.docx", "documentTitle": "来文一", "incomingId": "inc_1"},
                {"name": "second.docx", "documentTitle": "来文二", "incomingId": "inc_2"},
            ]
        },
    )

    assert "#### 来文：来文一" in prompt
    assert "#### 来文：来文二" in prompt
    assert "\n\n---\n\n" in prompt
