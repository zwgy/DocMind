"""独立 Dispatcher 进程入口。"""

from __future__ import annotations

import asyncio
import signal
import time

from sqlalchemy.exc import DBAPIError, OperationalError

from yuxi.scheduled_jobs.runtime import load_runtime_config
from yuxi.services.scheduled_job_dispatcher_service import ScheduledJobDispatcherService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger


def _install_stop_signals(stop_event: asyncio.Event) -> None:
    """优雅停止只阻止新的租约认领，已认领动作仍在 Compose 宽限期内完成或回滚。"""
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            signal.signal(signal_name, lambda *_: stop_event.set())


async def _dispatch_one(
    service: ScheduledJobDispatcherService,
    *,
    run_id: str,
    instance_id: str,
    action_timeout_seconds: int,
    max_attempts: int,
) -> None:
    try:
        await asyncio.wait_for(
            service.dispatch_action(run_id=run_id, instance_id=instance_id),
            timeout=action_timeout_seconds,
        )
    except (TimeoutError, OperationalError, DBAPIError) as error:
        await service.retry_or_fail(
            run_id=run_id,
            instance_id=instance_id,
            max_attempts=max_attempts,
            error_code="scheduled_action_temporary_error",
            error_message=str(error),
        )
    except Exception as error:
        logger.exception("定时任务动作失败: run_id=%s", run_id)
        await service.fail_non_retryable(
            run_id=run_id,
            instance_id=instance_id,
            error_code="scheduled_action_error",
            error_message=str(error),
        )


async def run() -> None:
    config = load_runtime_config()
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.ensure_business_schema()
    service = ScheduledJobDispatcherService(pg_manager.AsyncSession)
    instance_id = f"dispatcher:{config.instance_id}"
    stop_event = asyncio.Event()
    _install_stop_signals(stop_event)
    active: set[asyncio.Task[None]] = set()
    last_heartbeat = 0.0

    while not stop_event.is_set():
        completed = {task for task in active if task.done()}
        active -= completed
        for task in completed:
            try:
                task.result()
            except Exception:
                logger.exception("定时任务 Dispatcher 动作协程异常退出")
        try:
            if time.monotonic() - last_heartbeat >= 10:
                await service.heartbeat(instance_id=config.instance_id)
                last_heartbeat = time.monotonic()
            free_slots = config.dispatch_concurrency - len(active)
            if free_slots > 0:
                run_ids = await service.claim_once(
                    instance_id=instance_id,
                    limit=min(config.dispatch_batch_size, free_slots),
                    lease_seconds=config.dispatch_lease_seconds,
                )
                for run_id in run_ids:
                    task = asyncio.create_task(
                        _dispatch_one(
                            service,
                            run_id=run_id,
                            instance_id=instance_id,
                            action_timeout_seconds=config.dispatch_action_timeout_seconds,
                            max_attempts=config.dispatch_max_attempts,
                        )
                    )
                    active.add(task)
        except Exception:
            logger.exception("定时任务 Dispatcher 本轮认领失败")
            try:
                await service.heartbeat(instance_id=config.instance_id, error_code="dispatcher_loop_error")
            except Exception:
                logger.exception("定时任务 Dispatcher 错误心跳写入失败")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=config.dispatch_poll_seconds)
        except TimeoutError:
            pass

    if active:
        done, pending = await asyncio.wait(active, timeout=config.dispatch_action_timeout_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    await pg_manager.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
