"""来文错峰接入在真实 PostgreSQL 上的生命周期验收。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

import yuxi.services.incoming_ingest_worker as worker_module
from yuxi.repositories.incoming_ingest_repository import IncomingIngestRepository
from yuxi.services.incoming_ingest_worker import IncomingIngestWorkerService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import IncomingIngestBatch, IncomingIngestBatchItem, IncomingIngestJob


async def _cleanup(source_system: str) -> None:
    """按外键反向清理本用例登记的任务，避免共享验收库残留历史任务。"""
    async with pg_manager.get_async_session_context() as session:
        await session.execute(
            delete(IncomingIngestBatchItem).where(IncomingIngestBatchItem.source_system == source_system)
        )
        await session.execute(delete(IncomingIngestBatch).where(IncomingIngestBatch.source_system == source_system))
        await session.execute(delete(IncomingIngestJob).where(IncomingIngestJob.source_system == source_system))


async def _register_submitted_history(*, source_system: str, source_document_id: str) -> str:
    async with pg_manager.get_async_session_context() as session:
        repository = IncomingIngestRepository(session)
        batch = await repository.create_batch(
            source_system=source_system,
            batch_key=f"batch-{source_document_id}",
            name="集成测试历史来文",
            created_by="integration-test",
        )
        result = await repository.register_items(
            batch_id=batch.batch_id,
            source_system=source_system,
            items=[
                {
                    "source_document_id": source_document_id,
                    "document_metadata": {"source_doc_id": source_document_id, "title": "集成测试历史来文"},
                    "file_manifest": [],
                }
            ],
            actor_uid="integration-test",
        )
        await repository.submit_batch(batch.batch_id)
        assert result[0].job_id is not None
        return result[0].job_id


@pytest.mark.integration
async def test_stale_history_message_after_0730_does_not_start_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    """若移除 Worker 的时间门禁，07:30 后延迟 Redis 消息会错误占用解析资源。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    source_system = f"integration-incoming-{uuid4().hex}"
    job_id = await _register_submitted_history(source_system=source_system, source_document_id="old-redis-message")
    queued_at = datetime(2026, 9, 7, 23, 29, 59, tzinfo=UTC)
    after_window = datetime(2026, 9, 7, 23, 30, tzinfo=UTC)
    executed = False
    try:
        async with pg_manager.get_async_session_context() as session:
            repository = IncomingIngestRepository(session)
            claim = await repository.claim_next(instance_id="integration-dispatcher", now=queued_at, concurrency=1)
            assert claim is not None and claim.job_id == job_id
            assert await repository.mark_queued(job_id=claim.job_id, delivery_token=claim.delivery_token)

        async def execute_job(*_args: str) -> None:
            nonlocal executed
            executed = True

        monkeypatch.setattr(worker_module, "utc_now", lambda: after_window)
        service = IncomingIngestWorkerService(session_factory=pg_manager.AsyncSession, execute_job=execute_job)

        assert await service.process(job_id=claim.job_id, delivery_token=claim.delivery_token) is False
        assert executed is False

        async with pg_manager.get_async_session_context() as session:
            job = await session.scalar(select(IncomingIngestJob).where(IncomingIngestJob.job_id == job_id))
            assert job is not None
            assert job.status == "pending"
            assert job.delivery_token is None
    finally:
        await _cleanup(source_system)
        await pg_manager.close()


@pytest.mark.integration
async def test_immediate_job_is_claimed_before_submitted_history() -> None:
    """若优先级排序回退为创建时间，工作时间小助手会被历史积压阻塞。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    source_system = f"integration-incoming-{uuid4().hex}"
    try:
        await _register_submitted_history(source_system=source_system, source_document_id="older-history")
        async with pg_manager.get_async_session_context() as session:
            repository = IncomingIngestRepository(session)
            immediate = await repository.register_immediate(
                source_system=source_system,
                source_document_id="new-immediate",
                document_metadata={"source_doc_id": "new-immediate", "title": "即时来文"},
                file_manifest=[],
                actor_uid="integration-test",
            )
            claim = await repository.claim_next(
                instance_id="integration-dispatcher",
                now=datetime(2026, 9, 7, 11, 0, tzinfo=UTC),
                concurrency=1,
            )
            assert immediate.job_id is not None
            assert claim is not None and claim.job_id == immediate.job_id
    finally:
        await _cleanup(source_system)
        await pg_manager.close()
