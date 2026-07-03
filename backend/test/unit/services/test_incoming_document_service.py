from types import SimpleNamespace

from yuxi.services.incoming_document_service import IncomingDocumentService


def file_record(**overrides):
    data = {
        "kb_id": "kb_1",
        "file_id": "file_1",
        "filename": "来文.docx",
        "file_size": 125952,
        "markdown_file": "minio://knowledgebases/kb_1/parsed/file_1.md",
        "processing_params": {"source_key": "202606100417", "source_url": "http://example/a?202606100417"},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeFileRepo:
    def __init__(self, responses):
        self.responses = responses

    async def list_by_source_key(self, source_key, source_system=None):
        return self.responses.get(("source_key", source_key), [])

    async def list_by_source_url(self, source_url, source_system=None):
        return self.responses.get(("source_url", source_url), [])

    async def list_by_source_doc_id_and_filename(self, source_doc_id, filename, source_system=None):
        return self.responses.get(("source_doc_id_filename", source_doc_id, filename), [])

    async def list_by_filename_and_size(self, filename, file_size):
        return self.responses.get(("filename_size", filename, file_size), [])

    async def list_by_filename(self, filename):
        return self.responses.get(("filename", filename), [])


class FakeExtractionRepo:
    def __init__(self, latest=None, run=None):
        self.latest = latest
        self.run = run

    async def get_latest_success_view_by_file_id(self, file_id, markdown_file=None):
        return self.latest

    async def get_latest_run_by_file_id(self, file_id, markdown_file=None):
        return self.run


class FakeTasker:
    async def find_task_by_payload(self, **kwargs):
        return None


async def test_query_returns_ready_for_source_key_match_and_draft_result():
    service = IncomingDocumentService(
        file_repo=FakeFileRepo({("source_key", "202606100417"): [file_record()]}),
        extraction_repo=FakeExtractionRepo(
            latest={
                "run_id": "ber_1",
                "categories": {"risk_management": {"matched": True}},
                "items": [{"item_id": "bei_1", "chunk_id": None, "item_type": "risk_item"}],
                "status": "draft",
            }
        ),
        tasker=FakeTasker(),
        model_spec="model-a",
    )

    result = await service.query_extractions(
        [
            {
                "id": "202606100417",
                "name": "来文.docx",
                "sourceUrl": "http://example/a?202606100417",
                "sourceKey": "202606100417",
            }
        ]
    )

    item = result["items"][0]
    assert item["matchStatus"] == "matched"
    assert item["extractionStatus"] == "ready"
    assert item["runId"] == "ber_1"
    assert item["items"][0]["chunk_id"] is None


async def test_query_translates_match_miss_states():
    service = IncomingDocumentService(
        file_repo=FakeFileRepo({("filename", "来文.docx"): [file_record(), file_record(file_id="file_2")]}),
        extraction_repo=FakeExtractionRepo(),
        tasker=FakeTasker(),
        model_spec="model-a",
    )

    result = await service.query_extractions(
        [
            {"id": "a", "name": "缺失.docx", "sourceKey": "missing"},
            {"id": "b", "name": "只有文件名.docx"},
            {"id": "c", "name": "来文.docx"},
        ]
    )

    assert [item["matchStatus"] for item in result["items"]] == ["pending_sync", "not_found", "multiple"]
    assert [item["extractionStatus"] for item in result["items"]] == ["not_found", "not_found", "not_found"]
