"""专用来文 ARQ Worker，避免加载 Agent Worker 的恢复和运行时依赖。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.config.app import config as sys_config
from yuxi.repositories.incoming_ingest_repository import IncomingIngestRepository
from yuxi.services.incoming_ingest_dispatcher_service import INCOMING_QUEUE_NAME, INCOMING_WORKER_SAFE_CAPACITY
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.redis import get_arq_redis_settings
from yuxi.utils.datetime_utils import utc_now


class IncomingIngestWorkerService:
    """先验证数据库令牌，避免 Redis 的旧消息触发任何外部解析调用。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        execute_job: Callable[[str, str], Awaitable[None]],
        repository_factory=IncomingIngestRepository,
        worker_id: str | None = None,
        lease_renew_interval_seconds: float = 30,
    ):
        self._session_factory = session_factory
        self._execute_job = execute_job
        self._repository_factory = repository_factory
        self._worker_id = worker_id or f"incoming-worker:{os.getenv('HOSTNAME', 'local')}"
        self._lease_renew_interval_seconds = lease_renew_interval_seconds

    async def process(self, *, job_id: str, delivery_token: str) -> bool:
        now = utc_now()
        async with self._session_factory() as session:
            async with session.begin():
                job = await self._repository_factory(session).start_delivery(
                    job_id=job_id,
                    delivery_token=delivery_token,
                    worker_id=self._worker_id,
                    now=now,
                    historical_window_start=sys_config.incoming_history_window_start,
                    historical_window_end=sys_config.incoming_history_window_end,
        )
        if job is None:
            return False
        heartbeat = asyncio.create_task(self._renew_lease_until_finished(job_id, delivery_token))
        try:
            await self._execute_job(job_id, delivery_token)
        except Exception as exc:
            await self._stop_heartbeat(heartbeat)
            async with self._session_factory() as session:
                async with session.begin():
                    repository = self._repository_factory(session)
                    if _is_retryable_download_error(exc) and job.attempt_count <= sys_config.incoming_auto_retry_count:
                        await repository.retry_running_delivery(
                            job_id=job_id,
                            delivery_token=delivery_token,
                            worker_id=self._worker_id,
                            error_message=str(exc),
                        )
                        return False
                    await repository.finish_delivery(
                        job_id=job_id,
                        delivery_token=delivery_token,
                        worker_id=self._worker_id,
                        status="failed",
                        error_message=str(exc),
                    )
            raise
        await self._stop_heartbeat(heartbeat)
        async with self._session_factory() as session:
            async with session.begin():
                await self._repository_factory(session).finish_delivery(
                    job_id=job_id,
                    delivery_token=delivery_token,
                    worker_id=self._worker_id,
                    status="succeeded",
                )
        return True

    async def _renew_lease_until_finished(self, job_id: str, delivery_token: str) -> None:
        """长耗时解析期间定期续租，避免恢复器把存活 Worker 误判为失联。"""
        while True:
            await asyncio.sleep(self._lease_renew_interval_seconds)
            async with self._session_factory() as session:
                async with session.begin():
                    renewed = await self._repository_factory(session).renew_lease(
                        job_id=job_id,
                        delivery_token=delivery_token,
                        worker_id=self._worker_id,
                        now=utc_now(),
                    )
            if not renewed:
                return

    @staticmethod
    async def _stop_heartbeat(heartbeat: asyncio.Task[None]) -> None:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


def _is_retryable_download_error(exc: Exception) -> bool:
    """仅重试短暂网络故障，避免对失效链接和非法文件反复发起无效请求。"""
    message = str(exc)
    if "附件下载超时" in message:
        return True
    if "附件下载失败：HTTP 5" in message:
        return True
    return message.startswith("附件下载失败：") and "HTTP " not in message


async def _execute_ingest_job(job_id: str, delivery_token: str) -> None:
    from yuxi.services.incoming_document_ingest_service import IncomingDocumentIngestService

    await IncomingDocumentIngestService().execute_ingest_job(job_id, delivery_token)


_worker_service: IncomingIngestWorkerService | None = None


async def process_incoming_document_job(ctx, job_id: str, delivery_token: str) -> bool:
    del ctx
    if _worker_service is None:
        raise RuntimeError("来文 Worker 尚未初始化")
    return await _worker_service.process(job_id=job_id, delivery_token=delivery_token)


async def _worker_startup(ctx) -> None:
    del ctx
    global _worker_service
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.ensure_business_schema()
    sys_config.start_runtime_sync()
    _worker_service = IncomingIngestWorkerService(
        session_factory=pg_manager.AsyncSession,
        execute_job=_execute_ingest_job,
    )


async def _worker_shutdown(ctx) -> None:
    del ctx
    await pg_manager.close()


class IncomingWorkerSettings:
    functions = [process_incoming_document_job]
    queue_name = INCOMING_QUEUE_NAME
    # ARQ 预留有限安全容量，实际在途数由 Dispatcher 的运行时配置控制。
    max_jobs = INCOMING_WORKER_SAFE_CAPACITY
    max_tries = 1
    retry_jobs = False
    job_timeout = 21600
    keep_result = 0
    on_startup = _worker_startup
    on_shutdown = _worker_shutdown
    try:
        redis_settings = get_arq_redis_settings()
    except Exception:
        redis_settings = None
