from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import IncomingDocument
from yuxi.utils.datetime_utils import utc_now_naive


class IncomingDocumentRepository:
    _writable_fields = {
        "source_system",
        "source_document_id",
        "source_key",
        "source_url",
        "filename",
        "content_hash",
        "file_size",
        "mime_type",
        "original_file_url",
        "markdown_file_url",
        "status",
        "classification",
        "classification_confidence",
        "summary",
        "structured_result",
        "processing_error",
        "linked_kb_id",
        "linked_file_id",
        "knowledge_import_status",
        "knowledge_import_task_id",
        "knowledge_import_error",
        "metadata_json",
        "created_by",
        "updated_by",
    }

    @classmethod
    def _sanitize_data(cls, data: dict[str, Any]) -> dict[str, Any]:
        sanitized = {key: value for key, value in data.items() if key in cls._writable_fields}
        if sanitized:
            sanitized["updated_at"] = utc_now_naive()
        return sanitized

    async def get_by_source_identity(self, source_system: str, source_document_id: str) -> IncomingDocument | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument).where(
                    IncomingDocument.source_system == source_system,
                    IncomingDocument.source_document_id == source_document_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_by_incoming_id(self, incoming_id: str) -> IncomingDocument | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id)
            )
            return result.scalar_one_or_none()

    async def upsert(self, incoming_id: str, data: dict[str, Any]) -> IncomingDocument:
        sanitized_data = self._sanitize_data(data)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id)
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                record = IncomingDocument(incoming_id=incoming_id, **sanitized_data)
                session.add(record)
                return record
            for key, value in sanitized_data.items():
                setattr(existing, key, value)
            return existing

    async def update_fields(self, incoming_id: str, data: dict[str, Any]) -> IncomingDocument:
        sanitized_data = self._sanitize_data(data)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id)
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise ValueError(f"Incoming document not found: {incoming_id}")
            for key, value in sanitized_data.items():
                setattr(record, key, value)
            return record

    async def list_by_source_key(self, source_key: str, source_system: str | None = None) -> list[IncomingDocument]:
        if not source_key:
            return []
        filters = [IncomingDocument.source_key == source_key]
        if source_system:
            filters.append(IncomingDocument.source_system == source_system)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument).where(*filters).order_by(IncomingDocument.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_by_source_url(self, source_url: str, source_system: str | None = None) -> list[IncomingDocument]:
        if not source_url:
            return []
        filters = [IncomingDocument.source_url == source_url]
        if source_system:
            filters.append(IncomingDocument.source_system == source_system)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument).where(*filters).order_by(IncomingDocument.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_by_source_doc_id_and_filename(
        self,
        source_document_id: str,
        filename: str,
        source_system: str | None = None,
    ) -> list[IncomingDocument]:
        normalized = filename.strip().lower()
        if not source_document_id or not normalized:
            return []
        filters = [
            IncomingDocument.source_document_id == source_document_id,
            func.lower(IncomingDocument.filename) == normalized,
        ]
        if source_system:
            filters.append(IncomingDocument.source_system == source_system)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument).where(*filters).order_by(IncomingDocument.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_by_filename_and_size(self, filename: str, file_size: int) -> list[IncomingDocument]:
        normalized = filename.strip().lower()
        if not normalized:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument)
                .where(
                    func.lower(IncomingDocument.filename) == normalized,
                    IncomingDocument.file_size == int(file_size),
                )
                .order_by(IncomingDocument.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_by_filename(self, filename: str) -> list[IncomingDocument]:
        normalized = filename.strip().lower()
        if not normalized:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument)
                .where(func.lower(IncomingDocument.filename) == normalized)
                .order_by(IncomingDocument.created_at.desc())
            )
            return list(result.scalars().all())
