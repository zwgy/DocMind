"""统一收件箱的 PostgreSQL 查询与事件写入边界。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.storage.postgres.models_scheduled_jobs import InboxItem, ScheduledJob, ScheduledJobRun


class InboxRepository:
    """收件箱事件和已读状态独立于任务/运行事实，所有写入由调用方放在业务事务中提交。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def insert_event_if_absent(
        self,
        *,
        recipient_uid: str,
        scheduled_job_id: str,
        scheduled_job_run_id: str | None,
        category: str,
        item_type: str,
        event_key: str,
        title: str,
        content_snapshot: str,
    ) -> None:
        """事件键是接收人维度幂等键，租约接管或重试不会制造重复收件条目。"""
        await self.db.execute(
            insert(InboxItem)
            .values(
                id=new_scheduled_job_id("ibi_"),
                recipient_uid=recipient_uid,
                scheduled_job_id=scheduled_job_id,
                scheduled_job_run_id=scheduled_job_run_id,
                category=category,
                item_type=item_type,
                event_key=event_key,
                title=title,
                content_snapshot=content_snapshot,
                is_read=False,
            )
            .on_conflict_do_nothing(index_elements=[InboxItem.recipient_uid, InboxItem.event_key])
        )

    async def list_notifications(
        self,
        *,
        recipient_uid: str,
        cursor: tuple[bool, datetime, str] | None,
        limit: int,
    ) -> list[InboxItem]:
        # Boolean 列不能直接与 Python bool 使用大小比较；显式优先级同时与排序和游标保持一致。
        read_priority = case((InboxItem.is_read.is_(False), 0), else_=1)
        statement: Select = select(InboxItem).where(
            InboxItem.recipient_uid == recipient_uid,
            InboxItem.category == "notification",
        )
        if cursor is not None:
            is_read, created_at, item_id = cursor
            cursor_priority = int(is_read)
            statement = statement.where(
                or_(
                    read_priority > cursor_priority,
                    and_(read_priority == cursor_priority, InboxItem.created_at < created_at),
                    and_(
                        read_priority == cursor_priority,
                        InboxItem.created_at == created_at,
                        InboxItem.id < item_id,
                    ),
                )
            )
        result = await self.db.execute(
            statement.order_by(read_priority.asc(), InboxItem.created_at.desc(), InboxItem.id.desc()).limit(limit + 1)
        )
        return list(result.scalars())

    async def list_tasks(
        self,
        *,
        owner_uid: str,
        cursor: tuple[int, datetime, str] | None,
        limit: int,
    ) -> list[tuple]:
        """任务页只展示由 Agent 执行的定时任务及其运行状态。"""
        task_events = (
            select(
                InboxItem.scheduled_job_id.label("scheduled_job_id"),
                InboxItem.title.label("title"),
                InboxItem.content_snapshot.label("content_snapshot"),
                InboxItem.created_at.label("created_at"),
                InboxItem.id.label("id"),
                func.row_number()
                .over(
                    partition_by=InboxItem.scheduled_job_id,
                    order_by=(InboxItem.created_at.desc(), InboxItem.id.desc()),
                )
                .label("row_number"),
            )
            .where(InboxItem.recipient_uid == owner_uid, InboxItem.category == "task")
            .cte("task_events")
        )
        latest_event = (
            select(
                task_events.c.scheduled_job_id,
                task_events.c.title,
                task_events.c.content_snapshot,
                task_events.c.created_at,
            )
            .where(task_events.c.row_number == 1)
            .cte("latest_task_event")
        )
        unread_events = (
            select(
                InboxItem.scheduled_job_id.label("scheduled_job_id"),
                func.count(InboxItem.id).label("unread_update_count"),
            )
            .where(
                InboxItem.recipient_uid == owner_uid,
                InboxItem.category == "task",
                InboxItem.is_read.is_(False),
            )
            .group_by(InboxItem.scheduled_job_id)
            .cte("unread_task_events")
        )
        unread_count = func.coalesce(unread_events.c.unread_update_count, 0)
        has_unread = case((unread_count > 0, 0), else_=1).label("has_unread")
        sort_at = func.coalesce(latest_event.c.created_at, ScheduledJob.updated_at).label("sort_at")
        statement = (
            select(
                ScheduledJob,
                latest_event.c.title,
                latest_event.c.content_snapshot,
                latest_event.c.created_at.label("latest_update_at"),
                unread_count.label("unread_update_count"),
                has_unread,
                sort_at,
            )
            .outerjoin(latest_event, latest_event.c.scheduled_job_id == ScheduledJob.id)
            .outerjoin(unread_events, unread_events.c.scheduled_job_id == ScheduledJob.id)
            .where(
                ScheduledJob.owner_uid == owner_uid,
                ScheduledJob.action_type == "agent",
            )
        )
        if cursor is not None:
            cursor_has_unread, cursor_sort_at, cursor_job_id = cursor
            statement = statement.where(
                or_(
                    has_unread > cursor_has_unread,
                    and_(has_unread == cursor_has_unread, sort_at < cursor_sort_at),
                    and_(has_unread == cursor_has_unread, sort_at == cursor_sort_at, ScheduledJob.id < cursor_job_id),
                )
            )
        result = await self.db.execute(
            statement.order_by(has_unread.asc(), sort_at.desc(), ScheduledJob.id.desc()).limit(limit + 1)
        )
        return list(result.all())

    async def unread_counts(self, *, recipient_uid: str) -> tuple[int, int]:
        notification_count = await self.db.scalar(
            select(func.count(InboxItem.id)).where(
                InboxItem.recipient_uid == recipient_uid,
                InboxItem.category == "notification",
                InboxItem.is_read.is_(False),
            )
        )
        task_count = await self.db.scalar(
            select(func.count(func.distinct(InboxItem.scheduled_job_id)))
            .join(ScheduledJob, ScheduledJob.id == InboxItem.scheduled_job_id)
            .where(
                InboxItem.recipient_uid == recipient_uid,
                InboxItem.category == "task",
                InboxItem.is_read.is_(False),
                ScheduledJob.owner_uid == recipient_uid,
                ScheduledJob.action_type == "agent",
            )
        )
        return int(notification_count or 0), int(task_count or 0)

    async def task_run_summaries(
        self, *, job_ids: list[str], owner_uid: str
    ) -> tuple[dict[str, ScheduledJobRun], dict[str, ScheduledJobRun], dict[str, int]]:
        if not job_ids:
            return {}, {}, {}

        ranked_runs = (
            select(
                ScheduledJobRun.id.label("run_id"),
                ScheduledJobRun.scheduled_job_id.label("job_id"),
                func.row_number()
                .over(
                    partition_by=ScheduledJobRun.scheduled_job_id,
                    order_by=(ScheduledJobRun.created_at.desc(), ScheduledJobRun.id.desc()),
                )
                .label("row_number"),
            )
            .where(ScheduledJobRun.scheduled_job_id.in_(job_ids))
            .cte("ranked_task_runs")
        )
        latest_rows = await self.db.execute(
            select(ScheduledJobRun)
            .join(ranked_runs, ranked_runs.c.run_id == ScheduledJobRun.id)
            .where(ranked_runs.c.row_number == 1)
        )
        latest_by_job = {run.scheduled_job_id: run for run in latest_rows.scalars().all()}

        unread_run_ids = (
            select(
                InboxItem.scheduled_job_id.label("job_id"),
                InboxItem.scheduled_job_run_id.label("run_id"),
            )
            .where(
                InboxItem.recipient_uid == owner_uid,
                InboxItem.category == "task",
                InboxItem.is_read.is_(False),
                InboxItem.scheduled_job_id.in_(job_ids),
                InboxItem.scheduled_job_run_id.is_not(None),
            )
            .distinct()
            .cte("unread_task_run_ids")
        )
        ranked_unread_runs = (
            select(
                unread_run_ids.c.job_id,
                unread_run_ids.c.run_id,
                func.row_number()
                .over(
                    partition_by=unread_run_ids.c.job_id,
                    order_by=(ScheduledJobRun.created_at.desc(), ScheduledJobRun.id.desc()),
                )
                .label("row_number"),
            )
            .join(ScheduledJobRun, ScheduledJobRun.id == unread_run_ids.c.run_id)
            .cte("ranked_unread_task_runs")
        )
        unread_rows = await self.db.execute(
            select(ScheduledJobRun)
            .join(ranked_unread_runs, ranked_unread_runs.c.run_id == ScheduledJobRun.id)
            .where(ranked_unread_runs.c.row_number == 1)
        )
        latest_unread_by_job = {run.scheduled_job_id: run for run in unread_rows.scalars().all()}
        count_rows = await self.db.execute(
            select(unread_run_ids.c.job_id, func.count().label("unread_run_count")).group_by(unread_run_ids.c.job_id)
        )
        unread_counts = {job_id: int(count) for job_id, count in count_rows.all()}
        return latest_by_job, latest_unread_by_job, unread_counts

    async def notification_exists(self, *, item_id: str, recipient_uid: str) -> bool:
        return bool(
            await self.db.scalar(
                select(InboxItem.id).where(
                    InboxItem.id == item_id,
                    InboxItem.recipient_uid == recipient_uid,
                    InboxItem.category == "notification",
                )
            )
        )

    async def task_exists_for_owner(self, *, job_id: str, owner_uid: str) -> bool:
        return bool(
            await self.db.scalar(
                select(ScheduledJob.id).where(
                    ScheduledJob.id == job_id,
                    ScheduledJob.owner_uid == owner_uid,
                    ScheduledJob.action_type == "agent",
                )
            )
        )

    async def task_run_exists_for_owner(self, *, job_id: str, run_id: str, owner_uid: str) -> bool:
        return bool(
            await self.db.scalar(
                select(ScheduledJobRun.id)
                .join(ScheduledJob, ScheduledJob.id == ScheduledJobRun.scheduled_job_id)
                .where(
                    ScheduledJobRun.id == run_id,
                    ScheduledJobRun.scheduled_job_id == job_id,
                    ScheduledJob.owner_uid == owner_uid,
                    ScheduledJob.action_type == "agent",
                )
            )
        )

    async def mark_notification_read(self, *, item_id: str, recipient_uid: str) -> int:
        result = await self.db.execute(
            update(InboxItem)
            .where(
                InboxItem.id == item_id,
                InboxItem.recipient_uid == recipient_uid,
                InboxItem.category == "notification",
                InboxItem.is_read.is_(False),
            )
            .values(is_read=True, read_at=func.now())
        )
        return int(result.rowcount or 0)

    async def mark_task_read(self, *, job_id: str, owner_uid: str) -> int:
        result = await self.db.execute(
            update(InboxItem)
            .where(
                InboxItem.scheduled_job_id == job_id,
                InboxItem.recipient_uid == owner_uid,
                InboxItem.category == "task",
                InboxItem.is_read.is_(False),
            )
            .values(is_read=True, read_at=func.now())
        )
        return int(result.rowcount or 0)

    async def mark_task_run_read(self, *, job_id: str, run_id: str, owner_uid: str) -> int:
        result = await self.db.execute(
            update(InboxItem)
            .where(
                InboxItem.scheduled_job_id == job_id,
                InboxItem.scheduled_job_run_id == run_id,
                InboxItem.recipient_uid == owner_uid,
                InboxItem.category == "task",
                InboxItem.is_read.is_(False),
            )
            .values(is_read=True, read_at=func.now())
        )
        return int(result.rowcount or 0)

    async def mark_all_read(self, *, recipient_uid: str, category: str) -> int:
        statement = update(InboxItem).where(
            InboxItem.recipient_uid == recipient_uid,
            InboxItem.category == category,
            InboxItem.is_read.is_(False),
        )
        if category == "task":
            statement = statement.where(
                InboxItem.scheduled_job_id.in_(
                    select(ScheduledJob.id).where(
                        ScheduledJob.owner_uid == recipient_uid,
                        ScheduledJob.action_type == "agent",
                    )
                )
            )
        result = await self.db.execute(statement.values(is_read=True, read_at=func.now()))
        return int(result.rowcount or 0)
