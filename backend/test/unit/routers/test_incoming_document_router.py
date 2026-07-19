import json
from types import SimpleNamespace

import pytest

from server.routers import incoming_document_router


def test_parse_document_metadata_requires_object_and_accepts_new_contract():
    assert incoming_document_router._parse_document_metadata('{"title":"专项检查"}') == {"title": "专项检查"}
    with pytest.raises(ValueError, match="document_metadata"):
        incoming_document_router._parse_document_metadata("[]")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        incoming_document_router._parse_document_metadata('{"incoming_date":"2026-02-30"}')


def test_parse_file_metas_reads_main_file_marker():
    metas = incoming_document_router._parse_file_metas(
        json.dumps(
            [
                {"source_file_id": "main", "filename": "主文件.pdf", "is_main_file": True},
                {"source_file_id": "attachment", "filename": "附件.xlsx"},
            ]
        ),
        2,
    )
    assert [meta.is_main_file for meta in metas] == [True, None]


async def test_management_list_normalizes_classification_label(monkeypatch):
    captured = {}

    class FakeRepo:
        async def list_for_management(self, **kwargs):
            captured.update(kwargs)
            return [], 0

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", FakeRepo)

    result = await incoming_document_router.list_incoming_documents(
        classification="风险管理类",
        current_user=SimpleNamespace(),
    )

    assert result == {"items": [], "total": 0}
    assert captured["classification"] == "risk_management"


async def test_get_detail_returns_document_and_attachment_list(monkeypatch):
    document = SimpleNamespace(
        incoming_id="inc_1",
        source_system="oa",
        source_function_id="incoming",
        source_document_id="DOC-1",
        document_metadata={"title": "专项检查"},
        status="ready",
        ai_classification="staged_work",
        confirmed_classification=None,
        classification_confidence=0.8,
        review_status="draft",
        processing_error=None,
        linked_kb_id=None,
        knowledge_import_status="none",
        knowledge_import_task_id=None,
        knowledge_import_error=None,
        created_at=None,
        updated_at=None,
        summary="来文整体摘要",
    )
    file = SimpleNamespace(
        incoming_file_id="incf_1",
        source_file_id="main",
        filename="主文件.pdf",
        is_main_file=True,
        file_size=10,
        status="parsed",
        processing_error=None,
        original_file_url="minio://documents/main.pdf",
        markdown_file_url="minio://parsed/main.md",
        knowledge_import_status="none",
        knowledge_import_error=None,
        linked_file_id=None,
    )

    class FakeRepo:
        async def get_by_incoming_id(self, _incoming_id):
            return document

        async def list_files(self, _incoming_id):
            return [file]

    class FakeExtractionRepo:
        async def get_latest_by_incoming_id(self, _incoming_id):
            return {"run_id": "ber_1", "schema_ids": ["task_item"], "categories": {}, "items": []}

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", FakeRepo)
    monkeypatch.setattr(incoming_document_router, "DocumentBusinessExtractionRepository", FakeExtractionRepo)

    result = await incoming_document_router.get_incoming_document_detail("inc_1", current_user=SimpleNamespace())

    assert result["title"] == "专项检查"
    assert result["effectiveClassification"] == "staged_work"
    assert result["effectiveClassificationLabel"] == "阶段性工作类"
    assert result["files"] == [
        {
            "incomingFileId": "incf_1",
            "sourceFileId": "main",
            "filename": "主文件.pdf",
            "isMainFile": True,
            "fileSize": 10,
            "status": "parsed",
            "processingError": None,
            "hasOriginalFile": True,
            "hasMarkdownFile": True,
            "linkedFileId": None,
            "knowledgeImportStatus": "none",
            "knowledgeImportError": None,
        }
    ]


async def test_original_preview_rejects_unknown_attachment_instead_of_falling_back(monkeypatch):
    document = SimpleNamespace(incoming_id="inc_1")
    main = SimpleNamespace(source_file_id="main", is_main_file=True, original_file_url="minio://incoming/main.pdf")

    class FakeRepo:
        async def get_by_incoming_id(self, _incoming_id):
            return document

        async def list_files(self, _incoming_id):
            return [main]

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", FakeRepo)

    with pytest.raises(incoming_document_router.HTTPException) as exc_info:
        await incoming_document_router.get_incoming_document_original_file(
            "inc_1", source_file_id="missing", current_user=SimpleNamespace()
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "来文附件不存在"


async def test_markdown_preview_reports_truncation(monkeypatch):
    file = SimpleNamespace(source_file_id="main", markdown_file_url="minio://knowledgebases/incoming/main.md")

    class FakeRepo:
        async def list_files(self, _incoming_id):
            return [file]

    async def fake_download_text(_url):
        return "完整内容" * 10

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", FakeRepo)
    monkeypatch.setattr(
        incoming_document_router.IncomingDocumentMarkdownService,
        "download_text",
        staticmethod(fake_download_text),
    )
    monkeypatch.setattr(incoming_document_router, "INCOMING_MARKDOWN_PREVIEW_CHARS", 8)

    result = await incoming_document_router.get_incoming_document_markdown(
        "inc_1", "main", current_user=SimpleNamespace()
    )

    assert result == {"content": "完整内容完整内容", "truncated": True, "limit": 8}


async def test_classification_correction_and_confirmation_delegate_to_service(monkeypatch):
    calls = []

    class FakeIngestService:
        async def correct_classification(self, incoming_id, *, classification, operator_id):
            calls.append(("correct", incoming_id, classification, operator_id))
            return {"incomingId": incoming_id}

        async def confirm_document(self, incoming_id, *, operator_id):
            calls.append(("confirm", incoming_id, operator_id))
            return {"incomingId": incoming_id, "reviewStatus": "confirmed"}

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentIngestService", FakeIngestService)
    user = SimpleNamespace(uid="admin")
    corrected = await incoming_document_router.correct_incoming_document_classification(
        "inc_1",
        incoming_document_router.IncomingClassificationRequest(classification="阶段性工作类"),
        current_user=user,
    )
    confirmed = await incoming_document_router.confirm_incoming_document("inc_1", current_user=user)

    assert corrected == {"incomingId": "inc_1"}
    assert confirmed["reviewStatus"] == "confirmed"
    assert calls == [("correct", "inc_1", "阶段性工作类", "admin"), ("confirm", "inc_1", "admin")]


async def test_knowledge_import_delegates_selected_attachment_ids(monkeypatch):
    calls = []

    class FakeIngestService:
        async def import_to_knowledge(self, incoming_id, **kwargs):
            calls.append((incoming_id, kwargs))
            return {"incomingId": incoming_id, "status": "queued"}

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentIngestService", FakeIngestService)
    payload = incoming_document_router.IncomingKnowledgeImportRequest.model_validate(
        {"kbId": "kb_1", "sourceFileIds": ["attachment"]}
    )

    result = await incoming_document_router.import_incoming_document_to_knowledge(
        "inc_1", payload, current_user=SimpleNamespace(uid="admin")
    )

    assert result["status"] == "queued"
    assert calls[0][1]["source_file_ids"] == ["attachment"]
