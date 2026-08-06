"""独立 Scheduler 进程入口。"""

from __future__ import annotations

import asyncio
import signal
import time

from yuxi.scheduled_jobs.runtime import load_runtime_config
from yuxi.services.scheduled_job_scheduler_service import ScheduledJobSchedulerService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger


def _install_stop_signals(stop_event: asyncio.Event) -> None:
    """停止信号到达后不再认领新任务，让已提交的运行交由 Dispatcher 恢复。"""
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            signal.signal(signal_name, lambda *_: stop_event.set())


async def run() -> None:
    config = load_runtime_config()
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.ensure_business_schema()
    service = ScheduledJobSchedulerService(pg_manager.AsyncSession)
    stop_event = asyncio.Event()
    _install_stop_signals(stop_event)
    last_heartbeat = 0.0

    while not stop_event.is_set():
        try:
            if time.monotonic() - last_heartbeat >= 10:
                await service.heartbeat(instance_id=config.instance_id)
                last_heartbeat = time.monotonic()
            await service.schedule_once(batch_size=config.schedule_batch_size)
        except Exception:
            logger.exception("定时任务 Scheduler 本轮执行失败")
            try:
                await service.heartbeat(instance_id=config.instance_id, error_code="scheduler_loop_error")
            except Exception:
                logger.exception("定时任务 Scheduler 错误心跳写入失败")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=config.schedule_poll_seconds)
        except TimeoutError:
            pass

    await pg_manager.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
