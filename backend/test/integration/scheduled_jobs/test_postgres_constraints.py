from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledJob, ScheduledJobRun
from yuxi.storage.postgres.scheduled_jobs_migration import ensure_scheduled_jobs_schema
from yuxi.utils.datetime_utils import utc_now


def _personal_job(*, job_id: str, owner_uid: str, request_key: str, run_at):
    """构造满足第一版数据库契约的个人通知任务。"""
    return ScheduledJob(
        id=job_id,
        owner_uid=owner_uid,
        source_type="personal",
        create_request_key=request_key,
        create_request_hash="a" * 64,
        source_snapshot={"entry_point": "http_api", "thread_id": None},
        name="PostgreSQL 约束验收",
        schedule_kind="at",
        run_at=run_at,
        timezone="Asia/Shanghai",
        next_run_at=run_at,
        action_type="notification",
        action_data={"type": "notification", "title": "验收", "content": "数据库约束验证"},
        status="active",
        created_by_uid=owner_uid,
    )


@pytest.mark.integration
async def test_scheduled_job_migration_can_run_twice_against_postgres():
    """约束替换无法使用不存在的 ADD CONSTRAINT IF NOT EXISTS，必须实际验证 DROP/ADD 对可重复执行。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    try:
        async with pg_manager.async_engine.begin() as connection:
            await ensure_scheduled_jobs_schema(connection)
            await ensure_scheduled_jobs_schema(connection)
    finally:
        await pg_manager.close()


@pytest.mark.integration
async def test_scheduled_jobs_postgres_enforces_constraints_and_skip_locked():
    """必须使用真实 PostgreSQL，SQLite 不具备部分索引和 SKIP LOCKED 语义。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    uid = f"pytest_scheduled_{suffix}"
    username = f"pytest_scheduled_{suffix[:16]}"
    run_at = utc_now() + timedelta(minutes=5)
    job_id = new_scheduled_job_id("sj_")
    run_id = new_scheduled_job_id("sjr_")

    try:
        async with pg_manager.get_async_session_context() as session:
            session.add(User(uid=uid, username=username, password_hash="not-used", role="user"))
            session.add(_personal_job(job_id=job_id, owner_uid=uid, request_key=f"request-{suffix}", run_at=run_at))
            session.add(
                ScheduledJobRun(
                    id=run_id,
                    scheduled_job_id=job_id,
                    scheduled_for=run_at,
                    status="pending",
                    attempt_count=0,
                    next_attempt_at=utc_now(),
                    action_type="notification",
                    action_snapshot={"type": "notification", "title": "验收", "content": "数据库约束验证"},
                    recipient_snapshot=[{"uid": uid, "name": username}],
                )
            )

        async with pg_manager.get_async_session_context() as session:
            data_type = await session.scalar(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'scheduled_jobs' AND column_name = 'run_at'"
                )
            )
            partial_index = await session.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'scheduled_jobs' AND indexname = 'uq_sj_owner_create_request_key'"
                )
            )

        assert data_type == "timestamp with time zone"
        assert partial_index is not None and "WHERE (create_request_key IS NOT NULL)" in partial_index

        duplicate_session = await pg_manager.get_async_session()
        try:
            duplicate_session.add(
                _personal_job(
                    job_id=new_scheduled_job_id("sj_"),
                    owner_uid=uid,
                    request_key=f"request-{suffix}",
                    run_at=run_at + timedelta(minutes=1),
                )
            )
            with pytest.raises(IntegrityError):
                await duplicate_session.commit()
        finally:
            await duplicate_session.rollback()
            await duplicate_session.close()

        invalid_schedule_session = await pg_manager.get_async_session()
        try:
            invalid_schedule_session.add(
                _personal_job(
                    job_id=new_scheduled_job_id("sj_"),
                    owner_uid=uid,
                    request_key=f"invalid-{suffix}",
                    run_at=None,
                )
            )
            with pytest.raises(IntegrityError):
                await invalid_schedule_session.commit()
        finally:
            await invalid_schedule_session.rollback()
            await invalid_schedule_session.close()

        first_session = await pg_manager.get_async_session()
        second_session = await pg_manager.get_async_session()
        try:
            await first_session.begin()
            await second_session.begin()
            first_claim = await first_session.scalars(
                select(ScheduledJobRun).where(ScheduledJobRun.id == run_id).with_for_update(skip_locked=True)
            )
            second_claim = await second_session.scalars(
                select(ScheduledJobRun).where(ScheduledJobRun.id == run_id).with_for_update(skip_locked=True)
            )

            assert first_claim.one().id == run_id
            assert second_claim.all() == []
        finally:
            await first_session.rollback()
            await second_session.rollback()
            await first_session.close()
            await second_session.close()
    finally:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(ScheduledJobRun).where(ScheduledJobRun.scheduled_job_id == job_id))
            await session.execute(delete(ScheduledJob).where(ScheduledJob.id == job_id))
            await session.execute(delete(User).where(User.uid == uid))
        # pytest 默认每个异步测试使用独立事件循环，测试间不得复用 asyncpg 连接池。
        await pg_manager.close()
