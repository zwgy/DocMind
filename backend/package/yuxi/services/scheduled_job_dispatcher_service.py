"""独立 Dispatcher 的认领和通知分发服务。"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository


class ScheduledJobDispatcherService:
    """每个动作独立事务执行，避免网络或通知故障长时间持有批量认领锁。"""

    def __init__(self, session_factory: Callable[[], AsyncSession]):
        self._session_factory = session_factory

    async def claim_once(self, *, instance_id: str, limit: int, lease_seconds: int) -> list[str]:
        async with self._session_factory() as session:
            async with session.begin():
                runs = await ScheduledJobRepository(session).claim_runs(
                    instance_id=instance_id, limit=limit, lease_seconds=lease_seconds
                )
            return [run.id for run in runs]

    async def dispatch_notification(self, *, run_id: str, instance_id: str) -> str | None:
        async with self._session_factory() as session:
            async with session.begin():
                return await ScheduledJobRepository(session).deliver_notification(
                    run_id=run_id, instance_id=instance_id
                )

    async def retry_or_fail(
        self, *, run_id: str, instance_id: str, max_attempts: int, error_code: str, error_message: str
    ) -> str | None:
        async with self._session_factory() as session:
            async with session.begin():
                return await ScheduledJobRepository(session).reschedule_or_fail(
                    run_id=run_id,
                    instance_id=instance_id,
                    max_attempts=max_attempts,
                    error_code=error_code,
                    error_message=error_message,
                )

    async def fail_non_retryable(self, *, run_id: str, instance_id: str, error_code: str, error_message: str) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                return await ScheduledJobRepository(session).fail_run(
                    run_id=run_id,
                    instance_id=instance_id,
                    error_code=error_code,
                    error_message=error_message,
                )

    async def heartbeat(self, *, instance_id: str, error_code: str | None = None) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await ScheduledJobRepository(session).write_heartbeat(
                    service_type="dispatcher", instance_id=instance_id, error_code=error_code
                )
