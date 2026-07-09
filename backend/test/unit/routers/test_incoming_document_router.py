from datetime import datetime
from types import SimpleNamespace

import pytest

from server.routers import incoming_document_router

pytestmark = pytest.mark.asyncio


class FakeIncomingRepo:
    def __init__(self, record):
        self.record = record
        self.list_calls = []

    async def list_for_management(self, **kwargs):
        self.list_calls.append(kwargs)
        return [self.record], 1

    async def get_by_incoming_id(self, incoming_id):
        return self.record if incoming_id == self.record.incoming_id else None


async def test_list_incoming_documents_returns_management_page(monkeypatch):
    record = SimpleNamespace(
        incoming_id="inc_1",
        source_system="oa",
        source_document_id="doc_1",
        source_key="S001",
        filename="incoming.pdf",
        file_size=123,
        status="ready",
        classification="customer-review",
        classification_confidence=0.9,
        summary="summary",
        structured_result={"subject": "Global Finance"},
        processing_error=None,
        linked_kb_id=None,
        linked_file_id=None,
        knowledge_import_status="none",
        knowledge_import_task_id=None,
        knowledge_import_error=None,
        created_at=datetime(2026, 7, 8, 1, 2, 3),
        updated_at=datetime(2026, 7, 8, 1, 3, 3),
    )
    repo = FakeIncomingRepo(record)
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)

    result = await incoming_document_router.list_incoming_documents(
        page=2,
        page_size=10,
        status="ready",
        knowledge_import_status="none",
        keyword="incoming",
        current_user=SimpleNamespace(uid="admin"),
    )

    assert result["total"] == 1
    assert result["items"][0]["incomingId"] == "inc_1"
    assert result["items"][0]["sourceSystem"] == "oa"
    assert result["items"][0]["knowledgeImportStatus"] == "none"
    assert repo.list_calls == [
        {
            "page": 2,
            "page_size": 10,
            "status": "ready",
            "knowledge_import_status": "none",
            "keyword": "incoming",
            "source_system": None,
            "classification": None,
        }
    ]


async def test_get_incoming_document_detail_returns_summary(monkeypatch):
    record = SimpleNamespace(
        incoming_id="inc_1",
        source_system="oa",
        source_document_id="doc_1",
        source_key="S001",
        source_url="https://oa.example/doc_1",
        filename="incoming.pdf",
        content_hash="hash_1",
        file_size=123,
        original_file_url="minio://docs/inc_1/incoming.pdf",
        markdown_file_url="minio://parsed/inc_1.md",
        status="ready",
        classification="customer-review",
        classification_confidence=0.9,
        summary="summary",
        structured_result={"subject": "Global Finance"},
        processing_error=None,
        linked_kb_id="kb_1",
        linked_file_id="file_1",
        knowledge_import_status="indexed",
        knowledge_import_task_id="task_1",
        knowledge_import_error=None,
        metadata_json={"title": "demo"},
        created_at=datetime(2026, 7, 8, 1, 2, 3),
        updated_at=datetime(2026, 7, 8, 1, 3, 3),
    )
    repo = FakeIncomingRepo(record)
    async def fake_preview(record):
        return "## Markdown\n\n正文"

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)
    monkeypatch.setattr(incoming_document_router, "_read_incoming_markdown_preview", fake_preview)

    result = await incoming_document_router.get_incoming_document_detail(
        "inc_1",
        current_user=SimpleNamespace(uid="admin"),
    )

    assert result["incomingId"] == "inc_1"
    assert result["summary"] == "summary"
    assert result["structuredResult"] == {"subject": "Global Finance"}
    assert result["metadata"] == {"title": "demo"}
    assert result["markdownPreview"] == "## Markdown\n\n正文"


async def test_ingest_json_uses_project_incoming_fields(monkeypatch):
    captured = {}

    class FakeRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {
                "sourceUrl": "https://oa.example.test/files/risk-001.pdf",
                "sourceKey": "lw-001",
                "sourceDocId": "doc-001",
                "sourceSystem": "oa",
                "filename": "risk-001.pdf",
                "metadata": {
                    "documentNumber": "来文〔2026〕1号",
                    "title": "风险整改通知",
                    "incomingType": "安全管理",
                    "sourceUnit": "安监部",
                    "incomingDate": "2026-07-09",
                },
            }

    class FakeIngestService:
        async def ingest_source_url(self, **kwargs):
            captured.update(kwargs)
            return {"status": "accepted"}

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentIngestService", FakeIngestService)

    result = await incoming_document_router.ingest_incoming_document(
        FakeRequest(),
        current_user=SimpleNamespace(uid="u1"),
    )

    assert result == {"status": "accepted"}
    assert captured["source_url"] == "https://oa.example.test/files/risk-001.pdf"
    assert captured["filename"] == "risk-001.pdf"
    assert captured["source_key"] == "lw-001"
    assert captured["source_doc_id"] == "doc-001"
    assert captured["documentNumber"] == "来文〔2026〕1号"
    assert captured["title"] == "风险整改通知"
    assert captured["incomingType"] == "安全管理"
    assert captured["sourceUnit"] == "安监部"
    assert captured["incomingDate"] == "2026-07-09"


async def test_retry_incoming_document_delegates_to_ingest_service(monkeypatch):
    class FakeIngestService:
        async def retry_processing(self, incoming_id, *, operator_id=None):
            return {"incomingId": incoming_id, "taskId": "task_1", "status": "accepted", "operatorId": operator_id}

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentIngestService", FakeIngestService)

    result = await incoming_document_router.retry_incoming_document_processing(
        "inc_1",
        current_user=SimpleNamespace(uid="admin"),
    )

    assert result == {"incomingId": "inc_1", "taskId": "task_1", "status": "accepted", "operatorId": "admin"}
