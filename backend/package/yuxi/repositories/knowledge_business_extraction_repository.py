from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeBusinessExtractionItem,
    KnowledgeBusinessExtractionResult,
    KnowledgeBusinessExtractionRun,
)
from yuxi.utils.datetime_utils import utc_now_naive


class KnowledgeBusinessExtractionRepository:
    async def create_run(self, data: dict[str, Any]) -> KnowledgeBusinessExtractionRun:
        async with pg_manager.get_async_session_context() as session:
            record = KnowledgeBusinessExtractionRun(**data)
            session.add(record)
            return record

    async def update_run(self, run_id: str, data: dict[str, Any]) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeBusinessExtractionRun)
                .where(KnowledgeBusinessExtractionRun.run_id == run_id)
                .values(**data)
            )

    async def replace_result(
        self,
        *,
        run_id: str,
        result_data: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> KnowledgeBusinessExtractionResult:
        async with pg_manager.get_async_session_context() as session:
            old = await session.execute(
                select(KnowledgeBusinessExtractionResult).where(KnowledgeBusinessExtractionResult.run_id == run_id)
            )
            old_result = old.scalar_one_or_none()
            if old_result is not None:
                await session.execute(
                    delete(KnowledgeBusinessExtractionItem).where(
                        KnowledgeBusinessExtractionItem.result_id == old_result.id
                    )
                )
                await session.delete(old_result)
                await session.flush()

            result = KnowledgeBusinessExtractionResult(run_id=run_id, **result_data)
            session.add(result)
            await session.flush()

            for item in items:
                session.add(KnowledgeBusinessExtractionItem(result_id=result.id, **item))

            return result

    async def get_latest_by_file_id(self, file_id: str) -> dict[str, Any] | None:
        async with pg_manager.get_async_session_context() as session:
            result_row = await session.execute(
                select(KnowledgeBusinessExtractionResult)
                .where(KnowledgeBusinessExtractionResult.file_id == file_id)
                .order_by(KnowledgeBusinessExtractionResult.created_at.desc())
                .limit(1)
            )
            result = result_row.scalar_one_or_none()
            if result is None:
                return None

            items_row = await session.execute(
                select(KnowledgeBusinessExtractionItem)
                .where(KnowledgeBusinessExtractionItem.result_id == result.id)
                .order_by(KnowledgeBusinessExtractionItem.id.asc())
            )
            return {
                "run_id": result.run_id,
                "kb_id": result.kb_id,
                "file_id": result.file_id,
                "categories": result.categories or {},
                "confirmed_categories": result.confirmed_categories,
                "schema_ids": result.schema_ids or [],
                "status": result.status,
                "items": [self._item_to_dict(item) for item in items_row.scalars().all()],
            }

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
                select(KnowledgeBusinessExtractionItem).where(KnowledgeBusinessExtractionItem.item_id == item_id)
            )
            item = row.scalar_one_or_none()
            if item is None:
                return None
            item.confirmed_data = confirmed_data
            item.status = status
            item.confirmed_by = operator_id
            item.confirmed_at = utc_now_naive()
            return self._item_to_dict(item)

    @staticmethod
    def _item_to_dict(item: KnowledgeBusinessExtractionItem) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
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
