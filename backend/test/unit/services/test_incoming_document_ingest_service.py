import hashlib
from types import SimpleNamespace

import pytest

from yuxi.services import incoming_document_ingest_service as ingest_module
from yuxi.services.incoming_document_ingest_service import IncomingDocumentIngestService


@pytest.fixture(autouse=True)
def fixed_document_input_limit(monkeypatch):
    monkeypatch.setattr(ingest_module, "document_input_token_limit", lambda _model_spec: 20_000)


class FakeTask:
    id = "task_1"


class FakeTasker:
    def __init__(self):
        self.enqueued = []

    async def enqueue_unique_by_payload(self, **kwargs):
        self.enqueued.append(kwargs)
        return FakeTask(), True


class FakeIncomingRepo:
    def __init__(self):
        self.document = None
        self.files = []
        self.document_updates = []
        self.file_updates = []

    async def upsert_document(self, incoming_id, data):
        if self.document is None:
            self.document = SimpleNamespace(incoming_id=incoming_id, **data)
        else:
            for key, value in data.items():
                setattr(self.document, key, value)
        return self.document

    async def update_document(self, incoming_id, data):
        assert self.document.incoming_id == incoming_id
        self.document_updates.append(data)
        for key, value in data.items():
            setattr(self.document, key, value)
        return self.document

    async def get_by_incoming_id(self, incoming_id):
        return self.document if self.document and self.document.incoming_id == incoming_id else None

    async def list_files(self, incoming_id):
        return [file for file in self.files if file.incoming_id == incoming_id]

    async def upsert_file(self, incoming_id, incoming_file_id, data):
        file = next((item for item in self.files if item.source_file_id == data["source_file_id"]), None)
        if file is None:
            file = SimpleNamespace(incoming_id=incoming_id, incoming_file_id=incoming_file_id, **data)
            self.files.append(file)
        else:
            for key, value in data.items():
                setattr(file, key, value)
        return file

    async def update_file(self, incoming_file_id, data):
        file = next(item for item in self.files if item.incoming_file_id == incoming_file_id)
        self.file_updates.append((incoming_file_id, data))
        for key, value in data.items():
            setattr(file, key, value)
        return file

    async def set_main_file(self, incoming_id, source_file_id):
        for file in self.files:
            if file.incoming_id == incoming_id:
                file.is_main_file = file.source_file_id == source_file_id


class FakeBusinessExtractionService:
    def __init__(self):
        self.calls = []

    async def run_incoming_document_extraction(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "ber_1", "item_count": 2, "errors": []}


class FakeContext:
    async def set_progress(self, *_args):
        return None


class FakeKnowledgeIngest:
    def __init__(self, file_ids=None, enqueue_error=None):
        self.file_ids = file_ids or []
        self.enqueue_error = enqueue_error
        self.calls = []

    async def ensure_database_supports_documents(self, *_args):
        return None

    async def enqueue_ingest(self, **kwargs):
        self.calls.append(kwargs)
        if self.enqueue_error:
            raise self.enqueue_error
        await kwargs["on_success"]({"items": [{"file_id": file_id} for file_id in self.file_ids]})
        return {"task_id": "knowledge_task_1"}


async def test_ingest_creates_one_document_and_multiple_files_then_queues_one_task():
    async def fake_upload(*, filename, **_kwargs):
        return {"minio_url": f"minio://documents/{filename}", "content_hash": filename, "size": 10}

    repo = FakeIncomingRepo()
    tasker = FakeTasker()
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=tasker, upload_file=fake_upload)

    result = await service.ingest_files(
        source_system="oa",
        source_function_id="incoming",
        source_doc_id="DOC-1",
        document_metadata={"title": "专项检查通知", "incoming_date": "2026-07-17"},
        files=[
            {"source_file_id": "main", "filename": "main.pdf", "content": b"main", "is_main_file": True},
            {"source_file_id": "attachment", "filename": "attachment.xlsx", "content": b"attachment"},
        ],
        operator_id="u1",
    )

    assert result["status"] == "accepted"
    assert len(repo.files) == 2
    assert repo.document.document_metadata == {"title": "专项检查通知", "incoming_date": "2026-07-17"}
    assert [file.is_main_file for file in repo.files] == [True, False]
    assert len(tasker.enqueued) == 1
    assert tasker.enqueued[0]["task_type"] == "incoming_document_process"


async def test_process_reads_all_attachments_and_extracts_one_document_result():
    async def fake_parse(source, _params):
        return f"# {source}\n\n附件事实"

    async def fake_markdown_upload(*, incoming_id, markdown):
        return f"minio://parsed/{incoming_id}.md"

    async def fake_classify(**kwargs):
        assert "文件：主文件.pdf" in kwargs["markdown"]
        assert "文件：附件.xlsx" in kwargs["markdown"]
        return {
            "classification": "阶段性工作类",
            "classification_confidence": 0.9,
            "classification_evidence": "附件事实",
            "summary": "这份来文部署专项检查，并以附件列出具体事项。",
            "structured_result": {},
        }

    repo = FakeIncomingRepo()
    repo.document = SimpleNamespace(
        incoming_id="inc_1",
        source_document_id="DOC-1",
        document_metadata={"title": "专项检查"},
        confirmed_classification=None,
        ai_classification=None,
        status="uploaded",
    )
    repo.files = [
        SimpleNamespace(
            incoming_id="inc_1",
            incoming_file_id="incf_main",
            source_file_id="main",
            filename="主文件.pdf",
            original_file_url="minio://main",
            status="uploaded",
        ),
        SimpleNamespace(
            incoming_id="inc_1",
            incoming_file_id="incf_attachment",
            source_file_id="attachment",
            filename="附件.xlsx",
            original_file_url="minio://attachment",
            status="uploaded",
        ),
    ]
    extraction = FakeBusinessExtractionService()
    service = IncomingDocumentIngestService(
        incoming_repo=repo,
        tasker=FakeTasker(),
        parse_document=fake_parse,
        upload_markdown=fake_markdown_upload,
        classify_document=fake_classify,
        business_extraction_service=extraction,
    )

    result = await service.process_incoming_document("inc_1", context=FakeContext())

    assert result == {"incoming_id": "inc_1", "status": "ready"}
    assert repo.document.summary.startswith("这份来文")
    assert repo.document.ai_classification == "staged_work"
    assert len(extraction.calls) == 1
    assert [file["source_file_id"] for file in extraction.calls[0]["files"]] == ["main", "attachment"]
    assert extraction.calls[0]["classifications"] == ["staged_work"]
    assert all(file.markdown_file_url for file in repo.files)


async def test_process_rejects_empty_parsed_markdown():
    async def fake_parse(_source, _params):
        return "   "

    repo = FakeIncomingRepo()
    repo.document = SimpleNamespace(
        incoming_id="inc_1",
        source_document_id="DOC-1",
        document_metadata={},
        status="uploaded",
    )
    repo.files = [
        SimpleNamespace(
            incoming_id="inc_1",
            incoming_file_id="incf_main",
            source_file_id="main",
            filename="主文件.pdf",
            original_file_url="minio://main",
            status="uploaded",
        )
    ]
    service = IncomingDocumentIngestService(
        incoming_repo=repo,
        tasker=FakeTasker(),
        parse_document=fake_parse,
    )

    with pytest.raises(ValueError, match="Parsed Markdown is empty"):
        await service.process_incoming_document("inc_1")

    assert repo.document.status == "failed"
    assert repo.files[0].status == "failed"


@pytest.mark.parametrize(
    ("confidence", "expected_classifications"),
    [
        (0.8, ["regulation", "safety_management", "staged_work"]),
        (0.79, ["regulation"]),
    ],
)
async def test_classification_correction_recomputes_secondary_extraction_routes(
    monkeypatch, confidence, expected_classifications
):
    async def fake_download(_url):
        return "正文同时包含安全要求和阶段任务"

    async def fake_classify(**_kwargs):
        return {
            "classification": "安全管理类",
            "classification_confidence": confidence,
            "classification_evidence": "正文同时包含安全要求",
            "summary": "摘要",
            "structured_result": {},
            "additional_classifications": [
                {
                    "classification": "阶段性工作类",
                    "confidence": confidence,
                    "evidence": "阶段任务",
                }
            ],
        }

    monkeypatch.setattr(ingest_module, "_download_markdown", fake_download)
    repo = FakeIncomingRepo()
    repo.document = SimpleNamespace(
        incoming_id="inc_1",
        source_document_id="DOC-1",
        document_metadata={"title": "通知"},
        ai_classification="安全管理类",
        confirmed_classification=None,
        status="ready",
    )
    repo.files = [
        SimpleNamespace(
            incoming_id="inc_1",
            incoming_file_id="incf_1",
            source_file_id="main",
            filename="主文件.pdf",
            markdown_file_url="minio://parsed/main.md",
        )
    ]
    extraction = FakeBusinessExtractionService()
    service = IncomingDocumentIngestService(
        incoming_repo=repo,
        tasker=FakeTasker(),
        classify_document=fake_classify,
        business_extraction_service=extraction,
    )

    result = await service.correct_classification("inc_1", classification="规章制度类", operator_id="admin")

    assert result["status"] == "ready"
    assert repo.document.confirmed_classification == "regulation"
    assert extraction.calls[0]["classifications"] == expected_classifications


async def test_retry_resets_all_attachment_processing_state():
    repo = FakeIncomingRepo()
    repo.document = SimpleNamespace(incoming_id="inc_1", status="failed")
    repo.files = [
        SimpleNamespace(
            incoming_id="inc_1", incoming_file_id="incf_1", markdown_file_url="minio://parsed/1", processing_error="bad"
        ),
        SimpleNamespace(
            incoming_id="inc_1", incoming_file_id="incf_2", markdown_file_url="minio://parsed/2", processing_error="bad"
        ),
    ]
    tasker = FakeTasker()
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=tasker)

    result = await service.retry_processing("inc_1", operator_id="admin")

    assert result["taskId"] == "task_1"
    assert all(file.status == "uploaded" and file.markdown_file_url is None for file in repo.files)
    assert repo.document.status == "uploaded"


async def test_retry_rejects_document_while_processing():
    repo = FakeIncomingRepo()
    repo.document = SimpleNamespace(incoming_id="inc_1", status="extracting", knowledge_import_status="none")
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=FakeTasker())

    with pytest.raises(ValueError, match="cannot be retried"):
        await service.retry_processing("inc_1", operator_id="admin")


async def test_ingest_rejects_two_main_files():
    service = IncomingDocumentIngestService(incoming_repo=FakeIncomingRepo(), tasker=FakeTasker())

    with pytest.raises(ValueError, match="only one main file"):
        await service.ingest_files(
            source_system="oa",
            source_function_id="incoming",
            source_doc_id="DOC-1",
            document_metadata={},
            files=[
                {"source_file_id": "a", "filename": "a.pdf", "content": b"a", "is_main_file": True},
                {"source_file_id": "b", "filename": "b.pdf", "content": b"b", "is_main_file": True},
            ],
        )


async def test_incremental_attachment_preserves_existing_main_file():
    async def fake_upload(*, filename, **_kwargs):
        return {"minio_url": f"minio://documents/{filename}", "content_hash": filename, "size": 10}

    repo = FakeIncomingRepo()
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=FakeTasker(), upload_file=fake_upload)
    await service.ingest_files(
        source_system="oa",
        source_function_id="incoming",
        source_doc_id="DOC-1",
        document_metadata={"title": "通知"},
        files=[{"source_file_id": "main", "filename": "main.pdf", "content": b"main"}],
    )

    await service.ingest_files(
        source_system="oa",
        source_function_id="incoming",
        source_doc_id="DOC-1",
        document_metadata={"title": "通知"},
        files=[{"source_file_id": "attachment", "filename": "attachment.pdf", "content": b"attachment"}],
    )

    assert [(file.source_file_id, file.is_main_file) for file in repo.files] == [
        ("main", True),
        ("attachment", False),
    ]


async def test_explicit_new_main_file_replaces_existing_main_file():
    async def fake_upload(*, filename, **_kwargs):
        return {"minio_url": f"minio://documents/{filename}", "content_hash": filename, "size": 10}

    repo = FakeIncomingRepo()
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=FakeTasker(), upload_file=fake_upload)
    await service.ingest_files(
        source_system="oa",
        source_function_id="incoming",
        source_doc_id="DOC-1",
        document_metadata={"title": "通知"},
        files=[{"source_file_id": "main", "filename": "main.pdf", "content": b"main"}],
    )

    await service.ingest_files(
        source_system="oa",
        source_function_id="incoming",
        source_doc_id="DOC-1",
        document_metadata={"title": "通知"},
        files=[
            {
                "source_file_id": "replacement",
                "filename": "replacement.pdf",
                "content": b"replacement",
                "is_main_file": True,
            }
        ],
    )

    assert [(file.source_file_id, file.is_main_file) for file in repo.files] == [
        ("main", False),
        ("replacement", True),
    ]


async def test_unchanged_upload_does_not_reset_or_enqueue_again():
    async def fake_upload(*, filename, content, **_kwargs):
        return {
            "minio_url": f"minio://documents/{filename}",
            "content_hash": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

    repo = FakeIncomingRepo()
    tasker = FakeTasker()
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=tasker, upload_file=fake_upload)
    request = {
        "source_system": "oa",
        "source_function_id": "incoming",
        "source_doc_id": "DOC-1",
        "document_metadata": {"title": "通知"},
        "files": [{"source_file_id": "main", "filename": "main.pdf", "content": b"main"}],
    }
    await service.ingest_files(**request)
    repo.document.status = "ready"
    repo.document.summary = "已完成摘要"

    result = await service.ingest_files(**request)

    assert result["status"] == "ready"
    assert result["taskId"] is None
    assert repo.document.summary == "已完成摘要"
    assert len(tasker.enqueued) == 1


async def test_changed_upload_clears_stale_document_results():
    async def fake_upload(*, filename, content, **_kwargs):
        return {
            "minio_url": f"minio://documents/{filename}",
            "content_hash": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

    repo = FakeIncomingRepo()
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=FakeTasker(), upload_file=fake_upload)
    request = {
        "source_system": "oa",
        "source_function_id": "incoming",
        "source_doc_id": "DOC-1",
        "document_metadata": {"title": "通知"},
        "files": [{"source_file_id": "main", "filename": "main.pdf", "content": b"v1"}],
    }
    await service.ingest_files(**request)
    for name, value in {
        "ai_classification": "安全管理类",
        "classification_confidence": 0.9,
        "classification_evidence": "旧依据",
        "additional_classifications": [{"classification": "风险管理类"}],
        "confirmed_classification": "安全管理类",
        "confirmed_by": "admin",
        "confirmed_at": "now",
        "summary": "旧摘要",
        "knowledge_import_status": "failed",
        "linked_kb_id": "kb-old",
    }.items():
        setattr(repo.document, name, value)
    request["files"][0]["content"] = b"v2"

    await service.ingest_files(**request)

    assert repo.document.ai_classification is None
    assert repo.document.confirmed_classification is None
    assert repo.document.additional_classifications == []
    assert repo.document.summary is None
    assert repo.document.knowledge_import_status == "none"
    assert repo.document.linked_kb_id is None


async def test_partial_reupload_invalidates_old_ready_result_before_file_failure():
    failures_enabled = False

    async def fake_upload(*, filename, content, **_kwargs):
        if failures_enabled and filename == "attachment.pdf":
            raise RuntimeError("minio unavailable")
        return {
            "minio_url": f"minio://documents/{filename}",
            "content_hash": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

    repo = FakeIncomingRepo()
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=FakeTasker(), upload_file=fake_upload)
    request = {
        "source_system": "oa",
        "source_function_id": "incoming",
        "source_doc_id": "DOC-1",
        "document_metadata": {"title": "通知"},
        "files": [
            {"source_file_id": "main", "filename": "main.pdf", "content": b"v1"},
            {"source_file_id": "attachment", "filename": "attachment.pdf", "content": b"v1"},
        ],
    }
    await service.ingest_files(**request)
    repo.document.status = "ready"
    repo.document.summary = "旧摘要"
    request["files"][0]["content"] = b"v2"
    request["files"][1]["content"] = b"v2"
    failures_enabled = True

    with pytest.raises(RuntimeError, match="minio unavailable"):
        await service.ingest_files(**request)

    assert repo.document.status == "uploaded"
    assert repo.document.summary is None
    assert repo.document.ai_classification is None


async def test_indexed_document_allows_idempotent_upload_but_rejects_content_change():
    async def fake_upload(*, filename, content, **_kwargs):
        return {
            "minio_url": f"minio://documents/{filename}",
            "content_hash": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

    repo = FakeIncomingRepo()
    tasker = FakeTasker()
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=tasker, upload_file=fake_upload)
    request = {
        "source_system": "oa",
        "source_function_id": "incoming",
        "source_doc_id": "DOC-1",
        "document_metadata": {"title": "通知"},
        "files": [{"source_file_id": "main", "filename": "main.pdf", "content": b"v1"}],
    }
    await service.ingest_files(**request)
    repo.document.status = "ready"
    repo.document.knowledge_import_status = "indexed"

    result = await service.ingest_files(**request)
    assert result["status"] == "ready"

    request["files"][0]["content"] = b"v2"
    with pytest.raises(ValueError, match="cannot be replaced"):
        await service.ingest_files(**request)


async def test_knowledge_import_can_select_attachments_then_complete_remaining_files():
    repo = FakeIncomingRepo()
    repo.document = SimpleNamespace(
        incoming_id="inc_1",
        source_document_id="DOC-1",
        document_metadata={"title": "通知"},
        status="ready",
        ai_classification="阶段性工作类",
        confirmed_classification=None,
        knowledge_import_status="none",
        knowledge_import_task_id=None,
        linked_kb_id=None,
    )
    repo.files = [
        SimpleNamespace(
            incoming_id="inc_1",
            incoming_file_id="incf_main",
            source_file_id="main",
            filename="同名.pdf",
            original_file_url="minio://incoming/main.pdf",
            content_hash="hash-main",
            file_size=10,
            knowledge_import_status="none",
            knowledge_import_error=None,
            linked_file_id=None,
        ),
        SimpleNamespace(
            incoming_id="inc_1",
            incoming_file_id="incf_attachment",
            source_file_id="attachment",
            filename="同名.pdf",
            original_file_url="minio://incoming/attachment.pdf",
            content_hash="hash-attachment",
            file_size=20,
            knowledge_import_status="none",
            knowledge_import_error=None,
            linked_file_id=None,
        ),
    ]
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=FakeTasker())

    with pytest.raises(ValueError, match="not found"):
        await service.import_to_knowledge(
            "inc_1",
            kb_id="kb_1",
            source_file_ids=["missing"],
            document_ingest_service=FakeKnowledgeIngest(),
        )

    attachment_ingest = FakeKnowledgeIngest(["kb_attachment"])
    result = await service.import_to_knowledge(
        "inc_1",
        kb_id="kb_1",
        source_file_ids=["attachment"],
        document_ingest_service=attachment_ingest,
    )

    assert result["sourceFileIds"] == ["attachment"]
    assert repo.document.knowledge_import_status == "partial"
    assert repo.files[0].knowledge_import_status == "none"
    assert repo.files[1].linked_file_id == "kb_attachment"
    assert attachment_ingest.calls[0]["items"] == ["minio://incoming/attachment.pdf"]
    assert attachment_ingest.calls[0]["params"]["source_paths"] == {
        "minio://incoming/attachment.pdf": "incoming/阶段性工作类/incf_attachment/同名.pdf"
    }

    remaining_ingest = FakeKnowledgeIngest(["kb_main"])
    await service.import_to_knowledge(
        "inc_1",
        kb_id="kb_1",
        document_ingest_service=remaining_ingest,
    )

    assert remaining_ingest.calls[0]["items"] == ["minio://incoming/main.pdf"]
    assert repo.document.knowledge_import_status == "indexed"
    assert [file.linked_file_id for file in repo.files] == ["kb_main", "kb_attachment"]


async def test_knowledge_import_enqueue_failure_restores_failed_state():
    repo = FakeIncomingRepo()
    repo.document = SimpleNamespace(
        incoming_id="inc_1",
        source_document_id="DOC-1",
        document_metadata={},
        status="ready",
        ai_classification=None,
        confirmed_classification=None,
        knowledge_import_status="none",
        knowledge_import_task_id=None,
        linked_kb_id=None,
    )
    repo.files = [
        SimpleNamespace(
            incoming_id="inc_1",
            incoming_file_id="incf_main",
            source_file_id="main",
            filename="main.pdf",
            original_file_url="minio://incoming/main.pdf",
            content_hash="hash-main",
            file_size=10,
            knowledge_import_status="none",
            knowledge_import_error=None,
            linked_file_id=None,
        )
    ]
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=FakeTasker())

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await service.import_to_knowledge(
            "inc_1",
            kb_id="kb_1",
            document_ingest_service=FakeKnowledgeIngest(enqueue_error=RuntimeError("queue unavailable")),
        )

    assert repo.document.knowledge_import_status == "failed"
    assert repo.document.linked_kb_id is None
    assert repo.files[0].knowledge_import_status == "failed"


async def test_ingest_rejects_attachment_change_while_document_is_processing():
    repo = FakeIncomingRepo()
    repo.document = SimpleNamespace(
        incoming_id=IncomingDocumentIngestService._incoming_id("oa", "incoming", "DOC-1"),
        status="parsing",
        document_metadata={"title": "通知"},
    )
    service = IncomingDocumentIngestService(incoming_repo=repo, tasker=FakeTasker())

    with pytest.raises(ValueError, match="being processed"):
        await service.ingest_files(
            source_system="oa",
            source_function_id="incoming",
            source_doc_id="DOC-1",
            document_metadata={"title": "通知"},
            files=[{"source_file_id": "new", "filename": "new.pdf", "content": b"new"}],
        )


def test_low_confidence_classification_does_not_enable_secondary_extraction():
    result = ingest_module.IncomingDocumentClassificationResult(
        classification="安全管理类",
        classification_confidence=0.79,
        classification_evidence="安全管理要求",
        summary="摘要",
        additional_classifications=[{"classification": "阶段性工作类", "confidence": 0.79, "evidence": "阶段任务"}],
    )

    assert ingest_module._trusted_extraction_classifications(result) == ["safety_management"]


def test_additional_classification_requires_confidence_and_source_evidence():
    result = ingest_module._validated_classification_result(
        ingest_module.IncomingDocumentClassificationResult(
            classification="安全管理类",
            classification_confidence=0.9,
            classification_evidence="应加强现场安全管理",
            summary="安全管理要求",
            additional_classifications=[
                {"classification": "风险管理类", "confidence": 0.9, "evidence": "存在重大风险"},
                {"classification": "阶段性工作类", "confidence": 0.7, "evidence": "本月完成检查"},
                {"classification": "规章制度类", "confidence": 0.9, "evidence": "并不存在的原文"},
            ],
        ),
        "应加强现场安全管理，存在重大风险，本月完成检查。",
    )

    assert [item.classification for item in result.additional_classifications] == ["risk_management"]
    assert result.additional_classifications[0].evidence == "存在重大风险"


def test_primary_classification_evidence_normalizes_pdf_whitespace_and_quotes():
    source_text = "为进一步规范路用客车的检修\n运用管理，现将重新修订的„中国铁路客车检修规程‟印发给你们。"
    result = ingest_module._validated_classification_result(
        ingest_module.IncomingDocumentClassificationResult(
            classification="规章制度类",
            classification_confidence=0.95,
            classification_evidence='为进一步规范路用客车的检修运用管理，现将重新修订的"中国铁路客车检修规程"印发给你们。',
            summary="发布修订后的客车检修规程。",
        ),
        source_text,
    )

    assert result.classification_evidence == source_text


def test_primary_classification_evidence_rejects_paraphrase():
    source_text = "中国铁路上海局集团有限公司关于重新修订客车检修运用管理办法。"
    result = ingest_module._validated_classification_result(
        ingest_module.IncomingDocumentClassificationResult(
            classification="规章制度类",
            classification_confidence=0.95,
            classification_evidence="中国铁路上海局集团有限公司关于重新印发客车检修规程",
            summary="发布修订后的客车检修管理办法。",
        ),
        source_text,
    )

    assert result.classification_evidence is None


def test_unmatched_primary_classification_evidence_does_not_fail_document():
    result = ingest_module._validated_classification_result(
        ingest_module.IncomingDocumentClassificationResult(
            classification="规章制度类",
            classification_confidence=0.95,
            classification_evidence="模型概括但并非原文逐字引用",
            summary="发布修订后的客车检修规程。",
        ),
        "各单位应遵照修订后的客车检修规程执行。",
    )

    assert result.classification == "regulation"
    assert result.classification_evidence is None


def test_invalid_primary_classification_is_rejected():
    with pytest.raises(ValueError, match="not configured"):
        ingest_module._validated_classification_result(
            ingest_module.IncomingDocumentClassificationResult(
                classification="模型自创分类",
                classification_confidence=0.9,
                classification_evidence="正文依据",
                summary="摘要",
            ),
            "正文依据",
        )


async def test_long_document_classification_uses_structured_chunks(monkeypatch):
    prompts = []
    chunk_params = []

    async def fake_classify(**kwargs):
        prompts.append(kwargs["markdown"])
        evidence = kwargs["markdown"].splitlines()[0]
        return {
            "classification": "阶段性工作类",
            "classification_confidence": 0.8,
            "classification_evidence": evidence,
            "summary": "临时提要",
            "structured_result": {},
            "additional_classifications": [{"classification": "安全管理类", "confidence": 0.8, "evidence": evidence}],
        }

    monkeypatch.setattr(ingest_module, "document_input_token_limit", lambda _model_spec: 10)
    monkeypatch.setattr(ingest_module, "count_tokens", lambda text: 11 if "超长附件" in text else 3)
    monkeypatch.setattr(
        ingest_module,
        "chunk_markdown",
        lambda *_args: (
            chunk_params.append(_args[3])
            or [
                {"content": "第一部分 内容", "chunk_index": 0},
                {"content": "第二部分 内容", "chunk_index": 1},
            ]
        ),
    )
    service = IncomingDocumentIngestService(
        incoming_repo=FakeIncomingRepo(), tasker=FakeTasker(), classify_document=fake_classify
    )
    document = SimpleNamespace(document_metadata={}, source_document_id="DOC-1")
    file = SimpleNamespace(incoming_file_id="incf_1", filename="附件.pdf")

    result = await service._classify_document_bundle(
        document,
        [{"file": file, "markdown": "超长附件\n第一部分 内容\n第二部分 内容"}],
    )

    assert result.summary == "临时提要"
    assert ingest_module._trusted_extraction_classifications(result) == ["staged_work", "safety_management"]
    assert result.additional_classifications[0].evidence in "超长附件\n第一部分 内容\n第二部分 内容"
    assert chunk_params[0]["chunk_parser_config"]["overlapped_percent"] == 10
    assert any("抽取分类：staged_work、safety_management" in prompt for prompt in prompts)
