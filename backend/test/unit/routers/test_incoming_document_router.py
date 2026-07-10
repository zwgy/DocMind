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
        if self.record is None:
            return None
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


def _record_with_original(filename, original_file_url):
    return SimpleNamespace(
        incoming_id="inc_1",
        filename=filename,
        original_file_url=original_file_url,
    )


def _patch_minio(monkeypatch, content):
    """把 get_minio_client 换成只返回固定字节的假实现。"""

    class FakeMinio:
        async def adownload_file(self, bucket_name, object_name):
            return content

    fake_instance = FakeMinio()
    monkeypatch.setattr(
        incoming_document_router,
        "get_minio_client",
        lambda: fake_instance,
    )
    return fake_instance


async def test_get_incoming_original_file_streams_pdf(monkeypatch):
    record = _record_with_original("incoming.pdf", "minio://docs/inc_1/incoming.pdf")
    repo = FakeIncomingRepo(record)
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)
    pdf_bytes = b"%PDF-1.4\nfake"
    _patch_minio(monkeypatch, pdf_bytes)
    # 跳过 office 转 PDF 分支
    monkeypatch.setattr(
        incoming_document_router,
        "is_office_pdf_preview_file",
        lambda path: False,
    )
    monkeypatch.setattr(
        incoming_document_router,
        "detect_media_type",
        lambda path, content: "application/pdf",
    )

    result = await incoming_document_router.get_incoming_document_original_file(
        "inc_1",
        current_user=SimpleNamespace(uid="admin"),
    )

    assert result.headers["X-Yuxi-Preview-Type"] == "pdf"
    assert "incoming.pdf" in result.headers["Content-Disposition"]
    body = b"".join([chunk async for chunk in result.body_iterator])
    assert body == pdf_bytes


async def test_get_incoming_original_file_converts_docx_to_pdf(monkeypatch):
    record = _record_with_original("incoming.docx", "minio://docs/inc_1/incoming.docx")
    repo = FakeIncomingRepo(record)
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)
    _patch_minio(monkeypatch, b"PK\x03\x04docx-bytes")
    monkeypatch.setattr(
        incoming_document_router,
        "is_office_pdf_preview_file",
        lambda path: True,
    )

    async def fake_convert(filename, content):
        return b"%PDF-1.4\nconverted"

    monkeypatch.setattr(incoming_document_router, "convert_office_to_pdf", fake_convert)

    result = await incoming_document_router.get_incoming_document_original_file(
        "inc_1",
        current_user=SimpleNamespace(uid="admin"),
    )

    assert result.headers["X-Yuxi-Preview-Type"] == "pdf"
    assert result.media_type == "application/pdf"
    assert "incoming.pdf" in result.headers["Content-Disposition"]


async def test_get_incoming_original_file_rejects_oversize(monkeypatch):
    record = _record_with_original("big.bin", "minio://docs/inc_1/big.bin")
    repo = FakeIncomingRepo(record)
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)
    _patch_minio(monkeypatch, b"\x00" * (incoming_document_router.MAX_BINARY_PREVIEW_SIZE_BYTES + 1))

    result = await incoming_document_router.get_incoming_document_original_file(
        "inc_1",
        current_user=SimpleNamespace(uid="admin"),
    )

    # 过大走 JSON 字典分支
    assert result["supported"] is False
    assert "30 MB" in result["message"]
    assert result["limit"] == incoming_document_router.MAX_BINARY_PREVIEW_SIZE_BYTES


async def test_get_incoming_original_file_returns_text_content(monkeypatch):
    record = _record_with_original("notes.txt", "minio://docs/inc_1/notes.txt")
    repo = FakeIncomingRepo(record)
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)
    _patch_minio(monkeypatch, "hello world\n".encode("utf-8"))

    # 让后端走文本分支：is_office_pdf_preview_file=False；detect_preview_type 也走 text 路径
    monkeypatch.setattr(
        incoming_document_router,
        "is_office_pdf_preview_file",
        lambda path: False,
    )

    result = await incoming_document_router.get_incoming_document_original_file(
        "inc_1",
        current_user=SimpleNamespace(uid="admin"),
    )

    # render_preview_payload 使用蛇形命名，与 file_preview 模块保持一致
    assert result["preview_type"] == "text"
    assert result["supported"] is True
    assert "hello world" in result["content"]


async def test_get_incoming_original_file_returns_unsupported_for_binary(monkeypatch):
    record = _record_with_original("archive.zip", "minio://docs/inc_1/archive.zip")
    repo = FakeIncomingRepo(record)
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)
    _patch_minio(monkeypatch, b"PK\x05\x06fakezip")
    monkeypatch.setattr(
        incoming_document_router,
        "is_office_pdf_preview_file",
        lambda path: False,
    )

    result = await incoming_document_router.get_incoming_document_original_file(
        "inc_1",
        current_user=SimpleNamespace(uid="admin"),
    )

    assert result["supported"] is False
    assert result["content"] is None
    assert result["message"]  # 应当给出"暂不支持预览"类提示


async def test_get_incoming_original_file_404_when_no_url(monkeypatch):
    record = _record_with_original("incoming.pdf", None)
    repo = FakeIncomingRepo(record)
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)

    with pytest.raises(incoming_document_router.HTTPException) as exc:
        await incoming_document_router.get_incoming_document_original_file(
            "inc_1",
            current_user=SimpleNamespace(uid="admin"),
        )

    assert exc.value.status_code == 404
    assert "原文文件尚未上传" in exc.value.detail


async def test_get_incoming_original_file_404_when_record_missing(monkeypatch):
    repo = FakeIncomingRepo(record=None)
    monkeypatch.setattr(incoming_document_router, "IncomingDocumentRepository", lambda: repo)

    with pytest.raises(incoming_document_router.HTTPException) as exc:
        await incoming_document_router.get_incoming_document_original_file(
            "inc_missing",
            current_user=SimpleNamespace(uid="admin"),
        )

    assert exc.value.status_code == 404
