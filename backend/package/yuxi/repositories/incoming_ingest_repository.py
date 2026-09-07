"""来文接入任务的 PostgreSQL 持久化边界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_knowledge import IncomingIngestBatch, IncomingIngestBatchItem, IncomingIngestJob
from yuxi.utils.datetime_utils import utc_now_naive

IN_FLIGHT_STATUSES = ("dispatching", "queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DispatchClaim:
    """数据库准入成功后交给 Redis 的最小消息载荷。"""

    job_id: str
    delivery_token: str


@dataclass(frozen=True)
class RegistrationResult:
    """一条登记项的稳定结果，供分块 API 返回和断点重传复用。"""

    status: str
    job_id: str | None
    reused: bool = False
    error_message: str | None = None


def is_historical_window(now: datetime, *, start_time: str = "18:00", end_time: str = "07:30") -> bool:
    """按管理员配置的上海时间窗口判断历史任务是否可启动。"""
    local_time = now.astimezone(SHANGHAI_TIMEZONE).time()
    start = time.fromisoformat(start_time)
    end = time.fromisoformat(end_time)
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


class IncomingIngestRepository:
    """调用方管理事务，准入锁内只推进接入任务的持久化状态。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def database_now(self) -> datetime:
        now = await self.db.scalar(select(func.now()))
        if now is None:
            raise RuntimeError("无法读取 PostgreSQL 当前时间")
        return now.astimezone(UTC)

    async def get_by_source_identity(
        self, *, source_system: str, source_document_id: str
    ) -> IncomingIngestJob | None:
        return await self.db.scalar(
            select(IncomingIngestJob).where(
                IncomingIngestJob.source_system == source_system,
                IncomingIngestJob.source_document_id == source_document_id,
            )
        )

    async def list_batches(self, *, page: int, page_size: int) -> tuple[list[IncomingIngestBatch], int]:
        statement = select(IncomingIngestBatch).order_by(
            IncomingIngestBatch.updated_at.desc(), IncomingIngestBatch.id.desc()
        )
        total = await self.db.scalar(select(func.count()).select_from(statement.subquery()))
        batches = list((await self.db.scalars(statement.offset((page - 1) * page_size).limit(page_size))).all())
        return batches, int(total or 0)

    async def list_jobs(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        priority: str | None = None,
    ) -> tuple[list[IncomingIngestJob], int]:
        statement = select(IncomingIngestJob)
        if status:
            statement = statement.where(IncomingIngestJob.status == status)
        if priority:
            statement = statement.where(IncomingIngestJob.priority == priority)
        total = await self.db.scalar(select(func.count()).select_from(statement.subquery()))
        jobs = list(
            (
                await self.db.scalars(
                    statement.order_by(IncomingIngestJob.updated_at.desc(), IncomingIngestJob.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return jobs, int(total or 0)

    async def create_batch(
        self,
        *,
        source_system: str,
        batch_key: str,
        name: str,
        created_by: str,
    ) -> IncomingIngestBatch:
        batch = await self.db.scalar(
            select(IncomingIngestBatch).where(
                IncomingIngestBatch.source_system == source_system,
                IncomingIngestBatch.batch_key == batch_key,
            )
        )
        if batch is not None:
            return batch
        batch = IncomingIngestBatch(
            batch_id=f"ib_{uuid4().hex}",
            source_system=source_system,
            batch_key=batch_key,
            name=name,
            created_by=created_by,
        )
        self.db.add(batch)
        await self.db.flush()
        return batch

    async def register_items(
        self,
        *,
        batch_id: str,
        source_system: str,
        items: list[dict],
        actor_uid: str,
    ) -> list[RegistrationResult]:
        batch = await self.db.scalar(
            select(IncomingIngestBatch)
            .where(IncomingIngestBatch.batch_id == batch_id)
            .with_for_update()
        )
        if batch is None:
            raise ValueError("接入批次不存在")
        if batch.source_system != source_system:
            raise ValueError("接入批次来源不匹配")
        if batch.is_submitted:
            raise ValueError("接入批次已提交，不能继续登记")

        results: list[RegistrationResult] = []
        for item in items:
            source_document_id = str(item.get("source_document_id") or "").strip()
            if not source_document_id:
                results.append(RegistrationResult(status="invalid", job_id=None, error_message="缺少来源来文 ID"))
                continue
            document_metadata = dict(item.get("document_metadata") or {})
            file_manifest = list(item.get("file_manifest") or [])
            job = await self.db.scalar(
                select(IncomingIngestJob)
                .where(
                    IncomingIngestJob.source_system == source_system,
                    IncomingIngestJob.source_document_id == source_document_id,
                )
                .with_for_update()
            )
            reused = job is not None
            if job is not None and job.status not in TERMINAL_STATUSES and (
                job.document_metadata != document_metadata or job.file_manifest != file_manifest
            ):
                results.append(
                    RegistrationResult(status="conflict", job_id=job.job_id, error_message="待处理来文输入不一致")
                )
                continue
            if job is None:
                new_job = self._new_job(
                    source_system=source_system,
                    source_document_id=source_document_id,
                    document_metadata=document_metadata,
                    file_manifest=file_manifest,
                    source_kind="history",
                    priority="historical",
                    actor_uid=actor_uid,
                )
                if self.db.get_bind().dialect.name == "postgresql":
                    await self.db.execute(
                        self.job_insert_statement(
                            job_id=new_job.job_id,
                            source_system=source_system,
                            source_document_id=source_document_id,
                            document_metadata=document_metadata,
                            file_manifest=file_manifest,
                            source_kind="history",
                            priority="historical",
                            actor_uid=actor_uid,
                        )
                    )
                    job = await self.db.scalar(
                        select(IncomingIngestJob)
                        .where(
                            IncomingIngestJob.source_system == source_system,
                            IncomingIngestJob.source_document_id == source_document_id,
                        )
                        .with_for_update()
                    )
                    if job is None:
                        raise RuntimeError("来文任务登记后未找到来源身份")
                    reused = job.job_id != new_job.job_id
                else:
                    job = new_job
                    self.db.add(job)
                    await self.db.flush()

            member = await self.db.scalar(
                select(IncomingIngestBatchItem).where(
                    IncomingIngestBatchItem.batch_id == batch_id,
                    IncomingIngestBatchItem.source_system == source_system,
                    IncomingIngestBatchItem.source_document_id == source_document_id,
                )
            )
            if member is None:
                self.db.add(
                    IncomingIngestBatchItem(
                        batch_id=batch_id,
                        job_id=job.job_id,
                        source_system=source_system,
                        source_document_id=source_document_id,
                        input_version=job.input_version,
                        registration_status="exists" if reused else "accepted",
                    )
                )
            results.append(
                RegistrationResult(
                    status="exists" if reused else "accepted",
                    job_id=job.job_id,
                    reused=reused,
                )
            )
        batch.updated_by = actor_uid
        await self.db.flush()
        return results

    async def submit_batch(self, batch_id: str) -> IncomingIngestBatch:
        batch = await self._get_batch_for_update(batch_id)
        batch.is_submitted = True
        batch.is_paused = False
        batch.status = "active"
        batch.submitted_at = utc_now_naive()
        await self.db.flush()
        return batch

    async def pause_batch(self, batch_id: str) -> IncomingIngestBatch:
        batch = await self._get_batch_for_update(batch_id)
        batch.is_paused = True
        batch.status = "paused"
        await self.db.flush()
        return batch

    async def resume_batch(self, batch_id: str) -> IncomingIngestBatch:
        batch = await self._get_batch_for_update(batch_id)
        if not batch.is_submitted:
            raise ValueError("未提交批次不能恢复")
        batch.is_paused = False
        batch.status = "active"
        await self.db.flush()
        return batch

    async def register_immediate(
        self,
        *,
        source_system: str,
        source_document_id: str,
        document_metadata: dict,
        file_manifest: list[dict],
        actor_uid: str,
    ) -> RegistrationResult:
        job = await self.db.scalar(
            select(IncomingIngestJob)
            .where(
                IncomingIngestJob.source_system == source_system,
                IncomingIngestJob.source_document_id == source_document_id,
            )
            .with_for_update()
        )
        if job is not None:
            if job.status not in TERMINAL_STATUSES:
                job.priority = "immediate"
                job.updated_by = actor_uid
                await self.db.flush()
            return RegistrationResult(status="exists", job_id=job.job_id, reused=True)

        job = self._new_job(
            source_system=source_system,
            source_document_id=source_document_id,
            document_metadata=document_metadata,
            file_manifest=file_manifest,
            source_kind="immediate",
            priority="immediate",
            actor_uid=actor_uid,
        )
        self.db.add(job)
        await self.db.flush()
        return RegistrationResult(status="accepted", job_id=job.job_id)

    async def expedite(
        self,
        job_id: str,
        *,
        source_system: str,
        source_document_id: str,
        actor_uid: str,
    ) -> bool:
        job = await self.db.scalar(
            select(IncomingIngestJob)
            .where(
                IncomingIngestJob.job_id == job_id,
                IncomingIngestJob.source_system == source_system,
                IncomingIngestJob.source_document_id == source_document_id,
            )
            .with_for_update()
        )
        if job is None or job.status in TERMINAL_STATUSES:
            return False
        job.priority = "immediate"
        job.updated_by = actor_uid
        await self.db.flush()
        return True

    @staticmethod
    def job_insert_statement(
        *,
        job_id: str,
        source_system: str,
        source_document_id: str,
        document_metadata: dict,
        file_manifest: list[dict],
        source_kind: str,
        priority: str,
        actor_uid: str,
    ):
        return postgresql_insert(IncomingIngestJob).values(
            job_id=job_id,
            source_system=source_system,
            source_document_id=source_document_id,
            document_metadata=document_metadata,
            file_manifest=file_manifest,
            source_kind=source_kind,
            priority=priority,
            status="pending",
            stage="registered",
            input_ready=True,
            next_attempt_at=utc_now_naive(),
            created_by=actor_uid,
            updated_by=actor_uid,
        ).on_conflict_do_nothing(index_elements=("source_system", "source_document_id"))

    @staticmethod
    def claim_statement(*, now: datetime):
        active_history_batch = (
            select(IncomingIngestBatchItem.id)
            .join(IncomingIngestBatch, IncomingIngestBatch.batch_id == IncomingIngestBatchItem.batch_id)
            .where(
                IncomingIngestBatchItem.job_id == IncomingIngestJob.job_id,
                IncomingIngestBatch.is_submitted.is_(True),
                IncomingIngestBatch.is_paused.is_(False),
                IncomingIngestBatch.status == "active",
            )
            .exists()
        )
        return (
            select(IncomingIngestJob)
            .where(
                IncomingIngestJob.status == "pending",
                IncomingIngestJob.input_ready.is_(True),
                or_(IncomingIngestJob.next_attempt_at.is_(None), IncomingIngestJob.next_attempt_at <= now),
                or_(IncomingIngestJob.priority == "immediate", active_history_batch),
            )
            .order_by(
                case((IncomingIngestJob.priority == "immediate", 0), else_=1).asc(),
                IncomingIngestJob.created_at.asc(),
                IncomingIngestJob.id.asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    async def claim_next(
        self,
        *,
        instance_id: str,
        now: datetime,
        concurrency: int,
        historical_window_start: str = "18:00",
        historical_window_end: str = "07:30",
        lease_seconds: int = 60,
    ) -> DispatchClaim | None:
        if concurrency < 1:
            raise ValueError("来文 Worker 并发必须大于 0")
        if not await self._acquire_dispatch_lock():
            return None
        retrying_job = await self.db.scalar(
            select(IncomingIngestJob)
            .where(
                IncomingIngestJob.status == "dispatching",
                IncomingIngestJob.lease_owner == instance_id,
                IncomingIngestJob.delivery_token.is_not(None),
                IncomingIngestJob.lease_expires_at.is_not(None),
                IncomingIngestJob.lease_expires_at > now,
            )
            .order_by(IncomingIngestJob.created_at.asc(), IncomingIngestJob.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if retrying_job is not None:
            if retrying_job.priority == "immediate" or is_historical_window(
                now, start_time=historical_window_start, end_time=historical_window_end
            ):
                return DispatchClaim(job_id=retrying_job.job_id, delivery_token=retrying_job.delivery_token)
            retrying_job.status = "pending"
            retrying_job.delivery_token = None
            retrying_job.lease_owner = None
            retrying_job.lease_expires_at = None
            retrying_job.last_heartbeat_at = None
            retrying_job.next_attempt_at = now
            await self.db.flush()
        in_flight = await self.db.scalar(
            select(func.count(IncomingIngestJob.id)).where(IncomingIngestJob.status.in_(IN_FLIGHT_STATUSES))
        )
        if (in_flight or 0) >= concurrency:
            return None

        job = await self.db.scalar(self.claim_statement(now=now))
        if job is None:
            return None
        if job.priority == "historical" and not is_historical_window(
            now, start_time=historical_window_start, end_time=historical_window_end
        ):
            return None

        delivery_token = uuid4().hex
        job.status = "dispatching"
        job.delivery_token = delivery_token
        job.lease_owner = instance_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.last_heartbeat_at = now
        job.next_attempt_at = None
        job.attempt_count += 1
        job.total_attempt_count += 1
        await self.db.flush()
        return DispatchClaim(job_id=job.job_id, delivery_token=delivery_token)

    async def mark_queued(self, *, job_id: str, delivery_token: str) -> bool:
        job = await self._get_job_for_update(job_id)
        if job is None or job.status != "dispatching" or job.delivery_token != delivery_token:
            return False
        job.status = "queued"
        await self.db.flush()
        return True

    async def start_delivery(
        self,
        *,
        job_id: str,
        delivery_token: str,
        worker_id: str,
        now: datetime,
        historical_window_start: str = "18:00",
        historical_window_end: str = "07:30",
        lease_seconds: int = 120,
    ) -> IncomingIngestJob | None:
        job = await self._get_job_for_update(job_id)
        if job is None or job.delivery_token != delivery_token or job.status not in {"dispatching", "queued"}:
            return None
        if job.priority == "historical" and (
            not is_historical_window(now, start_time=historical_window_start, end_time=historical_window_end)
            or not await self._has_active_history_batch(job.job_id)
        ):
            job.status = "pending"
            job.delivery_token = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None
            job.next_attempt_at = now
            await self.db.flush()
            return None
        job.status = "running"
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.last_heartbeat_at = now
        await self.db.flush()
        return job

    async def get_running_delivery(self, *, job_id: str, delivery_token: str) -> IncomingIngestJob | None:
        """读取当前有效投递；外部调用前后都必须以此拒绝过期 Worker。"""
        job = await self.db.scalar(select(IncomingIngestJob).where(IncomingIngestJob.job_id == job_id))
        if job is None or job.status != "running" or job.delivery_token != delivery_token:
            return None
        return job

    async def bind_incoming_document(self, *, job_id: str, delivery_token: str, incoming_id: str) -> bool:
        """将已接收的来文绑定到仍有效的投递，过期令牌不得写入新关联。"""
        job = await self._get_job_for_update(job_id)
        if job is None or job.status != "running" or job.delivery_token != delivery_token:
            return False
        job.incoming_id = incoming_id
        await self.db.flush()
        return True

    async def snapshot_parser_params(
        self, *, job_id: str, delivery_token: str, parser_params: dict
    ) -> dict | None:
        """首次执行时固定解析参数，失败恢复不得随环境变量悄悄改变解析语义。"""
        job = await self._get_job_for_update(job_id)
        if job is None or job.status != "running" or job.delivery_token != delivery_token:
            return None
        if job.parser_params is None:
            job.parser_params = parser_params
            await self.db.flush()
        return job.parser_params

    async def renew_lease(
        self,
        *,
        job_id: str,
        delivery_token: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 120,
    ) -> bool:
        job = await self._get_job_for_update(job_id)
        if (
            job is None
            or job.status != "running"
            or job.delivery_token != delivery_token
            or job.lease_owner != worker_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            return False
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.last_heartbeat_at = now
        await self.db.flush()
        return True

    async def finish_delivery(
        self,
        *,
        job_id: str,
        delivery_token: str,
        worker_id: str,
        status: str,
        error_message: str | None = None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError("来文投递只能完成为终态")
        job = await self._get_job_for_update(job_id)
        if (
            job is None
            or job.status != "running"
            or job.delivery_token != delivery_token
            or job.lease_owner != worker_id
        ):
            return False
        job.status = status
        if status in {"succeeded", "cancelled"}:
            job.stage = status
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_heartbeat_at = None
        job.processing_error = error_message
        await self.db.flush()
        return True

    async def recover_expired(self, *, now: datetime) -> int:
        jobs = list(
            (
                await self.db.scalars(
                    select(IncomingIngestJob)
                    .where(
                        IncomingIngestJob.status.in_(IN_FLIGHT_STATUSES),
                        IncomingIngestJob.lease_expires_at.is_not(None),
                        IncomingIngestJob.lease_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for job in jobs:
            job.status = "pending"
            job.delivery_token = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None
            job.next_attempt_at = now
        await self.db.flush()
        return len(jobs)

    async def _acquire_dispatch_lock(self) -> bool:
        """SQLite 单测不支持 PostgreSQL advisory lock；生产连接必须拿到事务锁。"""
        if self.db.get_bind().dialect.name != "postgresql":
            return True
        locked = await self.db.scalar(select(func.pg_try_advisory_xact_lock(func.hashtext("incoming-ingest-dispatch"))))
        return bool(locked)

    async def _get_batch_for_update(self, batch_id: str) -> IncomingIngestBatch:
        batch = await self.db.scalar(
            select(IncomingIngestBatch).where(IncomingIngestBatch.batch_id == batch_id).with_for_update()
        )
        if batch is None:
            raise ValueError("接入批次不存在")
        return batch

    async def _get_job_for_update(self, job_id: str) -> IncomingIngestJob | None:
        return await self.db.scalar(
            select(IncomingIngestJob).where(IncomingIngestJob.job_id == job_id).with_for_update()
        )

    async def _has_active_history_batch(self, job_id: str) -> bool:
        return bool(
            await self.db.scalar(
                select(IncomingIngestBatchItem.id)
                .join(IncomingIngestBatch, IncomingIngestBatch.batch_id == IncomingIngestBatchItem.batch_id)
                .where(
                    IncomingIngestBatchItem.job_id == job_id,
                    IncomingIngestBatch.is_submitted.is_(True),
                    IncomingIngestBatch.is_paused.is_(False),
                    IncomingIngestBatch.status == "active",
                )
                .limit(1)
            )
        )

    @staticmethod
    def _new_job(
        *,
        source_system: str,
        source_document_id: str,
        document_metadata: dict,
        file_manifest: list[dict],
        source_kind: str,
        priority: str,
        actor_uid: str,
    ) -> IncomingIngestJob:
        return IncomingIngestJob(
            job_id=f"ij_{uuid4().hex}",
            source_system=source_system,
            source_document_id=source_document_id,
            document_metadata=document_metadata,
            file_manifest=file_manifest,
            source_kind=source_kind,
            priority=priority,
            status="pending",
            stage="registered",
            input_ready=True,
            next_attempt_at=utc_now_naive(),
            created_by=actor_uid,
            updated_by=actor_uid,
        )
