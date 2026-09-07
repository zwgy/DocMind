from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.repositories import document_business_extraction_repository as repository_module
from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository


class FakeResult:
    def one_or_none(self):
        return (SimpleNamespace(), SimpleNamespace())


class FakeSession:
    def __init__(self):
        self.scalar_statements = []
        self.execute_statements = []

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return SimpleNamespace(published_extraction_run_id="ber_published")

    async def execute(self, statement):
        self.execute_statements.append(statement)
        return FakeResult()


@pytest.mark.asyncio
async def test_get_latest_by_incoming_id_uses_published_run_when_available(monkeypatch):
    session = FakeSession()

    @asynccontextmanager
    async def fake_session_context():
        yield session

    async def fake_build_view(_session, _result, _run):
        return {"run_id": "ber_published"}

    monkeypatch.setattr(repository_module.pg_manager, "get_async_session_context", fake_session_context)
    repository = DocumentBusinessExtractionRepository()
    monkeypatch.setattr(repository, "_build_view", fake_build_view)

    result = await repository.get_latest_by_incoming_id("inc_1")

    assert result == {"run_id": "ber_published"}
    assert len(session.scalar_statements) == 1
    assert "ber_published" in str(session.execute_statements[0].compile(compile_kwargs={"literal_binds": True}))
