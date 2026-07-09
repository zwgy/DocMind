from __future__ import annotations

import pytest

from yuxi.services import iframe_context_service as svc


@pytest.mark.asyncio
async def test_render_iframe_context_inlines_short_page_and_kb_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr(svc.app_config, "save_dir", str(tmp_path))

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
    monkeypatch.setattr(svc.app_config, "save_dir", str(tmp_path))
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
    monkeypatch.setattr(svc.app_config, "save_dir", str(tmp_path))

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
async def test_render_iframe_context_writes_incoming_markdown_to_thread_file(tmp_path, monkeypatch):
    monkeypatch.setattr(svc.app_config, "save_dir", str(tmp_path))

    async def fake_read_incoming_markdown(incoming_id):
        assert incoming_id == "inc_1"
        return "# 完整来文\n\n客户要求复核 Global Finance 的资质。"

    monkeypatch.setattr(svc, "_read_incoming_markdown", fake_read_incoming_markdown)

    prompt = await svc.render_iframe_context_prompt(
        thread_id="thread-1",
        uid="user-1",
        iframe_context={
            "files": [
                {
                    "name": "client-review.pdf",
                    "incomingId": "inc_1",
                    "matchStatus": "matched",
                    "processingStatus": "ready",
                    "extractionStatus": "ready",
                    "hasMarkdown": True,
                    "summary": "客户审查摘要",
                    "items": [
                        {
                            "item_type": "risk_item",
                            "data": {"risk_name": "资质待核验", "department": "审查部"},
                            "source_quote": "需复核 Global Finance 的资质。",
                        }
                    ],
                }
            ]
        },
    )

    host_path = tmp_path / "threads" / "thread-1" / "user-data" / "uploads" / "iframe-context" / "incoming" / "inc_1.md"
    assert host_path.read_text(encoding="utf-8") == "# 完整来文\n\n客户要求复核 Global Finance 的资质。"
    assert "客户审查摘要" in prompt
    assert "结构化信息" in prompt
    assert "risk_item" in prompt
    assert "资质待核验" in prompt
    assert "Global Finance" in prompt
    assert "/home/gem/user-data/uploads/iframe-context/incoming/inc_1.md" in prompt
    assert "read_file" in prompt
