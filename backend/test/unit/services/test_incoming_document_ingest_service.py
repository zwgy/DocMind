from types import SimpleNamespace

from yuxi.services.incoming_document_ingest_service import IncomingDocumentIngestService


class FakeFileRepo:
    def __init__(self, existing=None):
        self.existing = existing or []

    async def list_by_source_key(self, source_key, source_system=None):
        return self.existing


class FakeKnowledgeBase:
    async def get_database_info(self, kb_id):
        return {"kb_id": kb_id, "name": "来文默认库"}

    async def add_file_record(self, kb_id, item, params=None, operator_id=None):
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
        file_repo=FakeFileRepo([SimpleNamespace(kb_id="kb_1", file_id="file_old")]),
        knowledge=FakeKnowledgeBase(),
        tasker=FakeTasker(),
        default_kb_id="kb_1",
    )

    result = await service.ingest_file(
        content=b"demo",
        filename="来文.docx",
        source_key="202606100417",
        operator_id="u1",
    )

    assert result == {"fileId": "file_old", "kbId": "kb_1", "taskId": None, "status": "exists"}


async def test_ingest_direct_file_adds_record_and_queues_parse(monkeypatch):
    async def fake_upload(*, kb_id, filename, content):
        return {"minio_url": "minio://knowledgebases/kb_1/upload/1.docx", "content_hash": "hash", "size": len(content)}

    tasker = FakeTasker()
    service = IncomingDocumentIngestService(
        file_repo=FakeFileRepo(),
        knowledge=FakeKnowledgeBase(),
        tasker=tasker,
        default_kb_id="kb_1",
        upload_file=fake_upload,
    )

    result = await service.ingest_file(
        content=b"demo",
        filename="来文.docx",
        source_key="202606100417",
        source_url="http://example/a?202606100417",
        operator_id="u1",
    )

    assert result == {"fileId": "file_new", "kbId": "kb_1", "taskId": "task_1", "status": "accepted"}
    assert tasker.enqueued[0]["task_type"] == "knowledge_parse"
