from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from yuxi.repositories import incoming_document_repository as repo_module
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.storage.postgres.models_knowledge import IncomingDocument


def test_business_query_filters_cover_document_metadata_files_and_latest_items():
    filters = IncomingDocumentRepository._business_query_filters(
        date_from="2026-01-01",
        date_to="2026-12-31",
        classifications=["风险管理类"],
        item_types=["risk_item"],
        keyword="合同",
    )

    statement = select(IncomingDocument.incoming_id).where(*filters)
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "incoming_document_files" in sql
    assert "document_business_extraction_items" in sql
    assert "max(document_business_extraction_results.id)" in sql
    assert "incoming_documents.status = 'ready'" in sql
    assert "incoming_date" in sql
    assert "2026-01-01" in sql
    assert "2026-12-31" in sql


@pytest.mark.asyncio
async def test_search_business_documents_returns_document_page_count(monkeypatch):
    document = SimpleNamespace(incoming_id="inc-1")

    class FakeScalars:
        def all(self):
            return [document]

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        async def scalar(self, statement):
            del statement
            return 1

        async def execute(self, statement):
            compiled = statement.compile().params
            assert list(compiled.values()).count(5) == 2
            return FakeResult()

    @asynccontextmanager
    async def fake_session_context():
        yield FakeSession()

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    items, total = await IncomingDocumentRepository().search_business_documents(page=2, page_size=5)

    assert items == [document]
    assert total == 1


@pytest.mark.asyncio
async def test_business_statistics_distinguish_documents_and_details(monkeypatch):
    result_rows = iter(
        [
            [("风险管理类", 2), ("未分类", 1)],
            [("risk_item", 2, 5)],
            [("2026-06", 1), ("2026-07", 2)],
        ]
    )

    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class FakeSession:
        async def scalar(self, statement):
            del statement
            return 3

        async def execute(self, statement):
            del statement
            return FakeResult(next(result_rows))

    @asynccontextmanager
    async def fake_session_context():
        yield FakeSession()

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    result = await IncomingDocumentRepository().get_business_statistics()

    assert result["total"] == 3
    assert result["by_classification"] == [
        {"classification": "风险管理类", "document_count": 2},
        {"classification": "未分类", "document_count": 1},
    ]
    assert result["by_item_type"] == [{"item_type": "risk_item", "document_count": 2, "detail_count": 5}]
    assert result["by_month"] == [
        {"month": "2026-06", "document_count": 1},
        {"month": "2026-07", "document_count": 2},
    ]


@pytest.mark.asyncio
async def test_business_document_facets_batch_file_counts_and_item_types(monkeypatch):
    rows = iter([[("inc-1", 2)], [("inc-1", "risk_item"), ("inc-1", "task_item")]])

    class FakeResult:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class FakeSession:
        async def execute(self, statement):
            del statement
            return FakeResult(next(rows))

    @asynccontextmanager
    async def fake_session_context():
        yield FakeSession()

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    result = await IncomingDocumentRepository().get_business_document_facets(["inc-1", "inc-2"])

    assert result == {
        "inc-1": {"attachment_count": 2, "item_types": ["risk_item", "task_item"]},
        "inc-2": {"attachment_count": 0, "item_types": []},
    }
