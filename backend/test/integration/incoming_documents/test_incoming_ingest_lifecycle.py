"""来文错峰接入在真实 PostgreSQL 上的生命周期验收。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import yuxi.services.incoming_ingest_worker as worker_module
from sqlalchemy import delete, select
from yuxi.repositories.incoming_ingest_repository import IncomingIngestRepository
from yuxi.services.incoming_ingest_worker import IncomingIngestWorkerService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import IncomingIngestJob


async def _cleanup(source_system: str) -> None:
    """清理本用例登记的任务，避免共享验收库残留历史任务。"""
    async with pg_manager.get_async_session_context() as session:
        await session.execute(delete(IncomingIngestJob).where(IncomingIngestJob.source_system == source_system))


async def _register_history(*, source_system: str, source_document_id: str) -> str:
    async with pg_manager.get_async_session_context() as session:
        repository = IncomingIngestRepository(session)
        result = await repository.register_historical(
            source_system=source_system,
            documents=[
                {
                    "source_document_id": source_document_id,
                    "document_metadata": {"source_doc_id": source_document_id, "title": "集成测试历史来文"},
                    "file_manifest": [],
                }
            ],
            actor_uid="integration-test",
        )
        assert result[0].job_id is not None
        return result[0].job_id


async def _register_history_documents(*, source_system: str, source_document_ids: list[str]):
    async with pg_manager.get_async_session_context() as session:
        return await IncomingIngestRepository(session).register_historical(
            source_system=source_system,
            documents=[
                {
                    "source_document_id": source_document_id,
                    "document_metadata": {
                        "source_doc_id": source_document_id,
                        "title": f"集成测试历史来文 {source_document_id}",
                    },
                    "file_manifest": [],
                }
                for source_document_id in source_document_ids
            ],
            actor_uid="integration-test",
        )


@pytest.mark.integration
async def test_stale_history_message_after_0730_does_not_start_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    """若移除 Worker 的时间门禁，07:30 后延迟 Redis 消息会错误占用解析资源。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    source_system = f"integration-incoming-{uuid4().hex}"
    job_id = await _register_history(source_system=source_system, source_document_id="old-redis-message")
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
        await _register_history(source_system=source_system, source_document_id="older-history")
        async with pg_manager.get_async_session_context() as session:
            repository = IncomingIngestRepository(session)
            immediate = await repository.register_immediate(
                source_system=source_system,
                source_document_id="new-immediate",
                document_metadata={"source_doc_id": "new-immediate", "title": "即时来文"},
                file_manifest=[],
                actor_uid="integration-test",
            )
            database_now = await repository.database_now()
            historical_window_time = database_now.replace(hour=11, minute=0, second=0, microsecond=0)
            if historical_window_time < database_now:
                historical_window_time += timedelta(days=1)
            claim = await repository.claim_next(
                instance_id="integration-dispatcher",
                now=historical_window_time,
                concurrency=1,
            )
            assert immediate.job_id is not None
            assert claim is not None and claim.job_id == immediate.job_id
    finally:
        await _cleanup(source_system)
        await pg_manager.close()


@pytest.mark.integration
async def test_reverse_overlapping_history_registrations_are_idempotent() -> None:
    """反序请求必须按稳定身份顺序加锁，避免死锁或重复任务。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    source_system = f"integration-incoming-{uuid4().hex}"
    try:
        first, second = await asyncio.wait_for(
            asyncio.gather(
                _register_history_documents(
                    source_system=source_system,
                    source_document_ids=["DOC-1", "DOC-2"],
                ),
                _register_history_documents(
                    source_system=source_system,
                    source_document_ids=["DOC-2", "DOC-1"],
                ),
            ),
            timeout=10,
        )

        assert {item.source_document_id for item in first} == {"DOC-1", "DOC-2"}
        assert {item.source_document_id for item in second} == {"DOC-1", "DOC-2"}
        async with pg_manager.get_async_session_context() as session:
            jobs = list(
                (
                    await session.scalars(
                        select(IncomingIngestJob).where(IncomingIngestJob.source_system == source_system)
                    )
                ).all()
            )
        assert len(jobs) == 2
        assert {job.source_document_id for job in jobs} == {"DOC-1", "DOC-2"}
    finally:
        await _cleanup(source_system)
        await pg_manager.close()
