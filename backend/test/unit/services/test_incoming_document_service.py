from types import SimpleNamespace

from yuxi.services.incoming_document_service import IncomingDocumentService


def incoming_record(**overrides):
    data = {
        "incoming_id": "inc_1",
        "filename": "来文.docx",
        "source_system": "oa",
        "document_number": "上铁辆〔2020〕316号",
        "title": "路用客车检修运用管理办法",
        "incoming_type": "集团公司文件",
        "source_unit": "安全科",
        "incoming_date": "2020-10-20",
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

    async def list_by_source_file_id(self, source_file_id, *, source_system, source_function_id, source_document_id):
        return self.responses.get(("source_file_id", source_system, source_function_id, source_document_id, source_file_id), [])

    async def list_by_source_url(self, source_url, *, source_system, source_function_id, source_document_id):
        return self.responses.get(("source_url", source_system, source_function_id, source_document_id, source_url), [])

    async def list_by_source_doc_id_and_filename(self, source_doc_id, filename, *, source_system, source_function_id):
        return self.responses.get(("source_doc_id_filename", source_system, source_function_id, source_doc_id, filename), [])

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
        incoming_repo=FakeIncomingRepo(
            {("source_file_id", "oa", "incomingDocument", "37906", "202606100417"): [incoming_record()]}
        ),
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
                "source_function_id": "incomingDocument",
                "source_doc_id": "37906",
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
    assert item["source_system"] == "oa"
    assert item["document_number"] == "上铁辆〔2020〕316号"
    assert item["title"] == "路用客车检修运用管理办法"
    assert item["incoming_type"] == "集团公司文件"
    assert item["source_unit"] == "安全科"
    assert item["incoming_date"] == "2020-10-20"
    assert item["classification"] == "客户审查"
    assert item["display"]["categoryLabels"]["risk_management"] == "风险管理类"
    assert item["display"]["schemaLabels"]["risk_item"] == "风险事项"
    assert item["display"]["fieldLabels"]["risk_item"]["risk_name"] == "风险事项"
    assert item["summary"] == "这是一份客户审查来文摘要。"
    assert item["runId"] == "ber_1"
    assert item["schemaIds"] == ["risk_item"]
    assert item["items"][0]["item_type"] == "risk_item"
    assert item["items"][0]["data"] == {"risk_name": "资质待核验"}
    assert item["hasMarkdown"] is True
    assert item["knowledgeImportStatus"] == "none"


async def test_query_returns_schema_display_metadata_for_regulation():
    service = IncomingDocumentService(
        incoming_repo=FakeIncomingRepo(
            {
                ("source_file_id", "oa", "incomingDocument", "37908", "202010200206"): [
                    incoming_record(classification="规章制度类")
                ]
            }
        ),
        extraction_repo=FakeExtractionRepo(
            {
                "inc_1": {
                    "run_id": "ber_1",
                    "categories": {"regulation": {"matched": True, "evidence": "摘要阶段分类：规章制度类"}},
                    "schema_ids": ["management_requirement_item"],
                    "items": [
                        {
                            "item_id": "bei_1",
                            "item_type": "management_requirement_item",
                            "data": {
                                "department": "集团公司车辆部",
                                "role": None,
                                "period_type": "长期性",
                                "requirement": "制定路用客车检修运用管理办法。",
                                "source_quote": "原文依据",
                            },
                            "source_quote": "原文依据",
                        }
                    ],
                }
            }
        ),
    )

    result = await service.query_extractions(
        [
            {
                "id": "202010200206",
                "name": "上铁辆〔2020〕316号.pdf",
                "source_file_id": "202010200206",
                "source_function_id": "incomingDocument",
                "source_doc_id": "37908",
                "source_system": "oa",
            }
        ]
    )

    display = result["items"][0]["display"]
    assert display["classificationLabel"] == "规章制度类"
    assert display["categoryLabels"]["regulation"] == "规章制度类"
    assert display["schemaLabels"]["management_requirement_item"] == "管理要求"
    assert display["fieldLabels"]["management_requirement_item"] == {
        "requirement": "管理要求",
        "department": "涉及部门",
        "role": "涉及岗位、角色",
        "period_type": "要求类型",
        "source_quote": "原文依据",
    }


async def test_query_returns_markdown_hint_when_summary_missing():
    service = IncomingDocumentService(
        incoming_repo=FakeIncomingRepo(
            {
                ("source_file_id", "production", "incomingDocument", "37906", "202606100417"): [
                    incoming_record(summary=None)
                ]
            }
        ),
        extraction_repo=FakeExtractionRepo(),
    )

    result = await service.query_extractions(
        [
            {
                "id": "202606100417",
                "name": "incoming.docx",
                "source_file_id": "202606100417",
                "source_function_id": "incomingDocument",
                "source_doc_id": "37906",
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
        incoming_repo=FakeIncomingRepo(
            {
                (
                    "source_doc_id_filename",
                    "production",
                    "incomingDocument",
                    "37906",
                    "来文.docx",
                ): [incoming_record(), incoming_record(incoming_id="inc_2")]
            }
        ),
        extraction_repo=FakeExtractionRepo(),
    )

    result = await service.query_extractions(
        [
            {"id": "a", "name": "缺失.docx", "source_file_id": "missing", "source_function_id": "incomingDocument", "source_doc_id": "37906"},
            {"id": "b", "name": "只有文件名.docx"},
            {"id": "c", "name": "来文.docx", "source_function_id": "incomingDocument", "source_doc_id": "37906"},
        ]
    )

    assert [item["matchStatus"] for item in result["items"]] == ["pending_sync", "not_found", "multiple"]
    assert [item["extractionStatus"] for item in result["items"]] == ["not_found", "not_found", "not_found"]
