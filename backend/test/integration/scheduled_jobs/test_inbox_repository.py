from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from yuxi.repositories.inbox_repository import InboxRepository
from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.services.inbox_service import InboxService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import InboxItem, ScheduledJob


def _job(*, job_id: str, owner_uid: str, status: str, updated_at: datetime) -> ScheduledJob:
    """构造最小个人任务，覆盖任务收件箱的所有生命周期状态。"""
    run_at = updated_at + timedelta(days=1)
    return ScheduledJob(
        id=job_id,
        owner_uid=owner_uid,
        source_type="personal",
        source_snapshot={"entry_point": "test", "thread_id": None},
        name=f"任务-{job_id[-6:]}",
        schedule_kind="at",
        run_at=run_at,
        timezone="Asia/Shanghai",
        next_run_at=run_at if status == "active" else None,
        action_type="notification",
        action_data={"type": "notification", "title": "通知", "content": "测试内容"},
        status=status,
        created_by_uid=owner_uid,
        updated_at=updated_at,
    )


def _inbox_item(
    *,
    item_id: str,
    recipient_uid: str,
    job_id: str,
    category: str,
    created_at: datetime,
    is_read: bool = False,
) -> InboxItem:
    return InboxItem(
        id=item_id,
        recipient_uid=recipient_uid,
        scheduled_job_id=job_id,
        category=category,
        item_type="notification_delivered" if category == "notification" else "run_failed",
        event_key=f"{category}:{item_id}",
        title=f"{category}-{item_id}",
        content_snapshot="测试快照",
        is_read=is_read,
        read_at=created_at if is_read else None,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.integration
async def test_inbox_paginates_aggregates_and_marks_read_with_owner_scope():
    """真实 PostgreSQL 验证排序游标、任务聚合与已读不会跨越用户边界。"""
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    alice_uid = f"inbox_alice_{suffix}"
    bob_uid = f"inbox_bob_{suffix}"
    job_ids = [new_scheduled_job_id("sj_") for _ in range(5)]
    item_ids = [f"ibi_{suffix}_{index}" for index in range(7)]
    base = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)

    try:
        async with pg_manager.get_async_session_context() as session:
            session.add_all(
                [
                    User(uid=alice_uid, username=f"inbox_alice_{suffix[:12]}", password_hash="not-used", role="user"),
                    User(uid=bob_uid, username=f"inbox_bob_{suffix[:12]}", password_hash="not-used", role="user"),
                    _job(
                        job_id=job_ids[0], owner_uid=alice_uid, status="active", updated_at=base + timedelta(minutes=1)
                    ),
                    _job(
                        job_id=job_ids[1], owner_uid=alice_uid, status="paused", updated_at=base + timedelta(minutes=2)
                    ),
                    _job(
                        job_id=job_ids[2],
                        owner_uid=alice_uid,
                        status="completed",
                        updated_at=base + timedelta(minutes=3),
                    ),
                    _job(
                        job_id=job_ids[3],
                        owner_uid=alice_uid,
                        status="cancelled",
                        updated_at=base + timedelta(minutes=4),
                    ),
                    _job(job_id=job_ids[4], owner_uid=bob_uid, status="active", updated_at=base + timedelta(minutes=5)),
                ]
            )
            session.add_all(
                [
                    _inbox_item(
                        item_id=item_ids[0],
                        recipient_uid=alice_uid,
                        job_id=job_ids[0],
                        category="notification",
                        created_at=base,
                    ),
                    _inbox_item(
                        item_id=item_ids[1],
                        recipient_uid=alice_uid,
                        job_id=job_ids[0],
                        category="notification",
                        created_at=base + timedelta(minutes=1),
                    ),
                    _inbox_item(
                        item_id=item_ids[2],
                        recipient_uid=alice_uid,
                        job_id=job_ids[1],
                        category="notification",
                        created_at=base + timedelta(minutes=2),
                        is_read=True,
                    ),
                    _inbox_item(
                        item_id=item_ids[3],
                        recipient_uid=alice_uid,
                        job_id=job_ids[0],
                        category="task",
                        created_at=base + timedelta(minutes=10),
                    ),
                    _inbox_item(
                        item_id=item_ids[4],
                        recipient_uid=alice_uid,
                        job_id=job_ids[0],
                        category="task",
                        created_at=base + timedelta(minutes=11),
                    ),
                    _inbox_item(
                        item_id=item_ids[5],
                        recipient_uid=alice_uid,
                        job_id=job_ids[2],
                        category="task",
                        created_at=base + timedelta(minutes=9),
                        is_read=True,
                    ),
                    _inbox_item(
                        item_id=item_ids[6],
                        recipient_uid=bob_uid,
                        job_id=job_ids[4],
                        category="notification",
                        created_at=base + timedelta(minutes=12),
                    ),
                ]
            )

        async with pg_manager.get_async_session_context() as session:
            service = InboxService(session)
            first_page = await service.list_notifications(recipient_uid=alice_uid, cursor=None, limit=1)
            second_page = await service.list_notifications(
                recipient_uid=alice_uid, cursor=first_page["next_cursor"], limit=2
            )
            task_page = await service.list_tasks(owner_uid=alice_uid, cursor=None, limit=10)
            counts = await service.unread_counts(recipient_uid=alice_uid)

            assert [item["id"] for item in first_page["items"]] == [item_ids[1]]
            assert [item["id"] for item in second_page["items"]] == [item_ids[0], item_ids[2]]
            assert [item["job"]["id"] for item in task_page["items"]] == [
                job_ids[0],
                job_ids[2],
                job_ids[3],
                job_ids[1],
            ]
            assert task_page["items"][0]["unread_update_count"] == 2
            assert task_page["items"][0]["latest_update"]["title"] == f"task-{item_ids[4]}"
            assert counts == {
                "notification_unread_count": 2,
                "task_unread_count": 1,
                "total_unread_count": 3,
            }

        async with pg_manager.get_async_session_context() as session:
            repository = InboxRepository(session)
            assert await repository.mark_notification_read(item_id=item_ids[0], recipient_uid=alice_uid) == 1
            assert await repository.mark_notification_read(item_id=item_ids[0], recipient_uid=alice_uid) == 0
            assert await repository.mark_task_read(job_id=job_ids[0], owner_uid=alice_uid) == 2
            assert await repository.mark_all_read(recipient_uid=alice_uid, category="notification") == 1
            assert await InboxService(session).unread_counts(recipient_uid=alice_uid) == {
                "notification_unread_count": 0,
                "task_unread_count": 0,
                "total_unread_count": 0,
            }
    finally:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(InboxItem).where(InboxItem.id.in_(item_ids)))
            await session.execute(delete(ScheduledJob).where(ScheduledJob.id.in_(job_ids)))
            await session.execute(delete(User).where(User.uid.in_([alice_uid, bob_uid])))
