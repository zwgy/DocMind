from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import IncomingDocument, IncomingDocumentFile
from yuxi.utils.datetime_utils import utc_now_naive


class IncomingDocumentRepository:
    """来文及其附件的持久化边界。"""

    _document_fields = {
        "source_system",
        "source_function_id",
        "source_document_id",
        "document_metadata",
        "status",
        "ai_classification",
        "classification_confidence",
        "classification_evidence",
        "additional_classifications",
        "confirmed_classification",
        "review_status",
        "confirmed_by",
        "confirmed_at",
        "summary",
        "processing_error",
        "linked_kb_id",
        "knowledge_import_status",
        "knowledge_import_task_id",
        "knowledge_import_error",
        "created_by",
        "updated_by",
    }
    _file_fields = {
        "source_file_id",
        "source_url",
        "filename",
        "is_main_file",
        "content_hash",
        "file_size",
        "mime_type",
        "original_file_url",
        "markdown_file_url",
        "status",
        "processing_error",
        "linked_file_id",
        "knowledge_import_status",
        "knowledge_import_error",
    }

    @staticmethod
    def _sanitize(data: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
        sanitized = {key: value for key, value in data.items() if key in allowed}
        if sanitized:
            sanitized["updated_at"] = utc_now_naive()
        return sanitized

    async def get_by_source_identity(
        self, source_system: str, source_function_id: str, source_document_id: str
    ) -> IncomingDocument | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument).where(
                    IncomingDocument.source_system == source_system,
                    IncomingDocument.source_function_id == source_function_id,
                    IncomingDocument.source_document_id == source_document_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_by_incoming_id(self, incoming_id: str) -> IncomingDocument | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id))
            return result.scalar_one_or_none()

    async def upsert_document(self, incoming_id: str, data: dict[str, Any]) -> IncomingDocument:
        sanitized = self._sanitize(data, self._document_fields)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id))
            record = result.scalar_one_or_none()
            if record is None:
                record = IncomingDocument(incoming_id=incoming_id, **sanitized)
                session.add(record)
            else:
                for key, value in sanitized.items():
                    setattr(record, key, value)
            return record

    async def update_document(self, incoming_id: str, data: dict[str, Any]) -> IncomingDocument:
        sanitized = self._sanitize(data, self._document_fields)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id))
            record = result.scalar_one_or_none()
            if record is None:
                raise ValueError(f"Incoming document not found: {incoming_id}")
            for key, value in sanitized.items():
                setattr(record, key, value)
            return record

    async def get_file_by_identity(self, incoming_id: str, source_file_id: str) -> IncomingDocumentFile | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocumentFile).where(
                    IncomingDocumentFile.incoming_id == incoming_id,
                    IncomingDocumentFile.source_file_id == source_file_id,
                )
            )
            return result.scalar_one_or_none()

    async def upsert_file(self, incoming_id: str, incoming_file_id: str, data: dict[str, Any]) -> IncomingDocumentFile:
        sanitized = self._sanitize(data, self._file_fields)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocumentFile).where(
                    IncomingDocumentFile.incoming_id == incoming_id,
                    IncomingDocumentFile.source_file_id == sanitized["source_file_id"],
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                record = IncomingDocumentFile(incoming_id=incoming_id, incoming_file_id=incoming_file_id, **sanitized)
                session.add(record)
            else:
                for key, value in sanitized.items():
                    setattr(record, key, value)
            return record

    async def update_file(self, incoming_file_id: str, data: dict[str, Any]) -> IncomingDocumentFile:
        sanitized = self._sanitize(data, self._file_fields)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocumentFile).where(IncomingDocumentFile.incoming_file_id == incoming_file_id)
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise ValueError(f"Incoming document file not found: {incoming_file_id}")
            for key, value in sanitized.items():
                setattr(record, key, value)
            return record

    async def set_main_file(self, incoming_id: str, source_file_id: str) -> None:
        """在同一事务内切换主文件，避免部分提交时出现无主文件。"""
        async with pg_manager.get_async_session_context() as session:
            target = await session.scalar(
                select(IncomingDocumentFile.incoming_file_id).where(
                    IncomingDocumentFile.incoming_id == incoming_id,
                    IncomingDocumentFile.source_file_id == source_file_id,
                )
            )
            if target is None:
                raise ValueError(f"Incoming document file not found: {source_file_id}")
            await session.execute(
                update(IncomingDocumentFile)
                .where(IncomingDocumentFile.incoming_id == incoming_id)
                .values(is_main_file=False, updated_at=utc_now_naive())
            )
            await session.execute(
                update(IncomingDocumentFile)
                .where(IncomingDocumentFile.incoming_file_id == target)
                .values(is_main_file=True, updated_at=utc_now_naive())
            )

    async def list_files(self, incoming_id: str) -> list[IncomingDocumentFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocumentFile)
                .where(IncomingDocumentFile.incoming_id == incoming_id)
                .order_by(IncomingDocumentFile.is_main_file.desc(), IncomingDocumentFile.created_at.asc())
            )
            return list(result.scalars().all())

    async def get_file_for_source(
        self, *, source_system: str, source_function_id: str, source_document_id: str, source_file_id: str
    ) -> tuple[IncomingDocument, IncomingDocumentFile] | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument, IncomingDocumentFile)
                .join(IncomingDocumentFile, IncomingDocumentFile.incoming_id == IncomingDocument.incoming_id)
                .where(
                    IncomingDocument.source_system == source_system,
                    IncomingDocument.source_function_id == source_function_id,
                    IncomingDocument.source_document_id == source_document_id,
                    IncomingDocumentFile.source_file_id == source_file_id,
                )
            )
            return result.one_or_none()

    async def list_for_management(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        knowledge_import_status: str | None = None,
        keyword: str | None = None,
        source_system: str | None = None,
        classification: str | None = None,
    ) -> tuple[list[IncomingDocument], int]:
        filters = []
        if status:
            filters.append(IncomingDocument.status == status)
        if knowledge_import_status:
            filters.append(IncomingDocument.knowledge_import_status == knowledge_import_status)
        if source_system:
            filters.append(IncomingDocument.source_system == source_system)
        if classification:
            filters.append(
                func.coalesce(IncomingDocument.confirmed_classification, IncomingDocument.ai_classification)
                == classification
            )
        if keyword := (keyword or "").strip():
            pattern = f"%{keyword}%"
            filters.append(
                or_(
                    IncomingDocument.source_document_id.ilike(pattern),
                    IncomingDocument.document_metadata["title"].as_string().ilike(pattern),
                    IncomingDocument.document_metadata["document_number"].as_string().ilike(pattern),
                    IncomingDocument.incoming_id.in_(
                        select(IncomingDocumentFile.incoming_id).where(IncomingDocumentFile.filename.ilike(pattern))
                    ),
                )
            )

        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 100)
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count()).select_from(IncomingDocument).where(*filters))
            rows = await session.execute(
                select(IncomingDocument)
                .where(*filters)
                .order_by(IncomingDocument.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return list(rows.scalars().all()), int(total or 0)
