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
        self.updates = []

    async def get_by_source_identity(self, source_system, source_document_id):
        return self.existing

    async def get_by_incoming_id(self, incoming_id):
        if self.existing and self.existing.incoming_id == incoming_id:
            return self.existing
        return None

    async def upsert(self, incoming_id, data):
        self.upserts.append((incoming_id, data))
        self.existing = SimpleNamespace(incoming_id=incoming_id, **data)
        return self.existing

    async def update_fields(self, incoming_id, data):
        self.updates.append((incoming_id, data))
        if self.existing and self.existing.incoming_id == incoming_id:
            for key, value in data.items():
                setattr(self.existing, key, value)
            return self.existing
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


class FakeContext:
    def __init__(self):
        self.progress = []

    async def set_progress(self, percent, message):
        self.progress.append((percent, message))


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


async def test_process_task_parses_markdown_and_saves_summary():
    async def fake_upload(*, source_system, incoming_id, filename, content):
        return {"minio_url": f"minio://docs/{incoming_id}/{filename}", "content_hash": "hash", "size": len(content)}

    async def fake_parse(source, params):
        assert source.endswith("/incoming.pdf")
        assert params["image_bucket"] == "public"
        assert params["image_prefix"].startswith("incoming/inc_")
        return "# 来文\n\n客户要求复核 Global Finance 的资质。"

    async def fake_markdown_upload(*, incoming_id, markdown):
        assert incoming_id.startswith("inc_")
        assert "Global Finance" in markdown
        return f"minio://knowledgebases/incoming/{incoming_id}/parsed.md"

    async def fake_summarize(*, filename, markdown):
        assert filename == "incoming.pdf"
        assert "客户要求复核" in markdown
        return {
            "classification": "客户审查",
            "classification_confidence": 0.86,
            "summary": "客户要求复核 Global Finance 的资质。",
            "structured_result": {"subject": "Global Finance"},
        }

    repo = FakeIncomingRepo(
        SimpleNamespace(
            incoming_id="inc_1",
            filename="incoming.pdf",
            original_file_url="minio://docs/inc_1/incoming.pdf",
        )
    )
    tasker = FakeTasker()
    service = IncomingDocumentIngestService(
        incoming_repo=repo,
        tasker=tasker,
        upload_file=fake_upload,
        parse_document=fake_parse,
        upload_markdown=fake_markdown_upload,
        summarize_document=fake_summarize,
    )

    result = await service.ingest_file(
        content=b"demo",
        filename="incoming.pdf",
        source_key="S001",
        content_hash="new-hash",
    )
    task_result = await tasker.enqueued[0]["coroutine"](FakeContext())

    assert result["status"] == "accepted"
    assert task_result == {"incoming_id": result["incomingId"], "status": "ready"}
    assert [update[1]["status"] for update in repo.updates] == ["parsing", "summarizing", "ready"]
    assert repo.existing.markdown_file_url == f"minio://knowledgebases/incoming/{result['incomingId']}/parsed.md"
    final_update = repo.updates[-1][1]
    assert final_update["classification"] == "客户审查"
    assert final_update["summary"] == "客户要求复核 Global Finance 的资质。"
    assert final_update["structured_result"] == {"subject": "Global Finance"}
    assert final_update["processing_error"] is None


async def test_process_task_marks_document_failed_when_parse_fails():
    async def fake_parse(source, params):
        raise RuntimeError("parser crashed")

    repo = FakeIncomingRepo(
        SimpleNamespace(
            incoming_id="inc_1",
            filename="incoming.pdf",
            original_file_url="minio://docs/inc_1/incoming.pdf",
        )
    )
    service = IncomingDocumentIngestService(
        incoming_repo=repo,
        tasker=FakeTasker(),
        parse_document=fake_parse,
    )

    with pytest.raises(RuntimeError, match="parser crashed"):
        await service.process_incoming_document("inc_1")

    assert repo.updates[-1][1]["status"] == "failed"
    assert repo.updates[-1][1]["processing_error"] == "parser crashed"
