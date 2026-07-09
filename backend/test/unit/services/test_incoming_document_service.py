from types import SimpleNamespace

from yuxi.services.incoming_document_service import IncomingDocumentService


def incoming_record(**overrides):
    data = {
        "incoming_id": "inc_1",
        "filename": "来文.docx",
        "file_size": 125952,
        "markdown_file_url": "minio://knowledgebases/incoming/inc_1.md",
        "status": "ready",
        "classification": "客户审查",
        "summary": "这是一份客户审查来文摘要。",
        "structured_result": {"risks": ["资质待核验"]},
        "knowledge_import_status": "none",
        "linked_kb_id": None,
        "linked_file_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeIncomingRepo:
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
    def __init__(self, responses=None):
        self.responses = responses or {}

    async def get_latest_by_incoming_id(self, incoming_id):
        return self.responses.get(incoming_id)


async def test_query_returns_ready_incoming_summary_for_source_file_id_match():
    service = IncomingDocumentService(
        incoming_repo=FakeIncomingRepo({("source_key", "202606100417"): [incoming_record()]}),
        extraction_repo=FakeExtractionRepo(
            {
                "inc_1": {
                    "run_id": "ber_1",
                    "categories": {"risk_management": {"matched": True, "evidence": "存在资质风险"}},
                    "schema_ids": ["risk_item"],
                    "items": [
                        {
                            "item_id": "bei_1",
                            "item_type": "risk_item",
                            "data": {"risk_name": "资质待核验"},
                            "source_quote": "需复核 Global Finance 的资质",
                        }
                    ],
                }
            }
        ),
    )

    result = await service.query_extractions(
        [
            {
                "id": "202606100417",
                "name": "来文.docx",
                "source_url": "http://example/a?202606100417",
                "source_file_id": "202606100417",
                "source_system": "oa",
            }
        ]
    )

    item = result["items"][0]
    assert item["matchStatus"] == "matched"
    assert item["reason"] == "source_file_id matched"
    assert item["processingStatus"] == "ready"
    assert item["extractionStatus"] == "ready"
    assert item["incomingId"] == "inc_1"
    assert item["classification"] == "客户审查"
    assert item["summary"] == "这是一份客户审查来文摘要。"
    assert item["runId"] == "ber_1"
    assert item["schemaIds"] == ["risk_item"]
    assert item["items"][0]["item_type"] == "risk_item"
    assert item["items"][0]["data"] == {"risk_name": "资质待核验"}
    assert item["hasMarkdown"] is True
    assert item["knowledgeImportStatus"] == "none"


async def test_query_returns_markdown_hint_when_summary_missing():
    service = IncomingDocumentService(
        incoming_repo=FakeIncomingRepo({("source_key", "202606100417"): [incoming_record(summary=None)]}),
        extraction_repo=FakeExtractionRepo(),
    )

    result = await service.query_extractions(
        [
            {
                "id": "202606100417",
                "name": "incoming.docx",
                "source_file_id": "202606100417",
            }
        ]
    )

    item = result["items"][0]
    assert item["matchStatus"] == "matched"
    assert item["processingStatus"] == "ready"
    assert item["extractionStatus"] == "ready"
    assert item["hasMarkdown"] is True
    assert item["incomingId"] == "inc_1"
    assert "kbId" not in item
    assert "fileId" not in item


async def test_query_translates_match_miss_states():
    service = IncomingDocumentService(
        incoming_repo=FakeIncomingRepo({("filename", "来文.docx"): [incoming_record(), incoming_record(incoming_id="inc_2")]}),
        extraction_repo=FakeExtractionRepo(),
    )

    result = await service.query_extractions(
        [
            {"id": "a", "name": "缺失.docx", "source_file_id": "missing"},
            {"id": "b", "name": "只有文件名.docx"},
            {"id": "c", "name": "来文.docx"},
        ]
    )

    assert [item["matchStatus"] for item in result["items"]] == ["pending_sync", "not_found", "multiple"]
    assert [item["extractionStatus"] for item in result["items"]] == ["not_found", "not_found", "not_found"]
