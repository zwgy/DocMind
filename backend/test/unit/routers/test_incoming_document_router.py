from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from starlette.datastructures import FormData, UploadFile

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
        source_function_id="incomingDocument",
        source_document_id="doc_1",
        source_file_id="S001",
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
        source_function_id="incomingDocument",
        source_document_id="doc_1",
        source_file_id="S001",
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


async def test_ingest_multipart_accepts_multiple_files_with_snake_case_fields(monkeypatch):
    captured = {}

    class FakeRequest:
        headers = {"content-type": "multipart/form-data; boundary=demo"}

        async def form(self):
            return FormData(
                [
                    ("source_doc_id", "doc-001"),
                    ("source_function_id", "incomingDocument"),
                    ("document_number", "来文〔2026〕1号"),
                    ("title", "风险整改通知"),
                    ("incoming_type", "安全管理"),
                    ("source_unit", "安监部"),
                    ("incoming_date", "2026-07-09"),
                    ("source_system", "oa"),
                    (
                        "file_metas",
                        '[{"source_file_id":"file-001","filename":"来文〔2026〕1号.pdf"},'
                        '{"source_file_id":"file-002","filename":"附件1.xlsx"}]',
                    ),
                    ("files", UploadFile(filename="来文〔2026〕1号.pdf", file=BytesIO(b"main"))),
                    ("files", UploadFile(filename="附件1.xlsx", file=BytesIO(b"attachment"))),
                ]
            )

    class FakeIngestService:
        async def ingest_files(self, **kwargs):
            captured.update(kwargs)
            return {"status": "accepted", "items": []}

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentIngestService", FakeIngestService)

    result = await incoming_document_router.ingest_incoming_document(
        FakeRequest(),
        current_user=SimpleNamespace(uid="u1"),
    )

    assert result == {"status": "accepted", "items": []}
    assert captured["source_doc_id"] == "doc-001"
    assert captured["source_function_id"] == "incomingDocument"
    assert captured["document_number"] == "来文〔2026〕1号"
    assert captured["title"] == "风险整改通知"
    assert captured["incoming_type"] == "安全管理"
    assert captured["source_unit"] == "安监部"
    assert captured["incoming_date"] == "2026-07-09"
    assert captured["source_system"] == "oa"
    assert [item["source_file_id"] for item in captured["files"]] == ["file-001", "file-002"]
    assert [item["filename"] for item in captured["files"]] == ["来文〔2026〕1号.pdf", "附件1.xlsx"]
    assert [item["content"] for item in captured["files"]] == [b"main", b"attachment"]


async def test_ingest_multipart_rejects_invalid_file_metas():
    class FakeRequest:
        headers = {"content-type": "multipart/form-data; boundary=demo"}

        async def form(self):
            return FormData(
                [
                    ("source_doc_id", "doc-001"),
                    ("source_function_id", "incomingDocument"),
                    ("file_metas", "not-json"),
                    ("files", UploadFile(filename="incoming.pdf", file=BytesIO(b"main"))),
                ]
            )

    with pytest.raises(incoming_document_router.HTTPException) as exc:
        await incoming_document_router.ingest_incoming_document(
            FakeRequest(),
            current_user=SimpleNamespace(uid="u1"),
        )

    assert exc.value.status_code == 400
    assert "file_metas" in exc.value.detail


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
