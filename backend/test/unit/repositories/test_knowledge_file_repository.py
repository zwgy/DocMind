from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.repositories import knowledge_file_repository as repo_module
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository, SQL_IN_BATCH_SIZE


def _extract_id_batch(statement) -> list[str]:
    id_batches = [value for value in statement.compile().params.values() if isinstance(value, list | tuple) and value]
    assert len(id_batches) == 1
    return list(id_batches[0])


@pytest.mark.asyncio
async def test_list_by_file_ids_splits_large_inputs(monkeypatch):
    file_ids = [f"file-{index:05d}" for index in range(SQL_IN_BATCH_SIZE + 5)]
    batch_lengths: list[int] = []

    class FakeScalarResult:
        def __init__(self, records: list[SimpleNamespace]):
            self.records = records

        def all(self):
            return self.records

    class FakeResult:
        def __init__(self, records: list[SimpleNamespace]):
            self.records = records

        def scalars(self):
            return FakeScalarResult(self.records)

    class FakeSession:
        async def execute(self, statement):
            batch = _extract_id_batch(statement)
            batch_lengths.append(len(batch))
            return FakeResult([SimpleNamespace(file_id=file_id) for file_id in batch])

    @asynccontextmanager
    async def fake_session_context():
        yield FakeSession()

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    records = await KnowledgeFileRepository().list_by_file_ids(file_ids)

    assert batch_lengths == [SQL_IN_BATCH_SIZE, 5]
    assert [record.file_id for record in records] == file_ids
