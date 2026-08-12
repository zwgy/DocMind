from __future__ import annotations

import pytest

from yuxi.storage.postgres.manager import PostgresManager


class _RecordingConnection:
    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))


class _RecordingBegin:
    def __init__(self, connection: _RecordingConnection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RecordingEngine:
    def __init__(self, connection: _RecordingConnection):
        self.connection = connection

    def begin(self):
        return _RecordingBegin(self.connection)


@pytest.mark.asyncio
async def test_ensure_knowledge_schema_rebuilds_vectors_for_incomplete_legacy_chunks():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_knowledge_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert (
        "UPDATE knowledge_chunks SET graph_structure_indexed = TRUE "
        "WHERE graph_indexed IS TRUE AND graph_structure_indexed IS NOT TRUE"
    ) in statements
    assert "mention.entity_id = entity.entity_id AND chunk.graph_indexed IS NOT TRUE" in statements
    assert "mention.triple_id = triple.triple_id AND chunk.graph_indexed IS NOT TRUE" in statements
    assert "THEN 'pending' ELSE 'indexed'" in statements


@pytest.mark.asyncio
async def test_ensure_knowledge_schema_uses_global_source_document_identity(monkeypatch):
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_knowledge_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)
    old_constraint_position = statements.index("DROP CONSTRAINT IF EXISTS uq_incoming_documents_source_identity")
    old_column_position = statements.index("DROP COLUMN IF EXISTS source_function_id")
    assert old_constraint_position < old_column_position
    assert "DROP INDEX IF EXISTS ix_incoming_documents_source_function_id" in statements
    assert "DROP COLUMN IF EXISTS source_function_id" in statements
    assert "DROP CONSTRAINT IF EXISTS uq_incoming_documents_source_identity" in statements
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_incoming_documents_source_document_identity "
        "ON incoming_documents(source_system, source_document_id)"
    ) in statements
