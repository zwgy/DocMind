from types import SimpleNamespace

import pytest

from yuxi.services.incoming_document_ingest_service import IncomingDocumentIngestService


class FakeFileRepo:
    def __init__(self, existing=None):
        self.existing = existing or []

    async def list_by_source_key(self, source_key, source_system=None):
        return self.existing


class FakeIncomingRepo:
    def __init__(self, existing=None):
        self.existing = existing
        self.upserts = []

    async def get_by_source_identity(self, source_system, source_document_id):
        return self.existing

    async def upsert(self, incoming_id, data):
        self.upserts.append((incoming_id, data))
        return SimpleNamespace(incoming_id=incoming_id, **data)


class FakeKnowledgeBase:
    def __init__(self):
        self.add_calls = []

    async def get_database_info(self, kb_id):
        return {"kb_id": kb_id, "name": "来文默认库"}

    async def add_file_record(self, kb_id, item, params=None, operator_id=None):
        self.add_calls.append((kb_id, item, params, operator_id))
        return {"kb_id": kb_id, "file_id": "file_new", "status": "uploaded", "processing_params": params}


class FakeTask:
    id = "task_1"


class FakeTasker:
    def __init__(self):
        self.enqueued = []

    async def enqueue_unique_by_payload(self, **kwargs):
        self.enqueued.append(kwargs)
        return FakeTask(), True


async def test_ingest_direct_file_reuses_existing_source_key():
    service = IncomingDocumentIngestService(
        incoming_repo=FakeIncomingRepo(
            SimpleNamespace(incoming_id="inc_old", content_hash="hash", knowledge_import_status="none")
        ),
        knowledge=FakeKnowledgeBase(),
        tasker=FakeTasker(),
    )

    result = await service.ingest_file(
        content=b"demo",
        filename="incoming.docx",
        source_key="202606100417",
        content_hash="hash",
        operator_id="u1",
    )

    assert result == {"incomingId": "inc_old", "taskId": None, "status": "exists", "knowledgeImportStatus": "none"}


async def test_ingest_direct_file_adds_record_and_queues_parse(monkeypatch):
    async def fake_upload(*, source_system, incoming_id, filename, content):
        return {"minio_url": f"minio://incoming/{incoming_id}/{filename}", "content_hash": "hash", "size": len(content)}

    tasker = FakeTasker()
    service = IncomingDocumentIngestService(
        incoming_repo=FakeIncomingRepo(),
        knowledge=FakeKnowledgeBase(),
        tasker=tasker,
        upload_file=fake_upload,
    )

    result = await service.ingest_file(
        content=b"demo",
        filename="incoming.docx",
        source_key="202606100417",
        source_url="http://example/a?202606100417",
        operator_id="u1",
    )

    assert result["incomingId"].startswith("inc_")
    assert result["taskId"] == "task_1"
    assert result["status"] == "accepted"
    assert result["knowledgeImportStatus"] == "none"
    assert tasker.enqueued[0]["task_type"] == "incoming_document_process"


async def test_ingest_direct_file_records_incoming_without_default_kb():
    async def fake_upload(*, source_system, incoming_id, filename, content):
        return {"minio_url": f"minio://incoming/{incoming_id}/{filename}", "content_hash": "hash", "size": len(content)}

    knowledge = FakeKnowledgeBase()
    repo = FakeIncomingRepo()
    tasker = FakeTasker()
    service = IncomingDocumentIngestService(
        incoming_repo=repo,
        knowledge=knowledge,
        tasker=tasker,
        default_kb_id=None,
        upload_file=fake_upload,
    )

    result = await service.ingest_file(
        content=b"demo",
        filename="incoming.docx",
        source_key="S001",
        source_system="oa",
        operator_id="u1",
    )

    assert result["incomingId"].startswith("inc_")
    assert result["status"] == "accepted"
    assert result["knowledgeImportStatus"] == "none"
    assert repo.upserts[0][1]["original_file_url"] == f"minio://incoming/{result['incomingId']}/incoming.docx"
    assert repo.upserts[0][1]["source_document_id"] == "S001"
    assert knowledge.add_calls == []
    assert tasker.enqueued[0]["task_type"] == "incoming_document_process"


@pytest.mark.asyncio
async def test_ingest_source_url_downloads_document_with_document_limits(monkeypatch):
    captured = {}

    async def fake_fetch_url_content(url, **kwargs):
        captured["fetch"] = {"url": url, **kwargs}
        return b"demo", url

    async def fake_upload(*, source_system, incoming_id, filename, content):
        return {"minio_url": f"minio://incoming/{incoming_id}/{filename}", "content_hash": "hash", "size": len(content)}

    class Context:
        async def set_progress(self, *_args):
            return None

    monkeypatch.setattr("yuxi.knowledge.utils.url_fetcher.fetch_url_content", fake_fetch_url_content)
    tasker = FakeTasker()
    service = IncomingDocumentIngestService(
        incoming_repo=FakeIncomingRepo(),
        knowledge=FakeKnowledgeBase(),
        tasker=tasker,
        upload_file=fake_upload,
    )

    result = await service.ingest_source_url(
        source_url="https://oa.example.test/incoming.pdf",
        filename="incoming.pdf",
        source_key="S001",
        operator_id="u1",
    )

    assert result["incomingId"].startswith("inc_")
    assert result["taskId"] == "task_1"
    assert result["status"] == "accepted"
    assert result["knowledgeImportStatus"] == "none"
    await tasker.enqueued[0]["coroutine"](Context())
    assert captured["fetch"]["url"] == "https://oa.example.test/incoming.pdf"
    assert "application/pdf" in captured["fetch"]["allowed_content_types"]
    assert captured["fetch"]["max_size"] >= len(b"demo")
