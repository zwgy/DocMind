from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository
from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.services.scheduled_job_dispatcher_service import ScheduledJobDispatcherService
from yuxi.services.scheduled_job_scheduler_service import ScheduledJobSchedulerService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import InboxItem, ScheduledJob, ScheduledJobRecipient, ScheduledJobRun


async def _create_due_at_job(*, owner_uid: str, recipient_uid: str, suffix: str) -> tuple[str, str]:
    """创建一个到期的一次性任务，供多实例和租约测试共用。"""
    job_id = new_scheduled_job_id("sj_")
    async with pg_manager.get_async_session_context() as session:
        repository = ScheduledJobRepository(session)
        now = await repository.database_now()
        session.add_all(
            [
                User(uid=owner_uid, username=f"owner_{suffix[:12]}", password_hash="not-used", role="user"),
                User(
                    uid=recipient_uid,
                    username=f"recipient_{suffix[:12]}",
                    password_hash="not-used",
                    role="user",
                ),
            ]
        )
        session.add(
            ScheduledJob(
                id=job_id,
                owner_uid=owner_uid,
                source_type="personal",
                source_snapshot={"entry_point": "integration_test", "thread_id": None},
                name="多实例调度验收",
                schedule_kind="at",
                run_at=now - timedelta(minutes=1),
                timezone="Asia/Shanghai",
                next_run_at=now - timedelta(minutes=1),
                action_type="notification",
                action_data={"type": "notification", "title": "验收通知", "content": "并发与租约验收"},
                status="active",
                created_by_uid=owner_uid,
            )
        )
        # 这些实体没有 ORM relationship，先落库才能为接收人建立可靠的外键引用。
        await session.flush()
        session.add(
            ScheduledJobRecipient(
                scheduled_job_id=job_id,
                recipient_uid=recipient_uid,
                recipient_name_snapshot="有效接收人",
            )
        )
    return job_id, recipient_uid


async def _cleanup_job(*, job_id: str, user_uids: list[str]) -> None:
    """按外键反向清理集成测试数据，避免影响其他并行验收。"""
    async with pg_manager.get_async_session_context() as session:
        run_ids = list(
            (await session.scalars(select(ScheduledJobRun.id).where(ScheduledJobRun.scheduled_job_id == job_id))).all()
        )
        if run_ids:
            await session.execute(delete(InboxItem).where(InboxItem.scheduled_job_run_id.in_(run_ids)))
            await session.execute(delete(ScheduledJobRun).where(ScheduledJobRun.id.in_(run_ids)))
        await session.execute(delete(ScheduledJobRecipient).where(ScheduledJobRecipient.scheduled_job_id == job_id))
        await session.execute(delete(ScheduledJob).where(ScheduledJob.id == job_id))
        await session.execute(delete(User).where(User.uid.in_(user_uids)))


@pytest.mark.integration
async def test_scheduler_coalesces_missed_interval_and_dispatcher_delivers_once():
    """真实 PostgreSQL 验证错过时点合并、租约认领和收件箱事件幂等。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    owner_uid = f"scheduler_owner_{suffix}"
    recipient_uid = f"scheduler_recipient_{suffix}"
    deleted_uid = f"scheduler_deleted_{suffix}"
    job_id = new_scheduled_job_id("sj_")
    run_ids: list[str] = []

    try:
        async with pg_manager.get_async_session_context() as session:
            now = await ScheduledJobRepository(session).database_now()
            session.add_all(
                [
                    User(uid=owner_uid, username=f"owner_{suffix[:12]}", password_hash="not-used", role="user"),
                    User(uid=recipient_uid, username=f"recipient_{suffix[:12]}", password_hash="not-used", role="user"),
                    User(
                        uid=deleted_uid,
                        username=f"deleted_{suffix[:12]}",
                        password_hash="not-used",
                        role="user",
                        is_deleted=1,
                    ),
                ]
            )
            session.add(
                ScheduledJob(
                    id=job_id,
                    owner_uid=owner_uid,
                    source_type="personal",
                    source_snapshot={"entry_point": "http_api", "thread_id": None},
                    name="错过运行合并验收",
                    schedule_kind="interval",
                    anchor_at=now - timedelta(minutes=120),
                    interval_seconds=60,
                    timezone="Asia/Shanghai",
                    next_run_at=now - timedelta(minutes=90),
                    action_type="notification",
                    action_data={"type": "notification", "title": "验收通知", "content": "按运行快照投递"},
                    status="active",
                    created_by_uid=owner_uid,
                )
            )
            session.add_all(
                [
                    ScheduledJobRecipient(
                        scheduled_job_id=job_id,
                        recipient_uid=recipient_uid,
                        recipient_name_snapshot="有效接收人",
                    ),
                    ScheduledJobRecipient(
                        scheduled_job_id=job_id,
                        recipient_uid=deleted_uid,
                        recipient_name_snapshot="已删除接收人",
                    ),
                ]
            )

        async with pg_manager.get_async_session_context() as session:
            repository = ScheduledJobRepository(session)
            runs = await repository.create_due_runs(batch_size=10)
            assert len(runs) == 1
            run_ids.append(runs[0].id)
            assert runs[0].scheduled_for <= await repository.database_now()
            job = await session.scalar(select(ScheduledJob).where(ScheduledJob.id == job_id))
            assert job is not None and job.next_run_at > await repository.database_now()

        async with pg_manager.get_async_session_context() as session:
            repository = ScheduledJobRepository(session)
            claims = await repository.claim_runs(instance_id="dispatcher-one", limit=1, lease_seconds=60)
            assert [run.id for run in claims] == run_ids

        async with pg_manager.get_async_session_context() as session:
            result = await ScheduledJobRepository(session).deliver_notification(
                run_id=run_ids[0], instance_id="dispatcher-one"
            )
            assert result == "partial"

        async with pg_manager.get_async_session_context() as session:
            run = await session.scalar(select(ScheduledJobRun).where(ScheduledJobRun.id == run_ids[0]))
            inboxes = list(
                (await session.scalars(select(InboxItem).where(InboxItem.scheduled_job_run_id == run_ids[0]))).all()
            )
            assert run is not None and run.status == "partial" and run.next_attempt_at is None
            assert {(item.recipient_uid, item.category, item.item_type) for item in inboxes} == {
                (owner_uid, "notification", "run_partial"),
                (recipient_uid, "notification", "notification_delivered"),
            }

        async with pg_manager.get_async_session_context() as session:
            assert await ScheduledJobRepository(session).create_due_runs(batch_size=10) == []
    finally:
        async with pg_manager.get_async_session_context() as session:
            if run_ids:
                await session.execute(delete(InboxItem).where(InboxItem.scheduled_job_run_id.in_(run_ids)))
                await session.execute(delete(ScheduledJobRun).where(ScheduledJobRun.id.in_(run_ids)))
            await session.execute(delete(ScheduledJobRecipient).where(ScheduledJobRecipient.scheduled_job_id == job_id))
            await session.execute(delete(ScheduledJob).where(ScheduledJob.id == job_id))
            await session.execute(delete(User).where(User.uid.in_([owner_uid, recipient_uid, deleted_uid])))
        # pytest 默认每个异步测试使用独立事件循环，测试间不得复用 asyncpg 连接池。
        await pg_manager.close()


@pytest.mark.integration
async def test_two_scheduler_instances_create_one_run_and_two_dispatchers_claim_once():
    """两份独立轮询同时执行时，行锁只能让一个实例推进同一个任务。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    owner_uid = f"scheduler_race_owner_{suffix}"
    recipient_uid = f"scheduler_race_recipient_{suffix}"
    job_id = ""
    try:
        job_id, _ = await _create_due_at_job(owner_uid=owner_uid, recipient_uid=recipient_uid, suffix=suffix)
        scheduler_one = ScheduledJobSchedulerService(pg_manager.AsyncSession)
        scheduler_two = ScheduledJobSchedulerService(pg_manager.AsyncSession)

        scheduled_counts = await asyncio.gather(
            scheduler_one.schedule_once(batch_size=10),
            scheduler_two.schedule_once(batch_size=10),
        )
        assert sorted(scheduled_counts) == [0, 1]

        async with pg_manager.get_async_session_context() as session:
            run_ids = list(
                (
                    await session.scalars(select(ScheduledJobRun.id).where(ScheduledJobRun.scheduled_job_id == job_id))
                ).all()
            )
        assert len(run_ids) == 1

        dispatcher_one = ScheduledJobDispatcherService(pg_manager.AsyncSession)
        dispatcher_two = ScheduledJobDispatcherService(pg_manager.AsyncSession)
        claims = await asyncio.gather(
            dispatcher_one.claim_once(instance_id="dispatcher-race-one", limit=1, lease_seconds=60),
            dispatcher_two.claim_once(instance_id="dispatcher-race-two", limit=1, lease_seconds=60),
        )
        assert sorted(claims, key=len) == [[], run_ids]

        claimed_by = "dispatcher-race-one" if claims[0] else "dispatcher-race-two"
        assert (
            await ScheduledJobDispatcherService(pg_manager.AsyncSession).dispatch_notification(
                run_id=run_ids[0], instance_id=claimed_by
            )
            == "succeeded"
        )

        async with pg_manager.get_async_session_context() as session:
            notifications = list(
                (
                    await session.scalars(
                        select(InboxItem).where(
                            InboxItem.scheduled_job_run_id == run_ids[0],
                            InboxItem.category == "notification",
                        )
                    )
                ).all()
            )
            assert len(notifications) == 1
    finally:
        if job_id:
            await _cleanup_job(job_id=job_id, user_uids=[owner_uid, recipient_uid])
        await pg_manager.close()


@pytest.mark.integration
async def test_expired_lease_is_handed_over_without_duplicate_notification():
    """旧实例租约失效后不能完成投递，新实例接管后只产生一条通知。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    owner_uid = f"lease_owner_{suffix}"
    recipient_uid = f"lease_recipient_{suffix}"
    job_id = ""
    try:
        job_id, _ = await _create_due_at_job(owner_uid=owner_uid, recipient_uid=recipient_uid, suffix=suffix)
        scheduler = ScheduledJobSchedulerService(pg_manager.AsyncSession)
        assert await scheduler.schedule_once(batch_size=10) == 1

        dispatcher = ScheduledJobDispatcherService(pg_manager.AsyncSession)
        run_ids = await dispatcher.claim_once(instance_id="dispatcher-old", limit=1, lease_seconds=1)
        assert len(run_ids) == 1

        async with pg_manager.get_async_session_context() as session:
            run = await session.scalar(select(ScheduledJobRun).where(ScheduledJobRun.id == run_ids[0]))
            assert run is not None
            # 显式过期租约，模拟实例在动作完成前崩溃；不依赖墙钟等待。
            run.lease_expires_at = await ScheduledJobRepository(session).database_now() - timedelta(seconds=1)

        takeover_ids = await dispatcher.claim_once(instance_id="dispatcher-new", limit=1, lease_seconds=60)
        assert takeover_ids == run_ids
        assert await dispatcher.dispatch_notification(run_id=run_ids[0], instance_id="dispatcher-old") is None
        assert await dispatcher.dispatch_notification(run_id=run_ids[0], instance_id="dispatcher-new") == "succeeded"

        async with pg_manager.get_async_session_context() as session:
            run = await session.scalar(select(ScheduledJobRun).where(ScheduledJobRun.id == run_ids[0]))
            notifications = list(
                (
                    await session.scalars(
                        select(InboxItem).where(
                            InboxItem.scheduled_job_run_id == run_ids[0],
                            InboxItem.category == "notification",
                        )
                    )
                ).all()
            )
            assert run is not None and run.status == "succeeded" and run.attempt_count == 2
            assert len(notifications) == 1
    finally:
        if job_id:
            await _cleanup_job(job_id=job_id, user_uids=[owner_uid, recipient_uid])
        await pg_manager.close()


@pytest.mark.integration
async def test_retry_limit_finishes_on_fifth_attempt_and_writes_one_notification_event():
    """可重试错误按五次封顶，终态清理租约且所有者只收到一条异常事件。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    owner_uid = f"retry_owner_{suffix}"
    recipient_uid = f"retry_recipient_{suffix}"
    job_id = ""
    try:
        job_id, _ = await _create_due_at_job(owner_uid=owner_uid, recipient_uid=recipient_uid, suffix=suffix)
        assert await ScheduledJobSchedulerService(pg_manager.AsyncSession).schedule_once(batch_size=10) == 1

        dispatcher = ScheduledJobDispatcherService(pg_manager.AsyncSession)
        run_id = ""
        for attempt in range(1, 6):
            claimed = await dispatcher.claim_once(instance_id="dispatcher-retry", limit=1, lease_seconds=60)
            assert len(claimed) == 1
            run_id = claimed[0]
            result = await dispatcher.retry_or_fail(
                run_id=run_id,
                instance_id="dispatcher-retry",
                max_attempts=5,
                error_code="transient_delivery_error",
                error_message="模拟可重试失败",
            )
            assert result == ("failed" if attempt == 5 else "pending")
            if attempt < 5:
                async with pg_manager.get_async_session_context() as session:
                    run = await session.scalar(select(ScheduledJobRun).where(ScheduledJobRun.id == run_id))
                    assert run is not None and run.next_attempt_at is not None
                    # 测试退避状态机时将下一次尝试提前，不把验收时间浪费在实际退避等待上。
                    run.next_attempt_at = await ScheduledJobRepository(session).database_now()

        async with pg_manager.get_async_session_context() as session:
            run = await session.scalar(select(ScheduledJobRun).where(ScheduledJobRun.id == run_id))
            notification_events = list(
                (
                    await session.scalars(
                        select(InboxItem).where(
                            InboxItem.scheduled_job_run_id == run_id,
                            InboxItem.category == "notification",
                        )
                    )
                ).all()
            )
            assert run is not None
            assert run.status == "failed" and run.attempt_count == 5 and run.finished_at is not None
            assert run.lease_owner is None and run.lease_expires_at is None and run.next_attempt_at is None
            assert [(item.recipient_uid, item.item_type) for item in notification_events] == [
                (owner_uid, "run_failed")
            ]
    finally:
        if job_id:
            await _cleanup_job(job_id=job_id, user_uids=[owner_uid, recipient_uid])
        await pg_manager.close()


@pytest.mark.integration
async def test_agent_run_status_creates_terminal_unread_task_event():
    """Agent Worker 的终态必须回写调度运行，不能只停留在聊天运行表。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    owner_uid = f"agent_status_owner_{suffix}"
    job_id = new_scheduled_job_id("sj_")
    run_id = new_scheduled_job_id("sjr_")
    try:
        async with pg_manager.get_async_session_context() as session:
            repository = ScheduledJobRepository(session)
            now = await repository.database_now()
            session.add(
                User(uid=owner_uid, username=f"agent_owner_{suffix[:12]}", password_hash="not-used", role="user")
            )
            session.add(
                ScheduledJob(
                    id=job_id,
                    owner_uid=owner_uid,
                    source_type="personal",
                    source_snapshot={"entry_point": "integration_test", "thread_id": None},
                    name="Agent 状态回写验收",
                    schedule_kind="at",
                    run_at=now - timedelta(minutes=1),
                    timezone="Asia/Shanghai",
                    next_run_at=None,
                    action_type="agent",
                    action_data={
                        "type": "agent",
                        "agent_slug": "assistant",
                        "instruction": "整理待办",
                        "timeout_seconds": 300,
                    },
                    status="active",
                    created_by_uid=owner_uid,
                )
            )
            await session.flush()
            session.add(
                ScheduledJobRun(
                    id=run_id,
                    scheduled_job_id=job_id,
                    scheduled_for=now,
                    status="queued",
                    attempt_count=1,
                    action_type="agent",
                    action_snapshot={
                        "type": "agent",
                        "agent_slug": "assistant",
                        "instruction": "整理待办",
                        "timeout_seconds": 300,
                    },
                    recipient_snapshot=[{"uid": owner_uid, "name": "agent owner"}],
                    agent_run_id=f"run_{suffix}",
                    conversation_id="1",
                )
            )
            await session.flush()
            await repository.mark_agent_run_queued(
                run=await session.get(ScheduledJobRun, run_id),
                job=await session.get(ScheduledJob, job_id),
                agent_run_id=f"run_{suffix}",
                conversation_id="1",
                now=now,
            )

        async with pg_manager.get_async_session_context() as session:
            repository = ScheduledJobRepository(session)
            assert await repository.sync_agent_run_status(agent_run_id=f"run_{suffix}", agent_status="running")
            assert await repository.sync_agent_run_status(agent_run_id=f"run_{suffix}", agent_status="completed")

        async with pg_manager.get_async_session_context() as session:
            run = await session.get(ScheduledJobRun, run_id)
            events = list(
                (
                    await session.scalars(
                        select(InboxItem)
                        .where(InboxItem.scheduled_job_run_id == run_id, InboxItem.category == "task")
                        .order_by(InboxItem.created_at.asc())
                    )
                ).all()
            )
            assert run is not None and run.status == "succeeded" and run.finished_at is not None
            assert [event.item_type for event in events] == ["agent_run_queued", "agent_run_succeeded"]
            assert all(not event.is_read for event in events)
    finally:
        await _cleanup_job(job_id=job_id, user_uids=[owner_uid])
        await pg_manager.close()
