from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    DocumentBusinessExtractionItem,
    DocumentBusinessExtractionResult,
    DocumentBusinessExtractionRun,
    IncomingDocument,
    IncomingDocumentFile,
)
from yuxi.storage.postgres.models_scheduled_jobs import (
    IncomingTaskBatch,
    ScheduledJob,
    ScheduledJobAuditLog,
    ScheduledJobCandidate,
)
from yuxi.utils.datetime_utils import utc_now_naive


class IncomingDocumentAuditReferenceError(ValueError):
    """来文已形成调度审计，物理删除会破坏来源追溯。"""

    code = "incoming_has_audit_reference"
    message = "来文已确认或存在定时任务审计引用，不能物理删除"

    def __init__(self):
        super().__init__(self.message)


class IncomingDocumentRepository:
    """来文及其附件的持久化边界。"""

    _document_fields = {
        "source_system",
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

    async def get_by_incoming_id(self, incoming_id: str) -> IncomingDocument | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id))
            return result.scalar_one_or_none()

    async def get_by_incoming_id_in_session(
        self, db_session: AsyncSession, incoming_id: str, *, for_update: bool = False
    ) -> IncomingDocument | None:
        """在调用方事务中读取来文，确认等跨表用例不能切换到仓储自建会话。"""
        statement = select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id)
        if for_update:
            statement = statement.with_for_update()
        return await db_session.scalar(statement)

    async def update_document_in_session(
        self, db_session: AsyncSession, incoming_id: str, data: dict[str, Any]
    ) -> IncomingDocument:
        """仅写入调用方会话；提交由跨表服务统一控制。"""
        record = await self.get_by_incoming_id_in_session(db_session, incoming_id, for_update=True)
        if record is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")
        for key, value in self._sanitize(data, self._document_fields).items():
            setattr(record, key, value)
        await db_session.flush()
        return record

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
        self, *, source_system: str, source_document_id: str, source_file_id: str
    ) -> tuple[IncomingDocument, IncomingDocumentFile] | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(IncomingDocument, IncomingDocumentFile)
                .join(IncomingDocumentFile, IncomingDocumentFile.incoming_id == IncomingDocument.incoming_id)
                .where(
                    IncomingDocument.source_system == source_system,
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
        include_archived: bool = False,
    ) -> tuple[list[IncomingDocument], int]:
        # 归档项仍可通过详情和来源链路访问，但默认不混入待处理的管理列表。
        filters = [] if include_archived else [IncomingDocument.archived_at.is_(None)]
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

    async def get_lifecycle_capabilities(self, incoming_ids: list[str]) -> dict[str, dict[str, Any]]:
        """批量返回来文归档/删除所需事实，避免前端依据展示状态猜测权限。"""
        normalized_ids = list(dict.fromkeys(value for value in incoming_ids if value))
        capabilities = {
            incoming_id: {
                "candidate_count": 0,
                "enabled_job_count": 0,
                "has_audit_reference": False,
            }
            for incoming_id in normalized_ids
        }
        if not normalized_ids:
            return capabilities

        async with pg_manager.get_async_session_context() as session:
            candidate_rows = await session.execute(
                select(ScheduledJobCandidate.incoming_id, func.count(ScheduledJobCandidate.id))
                .where(ScheduledJobCandidate.incoming_id.in_(normalized_ids))
                .group_by(ScheduledJobCandidate.incoming_id)
            )
            enabled_rows = await session.execute(
                select(ScheduledJobCandidate.incoming_id, func.count(ScheduledJob.id))
                .join(ScheduledJob, ScheduledJob.source_candidate_id == ScheduledJobCandidate.id)
                .where(ScheduledJobCandidate.incoming_id.in_(normalized_ids))
                .group_by(ScheduledJobCandidate.incoming_id)
            )
            audit_rows = await session.execute(
                select(ScheduledJobCandidate.incoming_id, func.count(ScheduledJobAuditLog.id))
                .join(ScheduledJobAuditLog, ScheduledJobAuditLog.candidate_id == ScheduledJobCandidate.id)
                .where(ScheduledJobCandidate.incoming_id.in_(normalized_ids))
                .group_by(ScheduledJobCandidate.incoming_id)
            )

        for incoming_id, count in candidate_rows.all():
            capabilities[incoming_id]["candidate_count"] = int(count)
        for incoming_id, count in enabled_rows.all():
            capabilities[incoming_id]["enabled_job_count"] = int(count)
        for incoming_id, count in audit_rows.all():
            capabilities[incoming_id]["has_audit_reference"] = bool(count)
        return capabilities

    async def archive_document(self, incoming_id: str, *, archived_by: str) -> IncomingDocument | None:
        """归档只隐藏管理列表，保留任务来源与全部审计记录。"""
        async with pg_manager.get_async_session_context() as session:
            document = (
                await session.execute(
                    select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id).with_for_update()
                )
            ).scalar_one_or_none()
            if document is None:
                return None
            if document.status in {"parsing", "extracting"}:
                raise ValueError("来文正在处理中，无法归档")
            document.archived_at = utc_now_naive()
            document.archived_by = archived_by
            document.updated_by = archived_by
            await session.commit()
            return document

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
        title: str | None = None,
        document_number: str | None = None,
        source_unit: str | None = None,
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

        metadata_filters = {
            "title": title,
            "document_number": document_number,
            "source_unit": source_unit,
        }
        for field, value in metadata_filters.items():
            if value := (value or "").strip():
                filters.append(IncomingDocument.document_metadata[field].as_string().icontains(value, autoescape=True))

        if keyword := (keyword or "").strip():
            filters.append(
                or_(
                    IncomingDocument.document_metadata["title"].as_string().icontains(keyword, autoescape=True),
                    IncomingDocument.document_metadata["document_number"]
                    .as_string()
                    .icontains(keyword, autoescape=True),
                    IncomingDocument.document_metadata["source_unit"].as_string().icontains(keyword, autoescape=True),
                    IncomingDocument.summary.icontains(keyword, autoescape=True),
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
        title: str | None = None,
        document_number: str | None = None,
        source_unit: str | None = None,
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
            title=title,
            document_number=document_number,
            source_unit=source_unit,
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
        title: str | None = None,
        document_number: str | None = None,
        source_unit: str | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """统计筛选后文档数，并区分条目类型的文档数和 detail 数。"""
        filters = self._business_query_filters(
            date_from=date_from,
            date_to=date_to,
            classifications=classifications,
            item_types=item_types,
            title=title,
            document_number=document_number,
            source_unit=source_unit,
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

    async def delete_cascade(self, incoming_id: str) -> tuple[IncomingDocument | None, list[IncomingDocumentFile]]:
        """在单事务内校验并删除来文主记录，返回被删的主记录与附件。

        删除规则与业务约束：
        - 状态处于 ``parsing`` / ``extracting`` 时拒绝（处理任务运行中）。
        - 已入库知识库（``knowledge_import_status`` 在 importing/partial/indexed 或
          ``linked_kb_id`` 非空）时拒绝，避免与 KB 文件状态不一致。
        - 已确认、已有候选审计记录或已启用来源任务时拒绝；这些记录必须保留来源。
        - 抽取运行记录不在外键级联链上，显式按 ``run_id`` 批量删除；结果与条目
          通过 ``DocumentBusinessExtractionResult.run_id`` 的 CASCADE 自动清理。

        调用方拿到 ``files`` 后应负责清理 MinIO 上的原文 / Markdown 对象，DB 是
        真相源，MinIO 失败不回滚本次删除（写入审计日志便于后续清理任务兜底）。
        """

        async with pg_manager.get_async_session_context() as session:
            document = (
                await session.execute(select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id))
            ).scalar_one_or_none()
            if document is None:
                return None, []
            if document.status in {"parsing", "extracting"}:
                raise ValueError("来文正在处理中，无法删除")
            if getattr(document, "knowledge_import_status", None) in {"importing", "partial", "indexed"} or getattr(
                document, "linked_kb_id", None
            ):
                raise ValueError("该来文已入库知识库，请先在知识库中删除对应文件后再清理")
            candidate_ids = (
                select(ScheduledJobCandidate.id)
                .where(ScheduledJobCandidate.incoming_id == incoming_id)
                .scalar_subquery()
            )
            has_audit_reference = (
                await session.execute(
                    select(ScheduledJobAuditLog.id).where(ScheduledJobAuditLog.candidate_id.in_(candidate_ids)).limit(1)
                )
            ).scalar_one_or_none() is not None
            has_enabled_job = (
                await session.execute(
                    select(ScheduledJob.id).where(ScheduledJob.source_candidate_id.in_(candidate_ids)).limit(1)
                )
            ).scalar_one_or_none() is not None
            if getattr(document, "review_status", None) == "confirmed" or has_audit_reference or has_enabled_job:
                raise IncomingDocumentAuditReferenceError()

            files = list(
                (
                    await session.execute(
                        select(IncomingDocumentFile).where(IncomingDocumentFile.incoming_id == incoming_id)
                    )
                )
                .scalars()
                .all()
            )
            run_ids = [
                row[0]
                for row in (
                    await session.execute(
                        select(DocumentBusinessExtractionRun.run_id).where(
                            DocumentBusinessExtractionRun.incoming_id == incoming_id
                        )
                    )
                ).all()
            ]
            if run_ids:
                await session.execute(
                    delete(DocumentBusinessExtractionRun).where(DocumentBusinessExtractionRun.run_id.in_(run_ids))
                )
            # 外键全部为 RESTRICT，删除只允许在没有任务/审计引用的草稿上显式逆序进行。
            await session.execute(delete(ScheduledJobCandidate).where(ScheduledJobCandidate.incoming_id == incoming_id))
            await session.execute(delete(IncomingTaskBatch).where(IncomingTaskBatch.incoming_id == incoming_id))
            await session.execute(delete(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id))
            await session.commit()
        return document, files

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
