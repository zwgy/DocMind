"""定时任务的 PostgreSQL 持久化边界。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import floor
from typing import Literal
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.scheduled_jobs.schemas import AtSchedule, CronSchedule, IntervalSchedule, Schedule
from yuxi.scheduled_jobs.timing import next_run_at
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import (
    InboxItem,
    ScheduledJob,
    ScheduledJobRecipient,
    ScheduledJobRun,
    ScheduledServiceHeartbeat,
)

TERMINAL_RUN_STATUSES = frozenset({"succeeded", "partial", "failed", "skipped"})


class ScheduledJobRepository:
    """调用方管理事务，保证锁定、快照和状态推进处于同一提交边界。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def database_now(self) -> datetime:
        """统一从 PostgreSQL 读取时间，避免多个容器的本地时钟影响调度顺序。"""
        now = await self.db.scalar(select(func.now()))
        if now is None:
            raise RuntimeError("无法读取 PostgreSQL 当前时间")
        return now.astimezone(UTC)

    async def create_due_runs(self, *, batch_size: int) -> list[ScheduledJobRun]:
        """锁定到期任务并创建运行快照；周期任务只合并为最新错过时点。"""
        now = await self.database_now()
        result = await self.db.execute(
            select(ScheduledJob)
            .where(
                ScheduledJob.status == "active",
                ScheduledJob.next_run_at.is_not(None),
                ScheduledJob.next_run_at <= now,
            )
            .order_by(ScheduledJob.next_run_at.asc(), ScheduledJob.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        jobs = list(result.scalars())
        runs: list[ScheduledJobRun] = []
        for job in jobs:
            scheduled_for, next_at = self._advance_schedule(job, now)
            recipients = await self._recipient_snapshot(job.id)
            run = ScheduledJobRun(
                id=new_scheduled_job_id("sjr_"),
                scheduled_job_id=job.id,
                scheduled_for=scheduled_for,
                status="pending",
                attempt_count=0,
                next_attempt_at=now,
                action_type=job.action_type,
                action_snapshot=dict(job.action_data),
                recipient_snapshot=recipients,
            )
            self.db.add(run)
            job.next_run_at = next_at
            if job.schedule_kind != "at":
                # 这两个字段是运行内部推进，不能制造用户编辑版本冲突。
                job.last_run_at = scheduled_for
            runs.append(run)
        await self.db.flush()
        return runs

    async def claim_runs(
        self, *, instance_id: str, limit: int, lease_seconds: int
    ) -> list[ScheduledJobRun]:
        """认领可执行或租约超时的运行；调用方必须按空闲并发数传入 limit。"""
        now = await self.database_now()
        due_order = case(
            (ScheduledJobRun.status == "pending", ScheduledJobRun.next_attempt_at),
            else_=ScheduledJobRun.lease_expires_at,
        )
        result = await self.db.execute(
            select(ScheduledJobRun)
            .where(
                or_(
                    (ScheduledJobRun.status == "pending") & (ScheduledJobRun.next_attempt_at <= now),
                    (ScheduledJobRun.status == "dispatching") & (ScheduledJobRun.lease_expires_at <= now),
                )
            )
            .order_by(due_order.asc(), ScheduledJobRun.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        runs = list(result.scalars())
        for run in runs:
            run.status = "dispatching"
            run.attempt_count += 1
            run.lease_owner = instance_id
            run.lease_expires_at = now + timedelta(seconds=lease_seconds)
            run.started_at = run.started_at or now
        await self.db.flush()
        return runs

    async def deliver_notification(
        self, *, run_id: str, instance_id: str
    ) -> Literal["succeeded", "partial", "skipped"] | None:
        """在持有有效租约时写入收件箱并结束运行，旧实例被接管后不得覆盖结果。"""
        now = await self.database_now()
        run = await self._lock_owned_run(run_id=run_id, instance_id=instance_id, now=now)
        if run is None:
            return None
        if run.action_type != "notification":
            raise ValueError(f"第一版不支持动作类型: {run.action_type}")

        action = run.action_snapshot
        recipients = list(run.recipient_snapshot)
        valid_uids = set(
            (
                await self.db.scalars(
                    select(User.uid).where(
                        User.uid.in_([item["uid"] for item in recipients]),
                        User.is_deleted == 0,
                    )
                )
            ).all()
        )
        delivered = 0
        for recipient in recipients:
            recipient_uid = recipient["uid"]
            if recipient_uid not in valid_uids:
                continue
            await self.db.execute(
                insert(InboxItem)
                .values(
                    id=new_scheduled_job_id("ibi_"),
                    recipient_uid=recipient_uid,
                    scheduled_job_id=run.scheduled_job_id,
                    scheduled_job_run_id=run.id,
                    category="notification",
                    item_type="notification_delivered",
                    event_key=f"notification:{run.id}",
                    title=action["title"],
                    content_snapshot=action["content"],
                    is_read=False,
                )
                .on_conflict_do_nothing(index_elements=[InboxItem.recipient_uid, InboxItem.event_key])
            )
            delivered += 1

        status: Literal["succeeded", "partial", "skipped"]
        if delivered == 0:
            status = "skipped"
        elif delivered == len(recipients):
            status = "succeeded"
        else:
            status = "partial"
        await self._finish_run(
            run,
            status=status,
            now=now,
            result_data={"delivered_count": delivered, "recipient_count": len(recipients)},
        )
        return status

    async def reschedule_or_fail(
        self, *, run_id: str, instance_id: str, max_attempts: int, error_code: str, error_message: str
    ) -> Literal["pending", "failed"] | None:
        """临时异常仅在租约仍归本实例时退避重试，避免旧实例写回覆盖新认领。"""
        now = await self.database_now()
        run = await self._lock_owned_run(run_id=run_id, instance_id=instance_id, now=now)
        if run is None:
            return None
        if run.attempt_count >= max_attempts:
            await self._finish_run(run, status="failed", now=now, error_code=error_code, error_message=error_message)
            return "failed"
        delays = (5, 30, 120, 300)
        run.status = "pending"
        run.lease_owner = None
        run.lease_expires_at = None
        run.next_attempt_at = now + timedelta(seconds=delays[run.attempt_count - 1])
        run.error_code = error_code
        run.error_message = error_message
        await self.db.flush()
        return "pending"

    async def fail_run(
        self, *, run_id: str, instance_id: str, error_code: str, error_message: str
    ) -> bool:
        now = await self.database_now()
        run = await self._lock_owned_run(run_id=run_id, instance_id=instance_id, now=now)
        if run is None:
            return False
        await self._finish_run(run, status="failed", now=now, error_code=error_code, error_message=error_message)
        return True

    async def write_heartbeat(
        self,
        *,
        service_type: Literal["scheduler", "dispatcher"],
        instance_id: str,
        error_code: str | None = None,
    ) -> None:
        now = await self.database_now()
        statement = insert(ScheduledServiceHeartbeat).values(
            service_type=service_type,
            instance_id=instance_id,
            last_seen_at=now,
            last_error_code=error_code,
        )
        await self.db.execute(
            statement.on_conflict_do_update(
                index_elements=[ScheduledServiceHeartbeat.service_type, ScheduledServiceHeartbeat.instance_id],
                set_={"last_seen_at": now, "last_error_code": error_code},
            )
        )

    async def _recipient_snapshot(self, job_id: str) -> list[dict[str, str]]:
        rows = await self.db.execute(
            select(ScheduledJobRecipient.recipient_uid, ScheduledJobRecipient.recipient_name_snapshot)
            .where(ScheduledJobRecipient.scheduled_job_id == job_id)
            .order_by(ScheduledJobRecipient.recipient_uid.asc())
        )
        return [{"uid": uid, "name": name} for uid, name in rows]

    async def _lock_owned_run(self, *, run_id: str, instance_id: str, now: datetime) -> ScheduledJobRun | None:
        run = await self.db.scalar(select(ScheduledJobRun).where(ScheduledJobRun.id == run_id).with_for_update())
        if (
            run is None
            or run.status != "dispatching"
            or run.lease_owner != instance_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
        ):
            return None
        return run

    async def _finish_run(
        self,
        run: ScheduledJobRun,
        *,
        status: Literal["succeeded", "partial", "failed", "skipped"],
        now: datetime,
        result_data: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        run.status = status
        run.lease_owner = None
        run.lease_expires_at = None
        run.next_attempt_at = None
        run.finished_at = now
        run.result_data = result_data
        run.error_code = error_code
        run.error_message = error_message
        job = await self.db.scalar(
            select(ScheduledJob).where(ScheduledJob.id == run.scheduled_job_id).with_for_update()
        )
        if job is not None and job.schedule_kind == "at":
            job.status = "completed"
            job.updated_at = now
        await self.db.flush()

    @staticmethod
    def _schedule_from_job(job: ScheduledJob) -> Schedule:
        if job.schedule_kind == "at":
            return AtSchedule(run_at=job.run_at)
        if job.schedule_kind == "interval":
            return IntervalSchedule(interval_seconds=job.interval_seconds, anchor_at=job.anchor_at)
        if job.schedule_kind == "cron":
            return CronSchedule(cron_expression=job.cron_expression)
        raise ValueError(f"不支持的调度类型: {job.schedule_kind}")

    def _advance_schedule(self, job: ScheduledJob, now: datetime) -> tuple[datetime, datetime | None]:
        if job.schedule_kind == "at":
            if job.run_at is None:
                raise ValueError(f"一次性任务 {job.id} 缺少 run_at")
            return job.run_at.astimezone(UTC), None

        schedule = self._schedule_from_job(job)
        scheduled_for = self._latest_due_at(schedule=schedule, timezone=job.timezone, now=now)
        return scheduled_for, next_run_at(schedule, job.timezone, now, inclusive=False)

    @staticmethod
    def _latest_due_at(*, schedule: Schedule, timezone: str, now: datetime) -> datetime:
        if isinstance(schedule, IntervalSchedule):
            anchor_at = schedule.anchor_at.astimezone(UTC)
            elapsed = max((now - anchor_at).total_seconds(), 0)
            return anchor_at + timedelta(seconds=floor(elapsed / schedule.interval_seconds) * schedule.interval_seconds)
        if isinstance(schedule, CronSchedule):
            local_now = now.astimezone(ZoneInfo(timezone))
            return (
                croniter(schedule.cron_expression, local_now + timedelta(minutes=1)).get_prev(datetime).astimezone(UTC)
            )
        if isinstance(schedule, AtSchedule):
            return schedule.run_at.astimezone(UTC)
        raise TypeError(f"不支持的调度类型: {type(schedule)!r}")
