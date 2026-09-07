"""来文接入任务的短消息投递服务。"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.incoming_ingest_repository import DispatchClaim, IncomingIngestRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.redis import create_arq_redis_pool
from yuxi.utils import logger

INCOMING_QUEUE_NAME = "arq:incoming"


async def enqueue_incoming_claim(queue, claim: DispatchClaim) -> None:
    """固定 ARQ 消息 ID，使同一令牌的投递重试保持幂等。"""
    await queue.enqueue_job(
        "process_incoming_document_job",
        claim.job_id,
        claim.delivery_token,
        _queue_name=INCOMING_QUEUE_NAME,
        _job_id=f"incoming:{claim.job_id}:{claim.delivery_token}",
        _expires=timedelta(hours=1),
    )


class IncomingIngestDispatcherService:
    """PostgreSQL 准入后才投递 Redis，Redis 故障不回滚持久化任务。"""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        queue_factory: Callable[[], Awaitable[object]] = create_arq_redis_pool,
    ):
        self._session_factory = session_factory
        self._queue_factory = queue_factory

    async def dispatch_once(self, *, instance_id: str, concurrency: int = 1) -> DispatchClaim | None:
        async with self._session_factory() as session:
            async with session.begin():
                repository = IncomingIngestRepository(session)
                now = await repository.database_now()
                await repository.recover_expired(now=now)
                claim = await repository.claim_next(
                    instance_id=instance_id,
                    now=now,
                    concurrency=concurrency,
                )
        if claim is None:
            return None

        queue = await self._queue_factory()
        try:
            await enqueue_incoming_claim(queue, claim)
        except Exception:
            # 保持 dispatching 和原令牌，下一轮会以同一 ARQ job ID 重投。
            logger.exception("来文接入任务投递失败: job_id=%s", claim.job_id)
            return claim
        finally:
            close = getattr(queue, "aclose", None)
            if close is not None:
                await close()

        async with self._session_factory() as session:
            async with session.begin():
                await IncomingIngestRepository(session).mark_queued(
                    job_id=claim.job_id,
                    delivery_token=claim.delivery_token,
                )
        return claim


def _install_stop_signals(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            signal.signal(signal_name, lambda *_: stop_event.set())


async def run_dispatcher() -> None:
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.ensure_business_schema()
    service = IncomingIngestDispatcherService(pg_manager.AsyncSession)
    stop_event = asyncio.Event()
    _install_stop_signals(stop_event)
    instance_id = f"incoming-dispatcher:{os.getenv('HOSTNAME', 'local')}"
    try:
        while not stop_event.is_set():
            try:
                await service.dispatch_once(instance_id=instance_id, concurrency=1)
            except Exception:
                logger.exception("来文接入 Dispatcher 本轮失败")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
            except TimeoutError:
                pass
    finally:
        await pg_manager.close()
