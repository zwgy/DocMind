from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    DocumentBusinessExtractionItem,
    DocumentBusinessExtractionResult,
    DocumentBusinessExtractionRun,
)
from yuxi.utils.datetime_utils import utc_now_naive


class DocumentBusinessExtractionRepository:
    """文档业务抽取仓储；用 document_scope 区分来文和知识库文件挂载点。"""

    async def create_run(self, data: dict[str, Any]) -> DocumentBusinessExtractionRun:
        async with pg_manager.get_async_session_context() as session:
            record = DocumentBusinessExtractionRun(**data)
            session.add(record)
            return record

    async def update_run(self, run_id: str, data: dict[str, Any]) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(DocumentBusinessExtractionRun)
                .where(DocumentBusinessExtractionRun.run_id == run_id)
                .values(**data)
            )

    async def replace_result(
        self,
        *,
        run_id: str,
        result_data: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> DocumentBusinessExtractionResult:
        async with pg_manager.get_async_session_context() as session:
            # 同一 run_id 重算时先清空旧条目，避免管理端看到两版草稿。
            old = await session.execute(
                select(DocumentBusinessExtractionResult).where(DocumentBusinessExtractionResult.run_id == run_id)
            )
            old_result = old.scalar_one_or_none()
            if old_result is not None:
                await session.execute(
                    delete(DocumentBusinessExtractionItem).where(
                        DocumentBusinessExtractionItem.result_id == old_result.id
                    )
                )
                await session.delete(old_result)
                await session.flush()

            result = DocumentBusinessExtractionResult(run_id=run_id, **result_data)
            session.add(result)
            await session.flush()

            for item in items:
                session.add(DocumentBusinessExtractionItem(result_id=result.id, **item))

            return result

    async def get_success_by_document_markdown_model(
        self,
        *,
        document_scope: str,
        incoming_id: str | None,
        file_id: str | None,
        markdown_file: str,
        model_spec: str,
    ) -> dict[str, Any] | None:
        async with pg_manager.get_async_session_context() as session:
            query = (
                select(DocumentBusinessExtractionResult, DocumentBusinessExtractionRun)
                .join(
                    DocumentBusinessExtractionRun,
                    DocumentBusinessExtractionRun.run_id == DocumentBusinessExtractionResult.run_id,
                )
                .where(
                    DocumentBusinessExtractionResult.document_scope == document_scope,
                    DocumentBusinessExtractionRun.status == "success",
                    DocumentBusinessExtractionRun.model_spec == model_spec,
                    DocumentBusinessExtractionRun.run_metadata["markdown_file"].as_string() == markdown_file,
                )
            )
            if incoming_id:
                query = query.where(DocumentBusinessExtractionResult.incoming_id == incoming_id)
            if file_id:
                query = query.where(DocumentBusinessExtractionResult.file_id == file_id)

            row = await session.execute(query.order_by(DocumentBusinessExtractionResult.created_at.desc()).limit(1))
            pair = row.one_or_none()
            if pair is None:
                return None
            result, run = pair
            return await self._build_view(session, result, run)

    async def get_latest_by_incoming_id(self, incoming_id: str) -> dict[str, Any] | None:
        async with pg_manager.get_async_session_context() as session:
            row = await session.execute(
                select(DocumentBusinessExtractionResult, DocumentBusinessExtractionRun)
                .join(
                    DocumentBusinessExtractionRun,
                    DocumentBusinessExtractionRun.run_id == DocumentBusinessExtractionResult.run_id,
                )
                .where(
                    DocumentBusinessExtractionResult.document_scope == "incoming",
                    DocumentBusinessExtractionResult.incoming_id == incoming_id,
                    DocumentBusinessExtractionRun.status == "success",
                )
                .order_by(DocumentBusinessExtractionResult.created_at.desc())
                .limit(1)
            )
            pair = row.one_or_none()
            if pair is None:
                return None
            result, run = pair
            return await self._build_view(session, result, run)

    async def link_knowledge_file(self, *, incoming_id: str, kb_id: str, file_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            # 来文入库只补充关联，不重新业务抽取，保证来文阶段的抽取结果是唯一来源。
            await session.execute(
                update(DocumentBusinessExtractionRun)
                .where(DocumentBusinessExtractionRun.incoming_id == incoming_id)
                .values(kb_id=kb_id, file_id=file_id)
            )
            await session.execute(
                update(DocumentBusinessExtractionResult)
                .where(DocumentBusinessExtractionResult.incoming_id == incoming_id)
                .values(kb_id=kb_id, file_id=file_id)
            )
            await session.execute(
                update(DocumentBusinessExtractionItem)
                .where(DocumentBusinessExtractionItem.incoming_id == incoming_id)
                .values(kb_id=kb_id, file_id=file_id)
            )

    async def confirm_item(
        self,
        *,
        item_id: str,
        confirmed_data: dict[str, Any],
        operator_id: str | None,
        status: str = "confirmed",
    ) -> dict[str, Any] | None:
        async with pg_manager.get_async_session_context() as session:
            row = await session.execute(
                select(DocumentBusinessExtractionItem).where(DocumentBusinessExtractionItem.item_id == item_id)
            )
            item = row.scalar_one_or_none()
            if item is None:
                return None
            item.confirmed_data = confirmed_data
            item.status = status
            item.confirmed_by = operator_id
            item.confirmed_at = utc_now_naive()
            return self._item_to_dict(item)

    async def _build_view(
        self,
        session,
        result: DocumentBusinessExtractionResult,
        run: DocumentBusinessExtractionRun,
    ) -> dict[str, Any]:
        items_row = await session.execute(
            select(DocumentBusinessExtractionItem)
            .where(DocumentBusinessExtractionItem.result_id == result.id)
            .order_by(DocumentBusinessExtractionItem.id.asc())
        )
        return {
            "run_id": result.run_id,
            "document_scope": result.document_scope,
            "incoming_id": result.incoming_id,
            "kb_id": result.kb_id,
            "file_id": result.file_id,
            "categories": result.categories or {},
            "confirmed_categories": result.confirmed_categories,
            "schema_ids": result.schema_ids or [],
            "status": result.status,
            "run_status": run.status,
            "run_metadata": run.run_metadata or {},
            "error": run.error,
            "items": [self._item_to_dict(item) for item in items_row.scalars().all()],
        }

    @staticmethod
    def _item_to_dict(item: DocumentBusinessExtractionItem) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "document_scope": item.document_scope,
            "incoming_id": item.incoming_id,
            "kb_id": item.kb_id,
            "file_id": item.file_id,
            "chunk_id": item.chunk_id,
            "item_type": item.item_type,
            "data": item.data or {},
            "confirmed_data": item.confirmed_data,
            "source_quote": item.source_quote,
            "status": item.status,
            "confirmed_by": item.confirmed_by,
            "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
        }
