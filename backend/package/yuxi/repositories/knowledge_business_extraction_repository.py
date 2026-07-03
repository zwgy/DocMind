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

    async def get_success_by_file_markdown_model(
        self,
        *,
        file_id: str,
        markdown_file: str,
        model_spec: str,
    ) -> dict[str, Any] | None:
        return await self.get_latest_success_view_by_file_id(
            file_id=file_id,
            markdown_file=markdown_file,
            model_spec=model_spec,
        )

    async def get_latest_success_view_by_file_id(
        self,
        file_id: str,
        markdown_file: str | None = None,
        model_spec: str | None = None,
    ) -> dict[str, Any] | None:
        async with pg_manager.get_async_session_context() as session:
            query = (
                select(KnowledgeBusinessExtractionResult, KnowledgeBusinessExtractionRun)
                .join(
                    KnowledgeBusinessExtractionRun,
                    KnowledgeBusinessExtractionRun.run_id == KnowledgeBusinessExtractionResult.run_id,
                )
                .where(
                    KnowledgeBusinessExtractionResult.file_id == file_id,
                    KnowledgeBusinessExtractionRun.status == "success",
                )
            )
            if markdown_file:
                markdown_path = KnowledgeBusinessExtractionRun.run_metadata["markdown_file"].as_string()
                query = query.where(markdown_path == markdown_file)
            if model_spec:
                query = query.where(KnowledgeBusinessExtractionRun.model_spec == model_spec)
            row = await session.execute(query.order_by(KnowledgeBusinessExtractionResult.created_at.desc()).limit(1))
            pair = row.one_or_none()
            if pair is None:
                return None
            result, run = pair
            return await self._build_view(session, result, run)

    async def get_latest_run_by_file_id(
        self,
        file_id: str,
        markdown_file: str | None = None,
    ) -> dict[str, Any] | None:
        async with pg_manager.get_async_session_context() as session:
            query = select(KnowledgeBusinessExtractionRun).where(KnowledgeBusinessExtractionRun.file_id == file_id)
            if markdown_file:
                markdown_path = KnowledgeBusinessExtractionRun.run_metadata["markdown_file"].as_string()
                query = query.where(markdown_path == markdown_file)
            row = await session.execute(query.order_by(KnowledgeBusinessExtractionRun.created_at.desc()).limit(1))
            run = row.scalar_one_or_none()
            if run is None:
                return None
            return {
                "run_id": run.run_id,
                "kb_id": run.kb_id,
                "file_id": run.file_id,
                "status": run.status,
                "model_spec": run.model_spec,
                "run_metadata": run.run_metadata or {},
                "error": run.error,
            }

    async def _build_view(
        self,
        session,
        result: KnowledgeBusinessExtractionResult,
        run: KnowledgeBusinessExtractionRun,
    ) -> dict[str, Any]:
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
            "run_status": run.status,
            "run_metadata": run.run_metadata or {},
            "error": run.error,
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
