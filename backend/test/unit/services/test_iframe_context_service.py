from __future__ import annotations

import pytest

from yuxi.services import iframe_context_service as svc
from yuxi.agents.backends.sandbox import paths as sandbox_paths


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
    assert 'open_kb_document(kb_id="kb1", file_id="file1")' in prompt


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
    assert "source quote 7" in prompt


@pytest.mark.asyncio
async def test_render_iframe_context_keeps_incoming_summary_evidence_and_attachment_list():
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
                    "additionalClassifications": [
                        {
                            "classification": "风险管理类",
                            "confidence": 0.91,
                            "evidence": "需复核 Global Finance 的资质。",
                        }
                    ],
                    "documentFiles": [
                        {
                            "sourceFileId": "main",
                            "filename": "client-review.pdf",
                            "isMainFile": True,
                            "status": "parsed",
                        },
                        {
                            "sourceFileId": "attachment",
                            "filename": "资质附件.xlsx",
                            "isMainFile": False,
                            "status": "parsed",
                        },
                    ],
                    "items": [
                        {
                            "item_type": "risk_item",
                            "data": {"risk_name": "资质待核验", "department": "审查部"},
                            "source_quote": "需复核 Global Finance 的资质。",
                            "evidence": [
                                {
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
    assert "有证据支持的附加分类" in prompt
    assert "风险管理类（置信度 0.91）" in prompt
    assert "结构化信息" in prompt
    assert "risk_item" in prompt
    assert "资质待核验" in prompt
    assert "Global Finance" in prompt
    assert "client-review.pdf（主文件" in prompt
    assert "资质附件.xlsx（附件" in prompt
    assert "可用 Skills 列表中的 incoming-document 技能" in prompt
    assert "使用 `read_file` 读取该 Skill 的 SKILL.md" in prompt
    assert "/home/gem/skills/incoming-document/SKILL.md" not in prompt
    assert prompt.count("incoming-document") == 1
    assert 'incoming_id="inc_1"' in prompt
    assert 'source_file_id="main"' in prompt
    assert "Phase 1" not in prompt
