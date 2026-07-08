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
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)

    result = await incoming_document_router.get_incoming_document_detail(
        "inc_1",
        current_user=SimpleNamespace(uid="admin"),
    )

    assert result["incomingId"] == "inc_1"
    assert result["summary"] == "summary"
    assert result["structuredResult"] == {"subject": "Global Finance"}
    assert result["metadata"] == {"title": "demo"}
