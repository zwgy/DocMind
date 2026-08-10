from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.inbox_repository import InboxRepository
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_knowledge import IncomingDocument  # noqa: F401
from yuxi.storage.postgres.models_scheduled_jobs import InboxItem, ScheduledJob, ScheduledJobRun

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_task_run_summaries_select_latest_and_latest_unread_run(session):
    now = datetime(2030, 1, 1, tzinfo=UTC)
    job = ScheduledJob(
        id="sj-summary",
        owner_uid="alice",
        source_type="personal",
        source_snapshot={"entry_point": "test", "thread_id": None},
        name="周期汇总",
        schedule_kind="interval",
        anchor_at=now,
        interval_seconds=3600,
        timezone="Asia/Shanghai",
        next_run_at=now + timedelta(hours=2),
        action_type="agent",
        action_data={
            "type": "agent",
            "agent_slug": "report-agent",
            "instruction": "生成汇总",
            "timeout_seconds": 300,
        },
        status="active",
        created_by_uid="alice",
        created_at=now,
        updated_at=now,
    )
    runs = [
        ScheduledJobRun(
            id=f"sjr-{index}",
            scheduled_job_id=job.id,
            scheduled_for=now + timedelta(hours=index),
            status="succeeded",
            attempt_count=1,
            action_type="agent",
            action_snapshot=job.action_data,
            recipient_snapshot=[{"uid": "alice", "name": "Alice"}],
            result_data={"result_preview": f"结果 {index}", "artifact_count": index},
            conversation_thread_id=f"thread-{index}",
            finished_at=now + timedelta(hours=index, minutes=1),
            created_at=now + timedelta(hours=index),
            updated_at=now + timedelta(hours=index, minutes=1),
        )
        for index in (1, 2)
    ]
    session.add(job)
    await session.flush()
    session.add_all(runs)
    await session.flush()
    session.add_all(
        [
            InboxItem(
                id=f"ibi-{index}",
                recipient_uid="alice",
                scheduled_job_id=job.id,
                scheduled_job_run_id=run.id,
                category="task",
                item_type="agent_run_succeeded",
                event_key=f"task:{run.id}:succeeded",
                title="任务完成",
                content_snapshot=f"结果 {index}",
                is_read=False,
                created_at=run.finished_at,
                updated_at=run.finished_at,
            )
            for index, run in enumerate(runs, start=1)
        ]
    )
    await session.commit()

    repository = InboxRepository(session)
    latest, latest_unread, unread_counts = await repository.task_run_summaries(job_ids=[job.id], owner_uid="alice")

    assert latest[job.id].id == "sjr-2"
    assert latest_unread[job.id].id == "sjr-2"
    assert unread_counts == {job.id: 2}
    assert await repository.mark_task_run_read(job_id=job.id, run_id="sjr-2", owner_uid="alice") == 1

    _, latest_unread, unread_counts = await repository.task_run_summaries(job_ids=[job.id], owner_uid="alice")
    assert latest_unread[job.id].id == "sjr-1"
    assert unread_counts == {job.id: 1}
