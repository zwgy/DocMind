from datetime import date
from types import SimpleNamespace

import pytest
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import ValidationError

from yuxi.agents.middlewares.skills import resolve_skill_gated_tools
from yuxi.agents.toolkits.incoming_documents import tools
from yuxi.agents.toolkits.registry import get_extra_metadata


def _tool_callable(tool):
    return tool.coroutine


def _document():
    return SimpleNamespace(
        incoming_id="inc-1",
        source_system="production",
        source_document_id="doc-1",
        document_metadata={"title": "风险通知", "incoming_date": "2026-07-01"},
        confirmed_classification=None,
        ai_classification="risk_management",
        classification_confidence=0.95,
        classification_evidence="存在逾期风险",
        additional_classifications=[],
        summary="来文要求跟进风险。",
        status="ready",
        review_status="confirmed",
        created_at=None,
    )


def test_search_schema_rejects_reversed_date_range():
    with pytest.raises(ValidationError, match="date_from 不能晚于 date_to"):
        tools.SearchIncomingDocumentsInput(date_from="2026-07-02", date_to="2026-07-01")


def test_incoming_document_tools_are_registered_for_skill_gating():
    for tool_name in (
        "search_incoming_documents",
        "read_incoming_document",
        "download_incoming_document_files",
        "get_incoming_document_statistics",
    ):
        metadata = get_extra_metadata(tool_name)
        assert metadata is not None
        assert metadata.category == "incoming_document"
        assert "来文业务 Skill" in metadata.config_guide

    context = SimpleNamespace(
        _readable_skills=["incoming-document"],
        _runtime_skill_dependency_map={
            "incoming-document": {
                "tools": [
                    "search_incoming_documents",
                    "read_incoming_document",
                    "download_incoming_document_files",
                    "get_incoming_document_statistics",
                ]
            }
        },
    )
    assert {tool.name for tool in resolve_skill_gated_tools(context)} == {
        "search_incoming_documents",
        "read_incoming_document",
        "download_incoming_document_files",
        "get_incoming_document_statistics",
    }


def test_tool_schemas_limit_filter_and_source_file_lists():
    with pytest.raises(ValidationError):
        tools.SearchIncomingDocumentsInput(classifications=["分类"] * 51)
    with pytest.raises(ValidationError):
        tools.read_incoming_document.args_schema(incoming_id="inc-1", source_file_ids=["file"] * 101)
    with pytest.raises(ValidationError):
        tools.download_incoming_document_files.args_schema(incoming_id="inc-1", source_file_ids=["file"] * 101)


def test_tool_schemas_accept_json_encoded_source_file_lists_from_local_models():
    read_input = tools.read_incoming_document.args_schema(
        incoming_id="inc-1",
        source_file_ids='["file-1"]',
        include_full_text="true",
    )
    download_input = tools.download_incoming_document_files.args_schema(
        incoming_id="inc-1",
        source_file_ids='["file-1"]',
    )

    assert read_input.source_file_ids == ["file-1"]
    assert read_input.include_full_text is True
    assert download_input.source_file_ids == ["file-1"]


def test_runtime_thread_scope_uses_runtime_configurable_when_context_is_missing():
    runtime = ToolRuntime(
        state={},
        tool_call_id="call-1",
        config={"configurable": {"uid": "user-1", "thread_id": "thread-1"}},
        context=None,
        store=None,
        stream_writer=lambda _: None,
    )
    parsed = tools.read_incoming_document._parse_input(
        {
            "incoming_id": "inc-1",
            "source_file_ids": ["file-1"],
            "include_full_text": True,
            "runtime": runtime,
        },
        "call-1",
    )

    assert parsed["runtime"] is runtime
    assert "runtime" not in tools.read_incoming_document.tool_call_schema.model_fields
    assert tools._runtime_thread_scope(parsed["runtime"]) == ("user-1", "thread-1")


def test_runtime_thread_scope_accepts_mapping_context():
    runtime = SimpleNamespace(context={"uid": "user-1", "file_thread_id": "files-thread-1"})

    assert tools._runtime_thread_scope(runtime) == ("user-1", "files-thread-1")


def test_item_type_names_are_dynamically_normalized():
    assert tools._normalize_item_types(["风险事项", "task_item", "风险事项"]) == ["risk_item", "task_item"]
    with pytest.raises(ValueError, match="未知条目类型.*当前支持"):
        tools._normalize_item_types(["不存在的类型"])


@pytest.mark.asyncio
async def test_search_normalizes_classification_id_or_label_and_rejects_unknown(monkeypatch):
    class FakeRepository:
        async def search_business_documents(self, **kwargs):
            assert kwargs["classifications"] == ["risk_management"]
            return [], 0

        async def get_business_document_facets(self, _incoming_ids):
            return {}

    monkeypatch.setattr(tools, "IncomingDocumentRepository", FakeRepository)

    result = await _tool_callable(tools.search_incoming_documents)(classifications=["风险管理类"])
    invalid = await _tool_callable(tools.search_incoming_documents)(classifications=["风险管控类"])

    assert result["classification_labels"]["risk_management"] == "风险管理类"
    assert "未知分类" in invalid


@pytest.mark.asyncio
async def test_search_returns_document_summary_without_full_details_or_urls(monkeypatch):
    class FakeRepository:
        async def search_business_documents(self, **kwargs):
            assert kwargs["date_from"] == "2026-07-01"
            assert kwargs["item_types"] == ["risk_item"]
            assert kwargs["title"] == "风险"
            assert kwargs["document_number"] == "安监〔2026〕1号"
            assert kwargs["source_unit"] == "安监部"
            assert kwargs["keyword"] == "整改"
            return [_document()], 1

        async def get_business_document_facets(self, incoming_ids):
            assert incoming_ids == ["inc-1"]
            return {"inc-1": {"attachment_count": 2, "item_types": ["risk_item"]}}

    monkeypatch.setattr(tools, "IncomingDocumentRepository", FakeRepository)

    result = await _tool_callable(tools.search_incoming_documents)(
        date_from=date(2026, 7, 1),
        item_types=["风险事项"],
        title="风险",
        document_number="安监〔2026〕1号",
        source_unit="安监部",
        keyword="整改",
    )

    assert result["total"] == 1
    assert result["items"][0]["attachment_count"] == 2
    assert result["items"][0]["item_types"] == ["risk_item"]
    assert result["items"][0]["classification"] == "risk_management"
    assert result["items"][0]["classification_label"] == "风险管理类"
    assert result["item_type_labels"]["risk_item"] == "风险事项"
    assert "result_groups" not in result["items"][0]
    assert "minio" not in str(result).lower()


@pytest.mark.asyncio
async def test_read_full_text_returns_only_selected_markdown_path(monkeypatch):
    class FakeIncomingRepository:
        async def get_by_incoming_id(self, incoming_id):
            assert incoming_id == "inc-1"
            return _document()

        async def list_files(self, incoming_id):
            assert incoming_id == "inc-1"
            return [
                SimpleNamespace(
                    source_file_id="main",
                    filename="主文件.docx",
                    is_main_file=True,
                    status="parsed",
                    markdown_file_url="minio://parsed/main.md",
                ),
                SimpleNamespace(
                    source_file_id="attachment",
                    filename="附件.xlsx",
                    is_main_file=False,
                    status="parsed",
                    markdown_file_url="minio://parsed/attachment.md",
                ),
            ]

    class UnexpectedExtractionRepository:
        async def get_latest_by_incoming_id(self, incoming_id):
            raise AssertionError(f"原文交付不应读取结构化结果: {incoming_id}")

    class FakeMarkdownService:
        def __init__(self, repo):
            assert isinstance(repo, FakeIncomingRepository)

        async def materialize(self, **kwargs):
            assert kwargs == {
                "incoming_id": "inc-1",
                "source_file_ids": ["attachment"],
                "uid": "user-1",
                "thread_id": "thread-1",
            }
            return [
                {
                    "source_file_id": "attachment",
                    "filename": "附件.xlsx",
                    "markdown_path": "/home/gem/user-data/outputs/incoming-documents/inc-1/file-2.md",
                }
            ]

    monkeypatch.setattr(tools, "IncomingDocumentRepository", FakeIncomingRepository)
    monkeypatch.setattr(tools, "DocumentBusinessExtractionRepository", UnexpectedExtractionRepository)
    monkeypatch.setattr(tools, "IncomingDocumentMarkdownService", FakeMarkdownService)

    result = await _tool_callable(tools.read_incoming_document)(
        incoming_id="inc-1",
        source_file_ids=["attachment"],
        include_full_text=True,
        runtime=SimpleNamespace(context=SimpleNamespace(uid="user-1", thread_id="thread-1")),
    )

    assert set(result) == {"incoming_id", "markdown_files"}
    assert result["incoming_id"] == "inc-1"
    assert result["markdown_files"][0]["markdown_path"].startswith("/home/gem/user-data/")
    assert "reader_tool" not in str(result)
    assert "minio://" not in str(result)


@pytest.mark.asyncio
async def test_read_without_full_text_does_not_materialize_markdown(monkeypatch):
    class FakeIncomingRepository:
        async def get_by_incoming_id(self, incoming_id):
            assert incoming_id == "inc-1"
            return _document()

        async def list_files(self, incoming_id):
            assert incoming_id == "inc-1"
            return []

    class FakeExtractionRepository:
        async def get_latest_by_incoming_id(self, incoming_id):
            assert incoming_id == "inc-1"
            return None

    class UnexpectedMarkdownService:
        def __init__(self, repo):
            del repo
            raise AssertionError("include_full_text=false 不应创建 Markdown 服务")

    monkeypatch.setattr(tools, "IncomingDocumentRepository", FakeIncomingRepository)
    monkeypatch.setattr(tools, "DocumentBusinessExtractionRepository", FakeExtractionRepository)
    monkeypatch.setattr(tools, "IncomingDocumentMarkdownService", UnexpectedMarkdownService)

    result = await _tool_callable(tools.read_incoming_document)(
        incoming_id="inc-1",
        include_full_text=False,
        runtime=SimpleNamespace(),
    )

    assert result["markdown_files"] == []


@pytest.mark.asyncio
async def test_download_original_files_returns_only_thread_artifact_paths(monkeypatch):
    class FakeOriginalFileService:
        async def materialize_original(self, **kwargs):
            assert kwargs == {
                "incoming_id": "inc-1",
                "source_file_ids": ["main"],
                "uid": "user-1",
                "thread_id": "thread-1",
            }
            return [
                {
                    "source_file_id": "main",
                    "filename": "主文件.docx",
                    "original_path": "/home/gem/user-data/outputs/incoming-documents/inc-1/file-1/主文件.docx",
                }
            ]

    monkeypatch.setattr(tools, "IncomingDocumentMarkdownService", FakeOriginalFileService)

    result = await _tool_callable(tools.download_incoming_document_files)(
        incoming_id="inc-1",
        source_file_ids=["main"],
        runtime=SimpleNamespace(context=SimpleNamespace(uid="user-1", thread_id="thread-1")),
    )

    assert set(result) == {"incoming_id", "original_files"}
    assert result["original_files"][0]["original_path"].startswith("/home/gem/user-data/")
    assert "minio" not in str(result).lower()


@pytest.mark.asyncio
async def test_read_returns_clear_error_when_markdown_storage_fails(monkeypatch):
    class FakeIncomingRepository:
        async def get_by_incoming_id(self, incoming_id):
            assert incoming_id == "inc-1"
            return _document()

        async def list_files(self, incoming_id):
            assert incoming_id == "inc-1"
            return []

    class FakeExtractionRepository:
        async def get_latest_by_incoming_id(self, incoming_id):
            assert incoming_id == "inc-1"
            return None

    class FailingMarkdownService:
        def __init__(self, repo):
            assert isinstance(repo, FakeIncomingRepository)

        async def materialize(self, **kwargs):
            del kwargs
            raise tools.IncomingDocumentMarkdownError("对象存储不可用")

    monkeypatch.setattr(tools, "IncomingDocumentRepository", FakeIncomingRepository)
    monkeypatch.setattr(tools, "DocumentBusinessExtractionRepository", FakeExtractionRepository)
    monkeypatch.setattr(tools, "IncomingDocumentMarkdownService", FailingMarkdownService)

    with pytest.raises(tools.ToolException, match="读取来文原文失败：对象存储不可用"):
        await _tool_callable(tools.read_incoming_document)(
            incoming_id="inc-1",
            source_file_ids=["main"],
            include_full_text=True,
            runtime=SimpleNamespace(context=SimpleNamespace(uid="user-1", thread_id="thread-1")),
        )


@pytest.mark.asyncio
async def test_read_full_text_requires_source_file_ids(monkeypatch):
    result = await _tool_callable(tools.read_incoming_document)(
        incoming_id="inc-1",
        include_full_text=True,
        runtime=SimpleNamespace(),
    )

    assert result == "include_full_text=true 时必须指定 source_file_ids"


@pytest.mark.asyncio
async def test_statistics_uses_same_filters(monkeypatch):
    class FakeRepository:
        async def get_business_statistics(self, **kwargs):
            assert kwargs == {
                "date_from": None,
                "date_to": "2026-07-31",
                "classifications": ["risk_management"],
                "item_types": None,
                "title": None,
                "document_number": None,
                "source_unit": None,
                "keyword": None,
            }
            return {"total": 2, "by_classification": [], "by_item_type": [], "by_month": []}

    monkeypatch.setattr(tools, "IncomingDocumentRepository", FakeRepository)

    result = await _tool_callable(tools.get_incoming_document_statistics)(
        date_to=date(2026, 7, 31),
        classifications=["风险管理类"],
    )

    assert result["total"] == 2


@pytest.mark.asyncio
async def test_read_hides_historical_extraction_while_document_is_not_ready(monkeypatch):
    document = _document()
    document.status = "parsing"

    class FakeIncomingRepository:
        async def get_by_incoming_id(self, incoming_id):
            assert incoming_id == "inc-1"
            return document

        async def list_files(self, incoming_id):
            assert incoming_id == "inc-1"
            return []

    class UnexpectedExtractionRepository:
        async def get_latest_by_incoming_id(self, incoming_id):
            raise AssertionError(f"非 ready 来文不应读取历史抽取结果: {incoming_id}")

    monkeypatch.setattr(tools, "IncomingDocumentRepository", FakeIncomingRepository)
    monkeypatch.setattr(tools, "DocumentBusinessExtractionRepository", UnexpectedExtractionRepository)

    result = await _tool_callable(tools.read_incoming_document)(incoming_id="inc-1")

    assert result["result_groups"] == []
    assert result["categories"] == {}
    assert result["schema_ids"] == []
