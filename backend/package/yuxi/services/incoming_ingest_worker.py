"""专用来文 ARQ Worker，避免加载 Agent Worker 的恢复和运行时依赖。"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.incoming_ingest_repository import IncomingIngestRepository
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
    ):
        self._session_factory = session_factory
        self._execute_job = execute_job
        self._repository_factory = repository_factory
        self._worker_id = worker_id or f"incoming-worker:{os.getenv('HOSTNAME', 'local')}"

    async def process(self, *, job_id: str, delivery_token: str) -> bool:
        now = utc_now()
        async with self._session_factory() as session:
            async with session.begin():
                job = await self._repository_factory(session).start_delivery(
                    job_id=job_id,
                    delivery_token=delivery_token,
                    worker_id=self._worker_id,
                    now=now,
                )
        if job is None:
            return False
        await self._execute_job(job_id, delivery_token)
        return True


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
    _worker_service = IncomingIngestWorkerService(
        session_factory=pg_manager.AsyncSession,
        execute_job=_execute_ingest_job,
    )


async def _worker_shutdown(ctx) -> None:
    del ctx
    await pg_manager.close()


class IncomingWorkerSettings:
    functions = [process_incoming_document_job]
    max_jobs = 1
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
