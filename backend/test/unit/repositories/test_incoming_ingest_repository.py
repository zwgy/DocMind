from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.repositories import incoming_ingest_repository as ingest_repository_module
from yuxi.repositories.incoming_ingest_repository import IncomingIngestRepository, is_historical_window
from yuxi.storage.postgres.models_knowledge import Base, IncomingIngestJob


@pytest.fixture
async def repository(monkeypatch):
    """仅建来文接入表，使用真实 ORM 状态验证仓储的事务内决策。"""
    # 领取断言使用固定时间，登记时间也必须固定，避免真实时钟越过测试时间后产生假失败。
    monkeypatch.setattr(
        ingest_repository_module,
        "utc_now_naive",
        lambda: datetime(2026, 9, 7, 9, 0),
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[IncomingIngestJob.__table__],
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
    assert is_historical_window(datetime(2026, 9, 7, 11, 15, tzinfo=UTC), start_time="19:00", end_time="06:30")
    assert not is_historical_window(datetime(2026, 9, 7, 10, 30, tzinfo=UTC), start_time="19:00", end_time="06:30")


@pytest.mark.asyncio
async def test_registered_history_is_claimed_in_configured_window(repository) -> None:
    """历史任务登记成功后无需提交批次，下一次窗口内扫描即可领取。"""
    results = await repository.register_historical(
        source_system="legacy-oa",
        documents=[
            {
                "source_document_id": "history-1",
                "document_metadata": {"source_doc_id": "history-1", "title": "历史来文"},
                "file_manifest": [
                    {
                        "source_file_id": "main-1",
                        "filename": "main.pdf",
                        "source_url": "https://oa.example/doc/1",
                    }
                ],
            }
        ],
        actor_uid="admin",
    )
    assert results[0].status == "accepted"

    now = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    claim = await repository.claim_next(instance_id="dispatcher", now=now, concurrency=1)

    assert claim is not None
    assert claim.job_id == results[0].job_id


@pytest.mark.asyncio
async def test_worker_does_not_start_queued_history_after_window_closes(repository) -> None:
    """队列已有消息但时间窗口已关闭时，Worker 必须在下载前归还历史任务。"""
    items = await repository.register_historical(
        source_system="legacy-oa",
        documents=[
            {
                "source_document_id": "paused-history",
                "document_metadata": {"source_doc_id": "paused-history"},
                "file_manifest": [],
            }
        ],
        actor_uid="admin",
    )
    now = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    claim = await repository.claim_next(instance_id="dispatcher", now=now, concurrency=1)
    assert claim is not None and claim.job_id == items[0].job_id
    assert await repository.mark_queued(job_id=claim.job_id, delivery_token=claim.delivery_token)

    assert (
        await repository.start_delivery(
            job_id=claim.job_id,
            delivery_token=claim.delivery_token,
            worker_id="worker",
            now=datetime(2026, 9, 7, 23, 30, tzinfo=UTC),
        )
        is None
    )


@pytest.mark.asyncio
async def test_immediate_job_is_claimed_before_submitted_history(repository) -> None:
    """错误的优先级排序会让工作时间的小助手请求排在历史积压之后。"""
    await repository.register_historical(
        source_system="legacy-oa",
        documents=[
            {
                "source_document_id": "history-first",
                "document_metadata": {"source_doc_id": "history-first"},
                "file_manifest": [],
            }
        ],
        actor_uid="admin",
    )
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


async def test_retry_terminal_job_resets_delivery_state_and_preserves_priority(repository) -> None:
    """人工重试必须只恢复可安全重新投递的终态任务。"""
    registered = await repository.register_immediate(
        source_system="oa",
        source_document_id="retryable-job",
        document_metadata={"source_doc_id": "retryable-job"},
        file_manifest=[],
        actor_uid="admin",
    )
    claimed = await repository.claim_next(
        instance_id="dispatcher-1",
        now=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        concurrency=1,
    )
    assert claimed is not None
    started = await repository.start_delivery(
        job_id=registered.job_id,
        delivery_token=claimed.delivery_token,
        worker_id="worker-1",
        now=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
    )
    assert started is not None
    await repository.finish_delivery(
        job_id=registered.job_id,
        delivery_token=claimed.delivery_token,
        worker_id="worker-1",
        status="failed",
        error_message="附件下载超时",
    )
    failed = await repository.get_by_source_identity(source_system="oa", source_document_id="retryable-job")
    assert failed is not None
    assert failed.stage == "failed"

    retried = await repository.retry_terminal_job(job_id=registered.job_id, actor_uid="admin")

    assert retried is not None
    assert retried.status == "pending"
    assert retried.stage == "registered"
    assert retried.priority == "immediate"
    assert retried.attempt_count == 0
    assert retried.delivery_token is None
    assert retried.lease_owner is None
    assert retried.processing_error is None
    assert retried.next_attempt_at is not None


async def test_refresh_terminal_job_source_replaces_manifest_before_retry(repository) -> None:
    """下载地址失效后，只允许在终态任务上换入新的来源清单。"""
    registered = await repository.register_immediate(
        source_system="oa",
        source_document_id="refreshable-job",
        document_metadata={"source_doc_id": "refreshable-job", "title": "旧标题"},
        file_manifest=[{"source_file_id": "main", "source_url": "https://old.example/doc"}],
        actor_uid="admin",
    )
    job = await repository.get_by_source_identity(source_system="oa", source_document_id="refreshable-job")
    assert job is not None
    job.status = "failed"
    job.stage = "failed"
    await repository.db.flush()

    refreshed = await repository.refresh_terminal_job_source(
        job_id=registered.job_id,
        document_metadata={"source_doc_id": "refreshable-job", "title": "新标题"},
        file_manifest=[{"source_file_id": "main", "source_url": "https://new.example/doc"}],
        actor_uid="admin",
    )

    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.stage == "registered"
    assert refreshed.document_metadata["title"] == "新标题"
    assert refreshed.file_manifest[0]["source_url"] == "https://new.example/doc"


async def test_retry_non_terminal_job_is_rejected(repository) -> None:
    registered = await repository.register_immediate(
        source_system="oa",
        source_document_id="running-job",
        document_metadata={"source_doc_id": "running-job"},
        file_manifest=[],
        actor_uid="admin",
    )

    retried = await repository.retry_terminal_job(job_id=registered.job_id, actor_uid="admin")

    assert retried is None


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


def _historical_document(source_document_id: str, *, files: list[dict] | None = None) -> dict:
    return {
        "source_document_id": source_document_id,
        "document_metadata": {"source_doc_id": source_document_id, "title": f"来文 {source_document_id}"},
        "file_manifest": files
        or [
            {
                "source_file_id": "main",
                "filename": "main.pdf",
                "source_url": f"https://attachments.test/{source_document_id}.pdf",
                "is_main_file": True,
            }
        ],
    }


@pytest.mark.asyncio
async def test_register_historical_returns_results_in_request_order(repository) -> None:
    results = await repository.register_historical(
        source_system="oa",
        documents=[_historical_document("DOC-2"), _historical_document("DOC-1")],
        actor_uid="admin",
    )

    assert [(item.source_document_id, item.status) for item in results] == [
        ("DOC-2", "accepted"),
        ("DOC-1", "accepted"),
    ]


@pytest.mark.asyncio
async def test_register_historical_treats_reordered_files_as_same_input(repository) -> None:
    files = [
        {
            "source_file_id": "main",
            "filename": "main.pdf",
            "source_url": "https://attachments.test/main.pdf",
            "is_main_file": True,
        },
        {
            "source_file_id": "attachment",
            "filename": "attachment.pdf",
            "source_url": "https://attachments.test/attachment.pdf",
            "is_main_file": False,
        },
    ]
    first = await repository.register_historical(
        source_system="oa",
        documents=[_historical_document("DOC-1", files=files)],
        actor_uid="admin",
    )
    second = await repository.register_historical(
        source_system="oa",
        documents=[_historical_document("DOC-1", files=list(reversed(files)))],
        actor_uid="admin",
    )

    assert first[0].status == "accepted"
    assert second[0].status == "exists"
    assert second[0].job_id == first[0].job_id


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
async def test_register_historical_requeues_same_failed_or_cancelled_input(repository, terminal_status) -> None:
    first = await repository.register_historical(
        source_system="oa",
        documents=[_historical_document("DOC-1")],
        actor_uid="admin",
    )
    job = await repository.get_by_source_identity(source_system="oa", source_document_id="DOC-1")
    job.status = terminal_status
    job.stage = terminal_status
    job.processing_error = "temporary failure"
    await repository.db.flush()

    second = await repository.register_historical(
        source_system="oa",
        documents=[_historical_document("DOC-1")],
        actor_uid="admin",
    )

    assert second[0].status == "requeued"
    assert second[0].job_id == first[0].job_id
    assert job.status == "pending"
    assert job.processing_error is None


@pytest.mark.asyncio
async def test_register_historical_reports_conflict_without_replacing_existing_input(repository) -> None:
    await repository.register_historical(
        source_system="oa",
        documents=[_historical_document("DOC-1")],
        actor_uid="admin",
    )

    result = await repository.register_historical(
        source_system="oa",
        documents=[
            {
                **_historical_document("DOC-1"),
                "document_metadata": {"source_doc_id": "DOC-1", "title": "不同内容"},
            }
        ],
        actor_uid="admin",
    )

    job = await repository.get_by_source_identity(source_system="oa", source_document_id="DOC-1")
    assert result[0].status == "conflict"
    assert result[0].error_message == "来文输入与已有任务不一致"
    assert job.document_metadata["title"] == "来文 DOC-1"


@pytest.mark.asyncio
async def test_register_historical_does_not_downgrade_existing_immediate_job(repository) -> None:
    document = _historical_document("DOC-1")
    immediate = await repository.register_immediate(
        source_system="oa",
        source_document_id="DOC-1",
        document_metadata=document["document_metadata"],
        file_manifest=document["file_manifest"],
        actor_uid="user",
    )

    result = await repository.register_historical(
        source_system="oa",
        documents=[document],
        actor_uid="admin",
    )

    job = await repository.get_by_source_identity(source_system="oa", source_document_id="DOC-1")
    assert result[0].status == "exists"
    assert result[0].job_id == immediate.job_id
    assert job.priority == "immediate"


@pytest.mark.asyncio
async def test_register_immediate_requeues_same_failed_input(repository) -> None:
    document = _historical_document("DOC-1")
    first = await repository.register_immediate(
        source_system="oa",
        source_document_id="DOC-1",
        document_metadata=document["document_metadata"],
        file_manifest=document["file_manifest"],
        actor_uid="user",
    )
    job = await repository.get_by_source_identity(source_system="oa", source_document_id="DOC-1")
    job.status = "failed"
    job.stage = "failed"
    job.processing_error = "下载失败"
    await repository.db.flush()

    second = await repository.register_immediate(
        source_system="oa",
        source_document_id="DOC-1",
        document_metadata=document["document_metadata"],
        file_manifest=document["file_manifest"],
        actor_uid="user",
    )

    assert second.status == "requeued"
    assert second.job_id == first.job_id
    assert job.status == "pending"
    assert job.priority == "immediate"
    assert job.processing_error is None


@pytest.mark.asyncio
async def test_register_immediate_reports_conflict_for_different_input(repository) -> None:
    document = _historical_document("DOC-1")
    await repository.register_immediate(
        source_system="oa",
        source_document_id="DOC-1",
        document_metadata=document["document_metadata"],
        file_manifest=document["file_manifest"],
        actor_uid="user",
    )

    result = await repository.register_immediate(
        source_system="oa",
        source_document_id="DOC-1",
        document_metadata={"source_doc_id": "DOC-1", "title": "不同输入"},
        file_manifest=document["file_manifest"],
        actor_uid="user",
    )

    assert result.status == "conflict"
    assert result.error_message == "来文输入与已有任务不一致"


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
