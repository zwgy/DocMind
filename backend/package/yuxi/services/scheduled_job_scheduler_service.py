"""独立 Scheduler 的单轮事务服务。"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository


class ScheduledJobSchedulerService:
    """将一次轮询压缩成短事务，提交后由 Dispatcher 扫描持久化运行。"""

    def __init__(self, session_factory: Callable[[], AsyncSession]):
        self._session_factory = session_factory

    async def schedule_once(self, *, batch_size: int) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                runs = await ScheduledJobRepository(session).create_due_runs(batch_size=batch_size)
            return len(runs)

    async def heartbeat(self, *, instance_id: str, error_code: str | None = None) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await ScheduledJobRepository(session).write_heartbeat(
                    service_type="scheduler", instance_id=instance_id, error_code=error_code
                )
