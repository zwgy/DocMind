"""定时任务的个人创建和生命周期用例。"""

from __future__ import annotations

import hashlib
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.scheduled_jobs.schemas import AgentAction, PersonalScheduledJobRequest
from yuxi.scheduled_jobs.timing import next_run_at
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import (
    ScheduledJob,
    ScheduledJobAuditLog,
    ScheduledJobRecipient,
    ScheduledJobRun,
)


class ScheduledJobDomainError(ValueError):
    """路由可映射为确定 HTTP 状态的预期领域错误。"""


class IdempotencyKeyReusedError(ScheduledJobDomainError):
    pass


class JobVersionConflictError(ScheduledJobDomainError):
    pass


class JobAlreadyTriggeredError(ScheduledJobDomainError):
    pass


class ScheduledJobService:
    """跨任务、接收人和审计表的写入由同一会话提交，禁止半成品任务可见。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.repository = ScheduledJobRepository(db_session)
        self.cancelled_agent_run_ids: list[str] = []

    async def create_personal_job(
        self, *, owner_uid: str, request: PersonalScheduledJobRequest, idempotency_key: str
    ) -> ScheduledJob:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ScheduledJobDomainError("Idempotency-Key 必须为 1 到 128 个字符")
        request_hash = self._request_hash(request)
        existing = await self._job_by_request_key(owner_uid, idempotency_key)
        if existing is not None:
            return self._same_request_or_raise(existing, request_hash)

        owner = await self.db.scalar(select(User).where(User.uid == owner_uid, User.is_deleted == 0))
        if owner is None:
            raise ScheduledJobDomainError("当前用户不存在或已删除")
        action_data = await self._resolve_action_data(request=request, owner=owner)
        now = await self.repository.database_now()
        next_at = next_run_at(request.schedule, request.timezone, now, inclusive=False)
        if request.schedule.kind == "at" and next_at <= now:
            raise ScheduledJobDomainError("一次性任务触发时间必须晚于当前时间")

        job = ScheduledJob(
            id=new_scheduled_job_id("sj_"),
            owner_uid=owner_uid,
            source_type="personal",
            create_request_key=idempotency_key,
            create_request_hash=request_hash,
            source_snapshot={"entry_point": "http_api", "thread_id": None},
            name=request.name,
            schedule_kind=request.schedule.kind,
            run_at=getattr(request.schedule, "run_at", None),
            anchor_at=getattr(request.schedule, "anchor_at", None),
            interval_seconds=getattr(request.schedule, "interval_seconds", None),
            cron_expression=getattr(request.schedule, "cron_expression", None),
            timezone=request.timezone,
            next_run_at=next_at,
            action_type=request.action.type,
            action_data=action_data,
            status="active",
            created_by_uid=owner_uid,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(job)
                await self.db.flush()
        except IntegrityError:
            existing = await self._job_by_request_key(owner_uid, idempotency_key)
            if existing is None:
                raise
            return self._same_request_or_raise(existing, request_hash)

        self.db.add(
            ScheduledJobRecipient(
                scheduled_job_id=job.id,
                recipient_uid=owner_uid,
                recipient_name_snapshot=owner.username,
            )
        )
        self._audit(job=job, actor_uid=owner_uid, action="created", after_data={"status": job.status})
        await self.db.flush()
        return job

    async def list_owned_jobs(
        self,
        *,
        owner_uid: str,
        statuses: tuple[str, ...],
        cursor: str | None,
        limit: int = 20,
    ) -> tuple[list[ScheduledJob], str | None]:
        statement = select(ScheduledJob).where(ScheduledJob.owner_uid == owner_uid)
        statement = statement.where(ScheduledJob.status.in_(statuses))
        if cursor:
            cursor_updated_at, cursor_id = self._decode_cursor(cursor)
            statement = statement.where(
                or_(
                    ScheduledJob.updated_at < cursor_updated_at,
                    and_(ScheduledJob.updated_at == cursor_updated_at, ScheduledJob.id < cursor_id),
                )
            )
        result = await self.db.execute(
            statement.order_by(ScheduledJob.updated_at.desc(), ScheduledJob.id.desc()).limit(limit + 1)
        )
        jobs = list(result.scalars())
        page = jobs[:limit]
        next_cursor = self._encode_cursor(page[-1]) if len(jobs) > limit else None
        return page, next_cursor

    async def get_owned_job(self, *, job_id: str, owner_uid: str) -> ScheduledJob | None:
        return await self.db.scalar(
            select(ScheduledJob).where(ScheduledJob.id == job_id, ScheduledJob.owner_uid == owner_uid)
        )

    async def pause(self, *, job_id: str, owner_uid: str, version: int) -> ScheduledJob:
        job = await self._lock_owned_job(job_id=job_id, owner_uid=owner_uid, version=version)
        if job.schedule_kind == "at" or job.status != "active":
            raise ScheduledJobDomainError("只有活动中的周期任务可以暂停")
        before = {"status": job.status}
        job.status = "paused"
        job.paused_at = await self.repository.database_now()
        job.next_run_at = None
        job.version += 1
        self._audit(
            job=job,
            actor_uid=owner_uid,
            action="paused",
            before_data=before,
            after_data={"status": job.status},
        )
        await self.db.flush()
        return job

    async def resume(self, *, job_id: str, owner_uid: str, version: int) -> ScheduledJob:
        job = await self._lock_owned_job(job_id=job_id, owner_uid=owner_uid, version=version)
        if job.status != "paused":
            raise ScheduledJobDomainError("只有已暂停任务可以恢复")
        now = await self.repository.database_now()
        job.status = "active"
        job.paused_at = None
        job.next_run_at = next_run_at(self.repository._schedule_from_job(job), job.timezone, now, inclusive=False)
        job.version += 1
        self._audit(job=job, actor_uid=owner_uid, action="resumed", after_data={"status": job.status})
        await self.db.flush()
        return job

    async def cancel(
        self, *, job_id: str, owner_uid: str, version: int, reason: str | None = None
    ) -> ScheduledJob:
        job = await self._lock_owned_job(job_id=job_id, owner_uid=owner_uid, version=version)
        existing_run = await self.db.scalar(
            select(ScheduledJobRun)
            .where(ScheduledJobRun.scheduled_job_id == job.id)
            .order_by(ScheduledJobRun.created_at.desc())
            .limit(1)
        )
        if job.schedule_kind == "at" and existing_run and (
            existing_run.action_type != "agent" or existing_run.status not in {"queued", "running"}
        ):
            raise JobAlreadyTriggeredError("一次性任务已经生成运行，不能取消")
        if job.status not in {"active", "paused"}:
            raise ScheduledJobDomainError("当前状态不能取消任务")
        before = {"status": job.status}
        job.status = "cancelled"
        job.cancelled_at = await self.repository.database_now()
        job.cancelled_reason = reason or "owner_cancelled"
        job.next_run_at = None
        job.version += 1
        active_agent_runs = list(
            (
                await self.db.scalars(
                    select(ScheduledJobRun).where(
                        ScheduledJobRun.scheduled_job_id == job.id,
                        ScheduledJobRun.action_type == "agent",
                        ScheduledJobRun.status.in_(("queued", "running")),
                        ScheduledJobRun.agent_run_id.is_not(None),
                    )
                )
            ).all()
        )
        agent_run_repository = AgentRunRepository(self.db)
        for run in active_agent_runs:
            await agent_run_repository.request_cancel(run.agent_run_id)
            await self.repository.sync_agent_run_status(agent_run_id=run.agent_run_id, agent_status="cancelled")
            self.cancelled_agent_run_ids.append(run.agent_run_id)
        self._audit(
            job=job,
            actor_uid=owner_uid,
            action="cancelled",
            before_data=before,
            after_data={"status": job.status},
            reason=job.cancelled_reason,
        )
        await self.db.flush()
        return job

    async def update_personal_job(
        self, *, job_id: str, owner_uid: str, version: int, request: PersonalScheduledJobRequest
    ) -> ScheduledJob:
        """编辑尚未产生运行的个人任务，避免改写已经冻结的运行快照。"""
        job = await self._lock_owned_job(job_id=job_id, owner_uid=owner_uid, version=version)
        if job.status not in {"active", "paused"}:
            raise ScheduledJobDomainError("当前状态不能编辑任务")
        if await self.db.scalar(select(ScheduledJobRun.id).where(ScheduledJobRun.scheduled_job_id == job.id).limit(1)):
            raise ScheduledJobDomainError("任务已经生成运行，不能编辑")
        now = await self.repository.database_now()
        next_at = next_run_at(request.schedule, request.timezone, now, inclusive=False)
        if request.schedule.kind == "at" and next_at <= now:
            raise ScheduledJobDomainError("一次性任务触发时间必须晚于当前时间")
        owner = await self.db.scalar(select(User).where(User.uid == owner_uid, User.is_deleted == 0))
        if owner is None:
            raise ScheduledJobDomainError("当前用户不存在或已删除")
        action_data = await self._resolve_action_data(request=request, owner=owner)
        before = {"name": job.name, "schedule_kind": job.schedule_kind, "action_data": job.action_data}
        job.name = request.name
        job.schedule_kind = request.schedule.kind
        job.run_at = getattr(request.schedule, "run_at", None)
        job.anchor_at = getattr(request.schedule, "anchor_at", None)
        job.interval_seconds = getattr(request.schedule, "interval_seconds", None)
        job.cron_expression = getattr(request.schedule, "cron_expression", None)
        job.timezone = request.timezone
        job.action_data = action_data
        job.next_run_at = next_at if job.status == "active" else None
        job.version += 1
        self._audit(
            job=job,
            actor_uid=owner_uid,
            action="updated",
            before_data=before,
            after_data={"name": job.name, "schedule_kind": job.schedule_kind, "action_data": job.action_data},
        )
        await self.db.flush()
        return job

    async def list_owned_runs(
        self, *, job_id: str, owner_uid: str, cursor: str | None, limit: int
    ) -> tuple[list[ScheduledJobRun], str | None]:
        if await self.get_owned_job(job_id=job_id, owner_uid=owner_uid) is None:
            raise ScheduledJobDomainError("任务不存在")
        statement = select(ScheduledJobRun).where(ScheduledJobRun.scheduled_job_id == job_id)
        if cursor:
            created_at, run_id = self._decode_cursor(cursor)
            statement = statement.where(
                or_(
                    ScheduledJobRun.created_at < created_at,
                    and_(ScheduledJobRun.created_at == created_at, ScheduledJobRun.id < run_id),
                )
            )
        result = await self.db.scalars(
            statement.order_by(ScheduledJobRun.created_at.desc(), ScheduledJobRun.id.desc()).limit(limit + 1)
        )
        rows = list(result.all())
        page = rows[:limit]
        return page, self._encode_cursor(page[-1]) if len(rows) > limit else None

    async def _lock_owned_job(self, *, job_id: str, owner_uid: str, version: int) -> ScheduledJob:
        job = await self.db.scalar(
            select(ScheduledJob)
            .where(ScheduledJob.id == job_id, ScheduledJob.owner_uid == owner_uid)
            .with_for_update()
        )
        if job is None:
            raise ScheduledJobDomainError("任务不存在")
        if job.version != version:
            raise JobVersionConflictError("任务已被其他操作更新")
        return job

    async def _job_by_request_key(self, owner_uid: str, request_key: str) -> ScheduledJob | None:
        return await self.db.scalar(
            select(ScheduledJob).where(
                ScheduledJob.owner_uid == owner_uid,
                ScheduledJob.create_request_key == request_key,
            )
        )

    @staticmethod
    def _request_hash(request: PersonalScheduledJobRequest) -> str:
        payload = request.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(serialized.encode()).hexdigest()

    async def _resolve_action_data(self, *, request: PersonalScheduledJobRequest, owner: User) -> dict:
        """校验 Agent 动作目标；运行时必须重新读取管理员保存的能力配置。"""
        action_data = request.action.model_dump(mode="json")
        if not isinstance(request.action, AgentAction):
            return action_data

        agent = await AgentRepository(self.db).get_visible_by_slug(
            slug=request.action.agent_slug,
            user=owner,
        )
        if agent is None or agent.is_subagent:
            raise ScheduledJobDomainError("目标 Agent 不存在、不可见或不能作为定时任务执行")
        return action_data

    @staticmethod
    def _same_request_or_raise(job: ScheduledJob, request_hash: str) -> ScheduledJob:
        if job.create_request_hash != request_hash:
            raise IdempotencyKeyReusedError("Idempotency-Key 已用于不同请求")
        return job

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, str]:
        try:
            payload = json.loads(urlsafe_b64decode(cursor.encode()).decode())
            updated_at = datetime.fromisoformat(payload["updated_at"])
            job_id = payload["id"]
        except (BinasciiError, KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
            raise ScheduledJobDomainError("cursor 无效") from error
        if updated_at.tzinfo is None or updated_at.utcoffset() is None or not isinstance(job_id, str) or not job_id:
            raise ScheduledJobDomainError("cursor 无效")
        return updated_at.astimezone(UTC), job_id

    @staticmethod
    def _encode_cursor(job: ScheduledJob) -> str:
        payload = json.dumps(
            {"updated_at": job.updated_at.astimezone(UTC).isoformat(), "id": job.id},
            separators=(",", ":"),
        )
        return urlsafe_b64encode(payload.encode()).decode()

    def _audit(
        self,
        *,
        job: ScheduledJob,
        actor_uid: str,
        action: str,
        before_data: dict | None = None,
        after_data: dict | None = None,
        reason: str | None = None,
    ) -> None:
        self.db.add(
            ScheduledJobAuditLog(
                id=new_scheduled_job_id("sja_"),
                scheduled_job_id=job.id,
                actor_uid=actor_uid,
                action=action,
                before_data=before_data,
                after_data=after_data,
                reason=reason,
            )
        )
