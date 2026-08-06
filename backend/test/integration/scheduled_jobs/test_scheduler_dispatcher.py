from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository
from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import InboxItem, ScheduledJob, ScheduledJobRecipient, ScheduledJobRun


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
            assert [(item.recipient_uid, item.category, item.item_type) for item in inboxes] == [
                (owner_uid, "task", "run_partial"),
                (recipient_uid, "notification", "notification_delivered"),
            ]

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
