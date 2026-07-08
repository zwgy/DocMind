from types import SimpleNamespace

import pytest

from yuxi.services.knowledge_document_ingest_service import KnowledgeDocumentIngestService


class FakeContext:
    def __init__(self):
        self.result = None
        self.progress = []

    async def set_message(self, message):
        return None

    async def set_progress(self, percent, message):
        self.progress.append((percent, message))

    async def set_result(self, result):
        self.result = result

    async def raise_if_cancelled(self):
        return None


class FakeKnowledge:
    def __init__(self):
        self.add_calls = []
        self.update_calls = []
        self.index_calls = []

    async def add_file_record(self, kb_id, item, params=None, operator_id=None):
        self.add_calls.append((kb_id, item, params, operator_id))
        return {"file_id": "file_1", "status": "uploaded"}

    async def parse_file(self, kb_id, file_id, operator_id=None):
        return {"file_id": file_id, "status": "parsed"}

    async def update_file_params(self, kb_id, file_id, params, operator_id=None):
        self.update_calls.append((kb_id, file_id, params, operator_id))

    async def index_file(self, kb_id, file_id, operator_id=None, params=None):
        self.index_calls.append((kb_id, file_id, params, operator_id))
        return {"file_id": file_id, "status": "indexed"}


@pytest.mark.asyncio
async def test_run_ingest_auto_indexes_and_preserves_source_path():
    item = "minio://knowledgebases/incoming/inc_1/source.pdf"
    knowledge = FakeKnowledge()
    service = KnowledgeDocumentIngestService(
        knowledge=knowledge,
        business_extraction_submitter=lambda **_kwargs: None,
    )

    result = await service.run_ingest(
        kb_id="kb_1",
        items=[item],
        params={
            "content_type": "file",
            "auto_index": True,
            "content_hashes": {item: "hash_1"},
            "file_sizes": {item: 123},
            "source_paths": {item: "incoming/customer-review/source.pdf"},
            "chunk_preset_id": "general",
            "chunk_parser_config": {"chunk_token_num": 512},
        },
        operator_id="u1",
        context=FakeContext(),
    )

    assert result["failed"] == 0
    assert result["items"] == [{"file_id": "file_1", "status": "indexed"}]
    assert knowledge.add_calls[0][2]["source_path"] == "incoming/customer-review/source.pdf"
    assert "source_paths" not in knowledge.add_calls[0][2]
    assert knowledge.update_calls == [
        ("kb_1", "file_1", {"chunk_preset_id": "general", "chunk_parser_config": {"chunk_token_num": 512}}, "u1")
    ]
    assert knowledge.index_calls == [
        ("kb_1", "file_1", {"chunk_preset_id": "general", "chunk_parser_config": {"chunk_token_num": 512}}, "u1")
    ]


class FakeTasker:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, **kwargs):
        self.enqueued.append(kwargs)
        return SimpleNamespace(id="task_1")


@pytest.mark.asyncio
async def test_enqueue_ingest_wraps_task_payload_and_callback():
    callback_results = []
    tasker = FakeTasker()
    service = KnowledgeDocumentIngestService(
        knowledge=FakeKnowledge(),
        tasker=tasker,
        business_extraction_submitter=lambda **_kwargs: None,
    )

    result = await service.enqueue_ingest(
        kb_id="kb_1",
        items=["minio://knowledgebases/incoming/inc_1/source.pdf"],
        params={"content_type": "file", "content_hashes": {"minio://knowledgebases/incoming/inc_1/source.pdf": "h"}},
        operator_id="u1",
        on_success=lambda task_result: callback_results.append(task_result),
    )

    assert result["status"] == "queued"
    assert result["task_id"] == "task_1"
    assert tasker.enqueued[0]["task_type"] == "knowledge_ingest"
    assert tasker.enqueued[0]["payload"]["kb_id"] == "kb_1"
    await tasker.enqueued[0]["coroutine"](FakeContext())
    assert callback_results[0]["failed"] == 0
