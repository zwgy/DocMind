from types import SimpleNamespace

from yuxi.services.incoming_document_service import IncomingDocumentService


class FakeIncomingRepo:
    def __init__(self, match=None, files=None):
        self.match = match
        self.files = files or []

    async def get_file_for_source(self, **_kwargs):
        return self.match

    async def list_files(self, _incoming_id):
        return self.files


class FakeExtractionRepo:
    def __init__(self, result=None):
        self.result = result

    async def get_latest_by_incoming_id(self, _incoming_id):
        return self.result


async def test_query_returns_one_document_summary_for_multiple_attachments():
    document = SimpleNamespace(
        incoming_id="inc_1",
        source_system="oa",
        status="ready",
        summary="整份来文部署专项检查，附件包含风险清单。",
        ai_classification="阶段性工作类",
        confirmed_classification=None,
        document_metadata={"title": "专项检查通知", "incoming_date": "2026-07-17"},
        knowledge_import_status="partial",
        linked_kb_id="kb_1",
    )
    main = SimpleNamespace(
        source_file_id="main",
        filename="主文件.pdf",
        is_main_file=True,
        status="parsed",
        markdown_file_url="minio://parsed/main.md",
        knowledge_import_status="indexed",
        linked_file_id="kbf_main",
    )
    attachment = SimpleNamespace(
        source_file_id="attachment",
        filename="风险清单.xlsx",
        is_main_file=False,
        status="parsed",
        markdown_file_url="minio://parsed/attachment.md",
        knowledge_import_status="none",
        linked_file_id=None,
    )
    extraction = {
        "run_id": "ber_1",
        "schema_ids": ["task_item", "risk_item"],
        "categories": {},
        "items": [{"item_type": "risk_item", "data": {"risk_name": "延期"}, "evidence": []}],
    }
    service = IncomingDocumentService(
        incoming_repo=FakeIncomingRepo((document, main), [main, attachment]),
        extraction_repo=FakeExtractionRepo(extraction),
    )

    result = await service.query_extractions(
        [
            {
                "name": "主文件.pdf",
                "source_file_id": "main",
                "source_system": "oa",
                "source_function_id": "incoming",
                "source_doc_id": "DOC-1",
            },
            {
                "name": "风险清单.xlsx",
                "source_file_id": "attachment",
                "source_system": "oa",
                "source_function_id": "incoming",
                "source_doc_id": "DOC-1",
            },
        ]
    )

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["incomingId"] == "inc_1"
    assert item["summary"].startswith("整份来文")
    assert item["classification"] == "阶段性工作类"
    assert (item["kbId"], item["fileId"], item["fileStatus"]) == ("kb_1", "kbf_main", "indexed")
    assert [file["filename"] for file in item["files"]] == ["主文件.pdf", "风险清单.xlsx"]
    assert item["hasParsedMarkdown"] is True
    assert all(file["hasParsedMarkdown"] for file in item["files"])
    assert item["items"][0]["item_type"] == "risk_item"


async def test_query_returns_pending_sync_when_attachment_is_not_ingested():
    service = IncomingDocumentService(incoming_repo=FakeIncomingRepo(), extraction_repo=FakeExtractionRepo())

    result = await service.query_extractions(
        [{"name": "附件.pdf", "source_file_id": "file-1", "source_function_id": "incoming", "source_doc_id": "DOC-1"}]
    )

    assert result["items"] == [
        {
            "incomingFileId": "file-1",
            "name": "附件.pdf",
            "source_url": None,
            "source_file_id": "file-1",
            "source_function_id": "incoming",
            "source_doc_id": "DOC-1",
            "matchStatus": "pending_sync",
            "processingStatus": "not_found",
            "extractionStatus": "not_found",
            "reason": "source_file_id not found",
        }
    ]


async def test_query_hides_previous_extraction_while_document_is_not_ready():
    document = SimpleNamespace(
        incoming_id="inc_1",
        source_system="oa",
        status="failed",
        summary=None,
        ai_classification=None,
        confirmed_classification=None,
        document_metadata={},
        knowledge_import_status="none",
        linked_kb_id=None,
    )
    file = SimpleNamespace(
        source_file_id="main",
        filename="主文件.pdf",
        is_main_file=True,
        status="failed",
        markdown_file_url=None,
    )
    service = IncomingDocumentService(
        incoming_repo=FakeIncomingRepo((document, file), [file]),
        extraction_repo=FakeExtractionRepo({"run_id": "old", "items": [{"item_type": "risk_item"}]}),
    )

    result = await service.query_extractions(
        [{"name": "主文件.pdf", "source_file_id": "main", "source_function_id": "incoming", "source_doc_id": "DOC-1"}]
    )

    assert result["items"][0]["runId"] is None
    assert result["items"][0]["items"] == []
