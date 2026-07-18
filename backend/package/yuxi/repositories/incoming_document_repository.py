from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    DocumentBusinessExtractionItem,
    DocumentBusinessExtractionResult,
    DocumentBusinessExtractionRun,
    IncomingDocument,
    IncomingDocumentFile,
)
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

    @staticmethod
    def _latest_success_result_ids():
        """每份来文只使用最近一次成功抽取，避免重试历史污染查询和统计。"""
        return (
            select(func.max(DocumentBusinessExtractionResult.id).label("result_id"))
            .join(
                DocumentBusinessExtractionRun,
                DocumentBusinessExtractionRun.run_id == DocumentBusinessExtractionResult.run_id,
            )
            .join(
                IncomingDocument,
                IncomingDocument.incoming_id == DocumentBusinessExtractionResult.incoming_id,
            )
            .where(
                DocumentBusinessExtractionResult.document_scope == "incoming",
                DocumentBusinessExtractionRun.status == "success",
                # 重新解析期间不能发布上一版本的正式结果，所有调用方统一在此隔离。
                IncomingDocument.status == "ready",
            )
            .group_by(DocumentBusinessExtractionResult.incoming_id)
            .subquery()
        )

    @classmethod
    def _business_query_filters(
        cls,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        classifications: list[str] | None = None,
        item_types: list[str] | None = None,
        keyword: str | None = None,
    ) -> list:
        filters = []
        incoming_date = IncomingDocument.document_metadata["incoming_date"].as_string()
        if date_from:
            filters.append(incoming_date >= date_from)
        if date_to:
            filters.append(incoming_date <= date_to)

        normalized_classifications = [value.strip() for value in classifications or [] if value.strip()]
        if normalized_classifications:
            effective = func.coalesce(
                IncomingDocument.confirmed_classification,
                IncomingDocument.ai_classification,
            )
            filters.append(effective.in_(normalized_classifications))

        normalized_item_types = [value.strip() for value in item_types or [] if value.strip()]
        if normalized_item_types:
            latest_result_ids = cls._latest_success_result_ids()
            filters.append(
                IncomingDocument.incoming_id.in_(
                    select(DocumentBusinessExtractionItem.incoming_id)
                    .where(
                        DocumentBusinessExtractionItem.result_id.in_(select(latest_result_ids.c.result_id)),
                        DocumentBusinessExtractionItem.item_type.in_(normalized_item_types),
                    )
                    .distinct()
                )
            )

        if keyword := (keyword or "").strip():
            filters.append(
                or_(
                    IncomingDocument.document_metadata["title"].as_string().icontains(keyword, autoescape=True),
                    IncomingDocument.document_metadata["document_number"]
                    .as_string()
                    .icontains(keyword, autoescape=True),
                    IncomingDocument.incoming_id.in_(
                        select(IncomingDocumentFile.incoming_id).where(
                            IncomingDocumentFile.filename.icontains(keyword, autoescape=True)
                        )
                    ),
                )
            )
        return filters

    async def search_business_documents(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        classifications: list[str] | None = None,
        item_types: list[str] | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[IncomingDocument], int]:
        """按文档分页查询来文；附件和抽取条目只参与筛选，不改变文档计数。"""
        filters = self._business_query_filters(
            date_from=date_from,
            date_to=date_to,
            classifications=classifications,
            item_types=item_types,
            keyword=keyword,
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

    async def get_business_statistics(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        classifications: list[str] | None = None,
        item_types: list[str] | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """统计筛选后文档数，并区分条目类型的文档数和 detail 数。"""
        filters = self._business_query_filters(
            date_from=date_from,
            date_to=date_to,
            classifications=classifications,
            item_types=item_types,
            keyword=keyword,
        )
        filtered_ids = select(IncomingDocument.incoming_id).where(*filters)
        effective_classification = func.coalesce(
            IncomingDocument.confirmed_classification,
            IncomingDocument.ai_classification,
            "未分类",
        )
        incoming_month = func.substr(IncomingDocument.document_metadata["incoming_date"].as_string(), 1, 7)
        latest_result_ids = self._latest_success_result_ids()
        item_filters = [
            DocumentBusinessExtractionItem.incoming_id.in_(filtered_ids),
            DocumentBusinessExtractionItem.result_id.in_(select(latest_result_ids.c.result_id)),
        ]
        normalized_item_types = [value.strip() for value in item_types or [] if value.strip()]
        if normalized_item_types:
            item_filters.append(DocumentBusinessExtractionItem.item_type.in_(normalized_item_types))

        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(select(func.count()).select_from(IncomingDocument).where(*filters))
            classification_rows = await session.execute(
                select(effective_classification, func.count())
                .where(*filters)
                .group_by(effective_classification)
                .order_by(func.count().desc(), effective_classification.asc())
            )
            item_type_rows = await session.execute(
                select(
                    DocumentBusinessExtractionItem.item_type,
                    func.count(func.distinct(DocumentBusinessExtractionItem.incoming_id)),
                    func.count(DocumentBusinessExtractionItem.id),
                )
                .where(*item_filters)
                .group_by(DocumentBusinessExtractionItem.item_type)
                .order_by(func.count(func.distinct(DocumentBusinessExtractionItem.incoming_id)).desc())
            )
            month_rows = await session.execute(
                select(incoming_month, func.count())
                .where(*filters, incoming_month.is_not(None))
                .group_by(incoming_month)
                .order_by(incoming_month.asc())
            )

        return {
            "total": int(total or 0),
            "by_classification": [
                {"classification": classification, "document_count": int(count)}
                for classification, count in classification_rows.all()
            ],
            "by_item_type": [
                {"item_type": item_type, "document_count": int(document_count), "detail_count": int(detail_count)}
                for item_type, document_count, detail_count in item_type_rows.all()
            ],
            "by_month": [{"month": month, "document_count": int(count)} for month, count in month_rows.all()],
        }

    async def get_business_document_facets(self, incoming_ids: list[str]) -> dict[str, dict[str, Any]]:
        """批量补齐搜索结果需要的附件数和条目类型，避免逐文档查询。"""
        normalized_ids = list(dict.fromkeys(value for value in incoming_ids if value))
        facets = {incoming_id: {"attachment_count": 0, "item_types": []} for incoming_id in normalized_ids}
        if not normalized_ids:
            return facets

        latest_result_ids = self._latest_success_result_ids()
        async with pg_manager.get_async_session_context() as session:
            file_rows = await session.execute(
                select(IncomingDocumentFile.incoming_id, func.count())
                .where(IncomingDocumentFile.incoming_id.in_(normalized_ids))
                .group_by(IncomingDocumentFile.incoming_id)
            )
            item_rows = await session.execute(
                select(
                    DocumentBusinessExtractionItem.incoming_id,
                    DocumentBusinessExtractionItem.item_type,
                )
                .where(
                    DocumentBusinessExtractionItem.incoming_id.in_(normalized_ids),
                    DocumentBusinessExtractionItem.result_id.in_(select(latest_result_ids.c.result_id)),
                )
                .distinct()
                .order_by(
                    DocumentBusinessExtractionItem.incoming_id.asc(),
                    DocumentBusinessExtractionItem.item_type.asc(),
                )
            )

        for incoming_id, count in file_rows.all():
            facets[incoming_id]["attachment_count"] = int(count)
        for incoming_id, item_type in item_rows.all():
            facets[incoming_id]["item_types"].append(item_type)
        return facets
