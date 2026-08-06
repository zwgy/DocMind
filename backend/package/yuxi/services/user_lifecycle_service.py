"""用户生命周期与定时任务的跨表一致性用例。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.storage.postgres.models_business import APIKey, User
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledJob, ScheduledJobAuditLog
from yuxi.utils.datetime_utils import utc_now_naive


class UserLifecycleService:
    """调用方负责提交，确保用户不可登录与未来任务停止之间不存在中间状态。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def delete_user(self, *, user: User, actor_uid: str) -> int:
        """软删除用户并取消其未来调度；已生成运行保留原快照继续分发。"""
        now = await self.db.scalar(select(func.now()))
        if now is None:
            raise RuntimeError("无法读取 PostgreSQL 当前时间")

        jobs = list(
            (
                await self.db.scalars(
                    select(ScheduledJob)
                    .where(ScheduledJob.owner_uid == user.uid, ScheduledJob.status.in_(("active", "paused")))
                    .with_for_update()
                )
            ).all()
        )
        for job in jobs:
            before = {"status": job.status}
            job.status = "cancelled"
            job.cancelled_at = now
            job.cancelled_reason = "owner_deleted"
            job.next_run_at = None
            job.version += 1
            self.db.add(
                ScheduledJobAuditLog(
                    id=new_scheduled_job_id("sja_"),
                    scheduled_job_id=job.id,
                    actor_uid=actor_uid,
                    action="cancelled",
                    before_data=before,
                    after_data={"status": "cancelled"},
                    reason="owner_deleted",
                )
            )

        user.is_deleted = 1
        user.deleted_at = utc_now_naive()
        user.username = f"已注销用户-{user.id}"
        user.phone_number = None
        user.password_hash = "DELETED"
        user.avatar = None
        api_keys = list(
            (await self.db.scalars(select(APIKey).where(APIKey.user_id == user.id).with_for_update())).all()
        )
        for api_key in api_keys:
            api_key.is_enabled = False
        await self.db.flush()
        return len(jobs)
