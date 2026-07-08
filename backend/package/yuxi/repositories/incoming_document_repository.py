from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import IncomingDocument
from yuxi.utils.datetime_utils import utc_now_naive


class IncomingDocumentRepository:
    """来文独立表的仓储，只管理 incoming_documents，不读写 knowledge_files。"""

    # 只允许服务层明确声明的字段落库，避免外部 metadata 或表单字段误写业务列。
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
            # Repository 统一刷新 updated_at，保证 upsert/update_fields 的审计时间一致。
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
        # 管理端列表只做轻量筛选；复杂全文检索应走后续专门搜索接口，避免把管理页拖成检索服务。
        filters = []
        if status:
            filters.append(IncomingDocument.status == status)
        if knowledge_import_status:
            filters.append(IncomingDocument.knowledge_import_status == knowledge_import_status)
        if source_system:
            filters.append(IncomingDocument.source_system == source_system)
        if classification:
            filters.append(IncomingDocument.classification == classification)
        if keyword:
            cleaned_keyword = keyword.strip()
            if cleaned_keyword:
                pattern = f"%{cleaned_keyword}%"
                filters.append(
                    or_(
                        IncomingDocument.filename.ilike(pattern),
                        IncomingDocument.source_document_id.ilike(pattern),
                        IncomingDocument.source_key.ilike(pattern),
                    )
                )

        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 100)
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count()).select_from(IncomingDocument).where(*filters))
            result = await session.execute(
                select(IncomingDocument)
                .where(*filters)
                .order_by(IncomingDocument.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return list(result.scalars().all()), int(total or 0)

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
            # 相同外部单号重新上传时覆盖当前态，不保留版本历史，这是来文 v1 的明确取舍。
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
