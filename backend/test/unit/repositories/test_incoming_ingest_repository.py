from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.incoming_ingest_repository import IncomingIngestRepository, is_historical_window
from yuxi.storage.postgres.models_knowledge import Base, IncomingIngestBatch, IncomingIngestBatchItem, IncomingIngestJob


@pytest.fixture
async def repository():
    """仅建来文接入表，使用真实 ORM 状态验证仓储的事务内决策。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    IncomingIngestBatch.__table__,
                    IncomingIngestJob.__table__,
                    IncomingIngestBatchItem.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield IncomingIngestRepository(session)
    await engine.dispose()


def test_historical_window_uses_shanghai_boundaries() -> None:
    """错误地把 07:30 当作允许时间会使历史解析侵占工作时间。"""
    assert is_historical_window(datetime(2026, 9, 7, 10, 0, tzinfo=UTC))
    assert is_historical_window(datetime(2026, 9, 7, 23, 29, 59, tzinfo=UTC))
    assert not is_historical_window(datetime(2026, 9, 7, 23, 30, tzinfo=UTC))


def test_historical_window_accepts_runtime_configured_cross_midnight_boundaries() -> None:
    """手工调整窗口后，历史任务应按新边界而非写死的默认值启动。"""
    assert is_historical_window(
        datetime(2026, 9, 7, 11, 15, tzinfo=UTC), start_time="19:00", end_time="06:30"
    )
    assert not is_historical_window(
        datetime(2026, 9, 7, 10, 30, tzinfo=UTC), start_time="19:00", end_time="06:30"
    )


@pytest.mark.asyncio
async def test_registered_history_is_not_claimed_before_submit_or_when_paused(repository) -> None:
    """未提交或暂停批次中的历史成员不得因调度轮询而开始下载。"""
    batch = await repository.create_batch(
        source_system="legacy-oa",
        batch_key="2026-history",
        name="历史来文",
        created_by="admin",
    )
    results = await repository.register_items(
        batch_id=batch.batch_id,
        source_system="legacy-oa",
        items=[
            {
                "source_document_id": "history-1",
                "document_metadata": {"source_doc_id": "history-1", "title": "历史来文"},
                "file_manifest": [{"source_file_id": "main-1", "download_url": "https://oa.example/doc/1"}],
            }
        ],
        actor_uid="admin",
    )
    assert results[0].status == "accepted"

    now = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    assert await repository.claim_next(instance_id="dispatcher", now=now, concurrency=1) is None

    await repository.submit_batch(batch.batch_id)
    await repository.pause_batch(batch.batch_id)
    assert await repository.claim_next(instance_id="dispatcher", now=now, concurrency=1) is None


@pytest.mark.asyncio
async def test_worker_does_not_start_queued_history_after_batch_is_paused(repository) -> None:
    """队列已有消息后暂停批次时，Worker 必须在下载前归还历史任务。"""
    batch = await repository.create_batch(
        source_system="legacy-oa",
        batch_key="paused-after-queue",
        name="历史来文",
        created_by="admin",
    )
    items = await repository.register_items(
        batch_id=batch.batch_id,
        source_system="legacy-oa",
        items=[
            {
                "source_document_id": "paused-history",
                "document_metadata": {"source_doc_id": "paused-history"},
                "file_manifest": [],
            }
        ],
        actor_uid="admin",
    )
    await repository.submit_batch(batch.batch_id)
    now = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    claim = await repository.claim_next(instance_id="dispatcher", now=now, concurrency=1)
    assert claim is not None and claim.job_id == items[0].job_id
    assert await repository.mark_queued(job_id=claim.job_id, delivery_token=claim.delivery_token)
    await repository.pause_batch(batch.batch_id)

    assert (
        await repository.start_delivery(
            job_id=claim.job_id,
            delivery_token=claim.delivery_token,
            worker_id="worker",
            now=now,
        )
        is None
    )


@pytest.mark.asyncio
async def test_immediate_job_is_claimed_before_submitted_history(repository) -> None:
    """错误的优先级排序会让工作时间的小助手请求排在历史积压之后。"""
    history = await repository.create_batch(
        source_system="legacy-oa",
        batch_key="2026-history-priority",
        name="历史来文",
        created_by="admin",
    )
    await repository.register_items(
        batch_id=history.batch_id,
        source_system="legacy-oa",
        items=[
            {
                "source_document_id": "history-first",
                "document_metadata": {"source_doc_id": "history-first"},
                "file_manifest": [],
            }
        ],
        actor_uid="admin",
    )
    await repository.submit_batch(history.batch_id)
    immediate = await repository.register_immediate(
        source_system="legacy-oa",
        source_document_id="new-arrival",
        document_metadata={"source_doc_id": "new-arrival"},
        file_manifest=[],
        actor_uid="assistant",
    )

    claim = await repository.claim_next(
        instance_id="dispatcher",
        now=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
        concurrency=1,
    )

    assert claim is not None and claim.job_id == immediate.job_id
    assert claim.delivery_token


@pytest.mark.asyncio
async def test_dispatching_job_keeps_same_token_for_enqueue_retry(repository) -> None:
    """Redis 超时不代表消息未收到，重复投递不得生成能绕过旧消息的新令牌。"""
    job = await repository.register_immediate(
        source_system="legacy-oa",
        source_document_id="retry-same-token",
        document_metadata={"source_doc_id": "retry-same-token"},
        file_manifest=[],
        actor_uid="assistant",
    )
    now = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    first = await repository.claim_next(instance_id="dispatcher", now=now, concurrency=1)
    second = await repository.claim_next(instance_id="dispatcher", now=now, concurrency=1)

    assert first is not None and first.job_id == job.job_id
    assert second == first


@pytest.mark.asyncio
async def test_successful_delivery_records_succeeded_stage(repository) -> None:
    """管理页的阶段必须反映已完成任务，而不是永久显示注册阶段。"""
    registered = await repository.register_immediate(
        source_system="legacy-oa",
        source_document_id="terminal-stage",
        document_metadata={"source_doc_id": "terminal-stage"},
        file_manifest=[],
        actor_uid="assistant",
    )
    now = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    claim = await repository.claim_next(instance_id="dispatcher", now=now, concurrency=1)
    assert registered.job_id is not None
    assert claim is not None
    assert await repository.mark_queued(job_id=claim.job_id, delivery_token=claim.delivery_token)
    assert await repository.start_delivery(
        job_id=claim.job_id,
        delivery_token=claim.delivery_token,
        worker_id="worker",
        now=now,
    )

    assert await repository.finish_delivery(
        job_id=claim.job_id,
        delivery_token=claim.delivery_token,
        worker_id="worker",
        status="succeeded",
    )

    job = await repository.get_by_source_identity(source_system="legacy-oa", source_document_id="terminal-stage")
    assert job is not None
    assert job.stage == "succeeded"


@pytest.mark.asyncio
async def test_registering_same_source_identity_reuses_pending_job(repository) -> None:
    """丢失来源身份去重会让不同入口重复下载同一份来文。"""
    first = await repository.register_immediate(
        source_system="legacy-oa",
        source_document_id="same-document",
        document_metadata={"source_doc_id": "same-document"},
        file_manifest=[],
        actor_uid="assistant",
    )
    second = await repository.register_immediate(
        source_system="legacy-oa",
        source_document_id="same-document",
        document_metadata={"source_doc_id": "same-document"},
        file_manifest=[],
        actor_uid="assistant",
    )

    assert second.job_id == first.job_id
    assert second.reused


def test_claim_query_uses_postgresql_row_lock_and_stable_priority_order() -> None:
    """移除 SKIP LOCKED 或稳定排序会让多个 dispatcher 争抢同一空闲槽位。"""
    statement = IncomingIngestRepository.claim_statement(now=datetime(2026, 9, 7, 10, 0, tzinfo=UTC))
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "CASE" in sql
    assert "incoming_ingest_jobs.created_at ASC" in sql


def test_job_insert_uses_source_identity_conflict_target() -> None:
    """并发登记同一来源 ID 时，唯一键冲突必须复用既有任务而不是使整块提交失败。"""
    statement = IncomingIngestRepository.job_insert_statement(
        job_id="ij_test",
        source_system="legacy-oa",
        source_document_id="same-document",
        document_metadata={"source_doc_id": "same-document"},
        file_manifest=[],
        source_kind="history",
        priority="historical",
        actor_uid="admin",
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "ON CONFLICT (source_system, source_document_id) DO NOTHING" in sql
