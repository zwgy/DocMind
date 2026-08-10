"""独立 Dispatcher 的认领和通知分发服务。"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository
from yuxi.services.agent_run_service import enqueue_agent_run
from yuxi.services.scheduled_job_action_handlers import get_action_handler
from yuxi.services.scheduled_job_result_service import ScheduledJobResultService
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledJobRun


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

    async def dispatch_action(self, *, run_id: str, instance_id: str) -> str | None:
        async with self._session_factory() as session:
            async with session.begin():
                repository = ScheduledJobRepository(session)
                run = await session.get(ScheduledJobRun, run_id)
                if run is None:
                    return None
                result = await get_action_handler(run.action_type).dispatch(
                    repository=repository, run_id=run_id, instance_id=instance_id
                )
        if result.agent_run_id:
            await enqueue_agent_run(result.agent_run_id)
        return result.status

    async def dispatch_notification(self, *, run_id: str, instance_id: str) -> str | None:
        """兼容第一版调用方；新代码统一通过动作注册器分发。"""
        return await self.dispatch_action(run_id=run_id, instance_id=instance_id)

    async def sync_agent_run_status(self, *, agent_run_id: str, agent_status: str) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                return await ScheduledJobResultService(session).sync_agent_run_status(
                    agent_run_id=agent_run_id, agent_status=agent_status
                )

    async def reconcile_agent_runs(self, *, limit: int) -> int:
        async with self._session_factory() as session:
            mismatches = await ScheduledJobResultService(session).list_status_mismatches(limit=limit)
        reconciled = 0
        for agent_run_id, agent_status in mismatches:
            if await self.sync_agent_run_status(agent_run_id=agent_run_id, agent_status=agent_status):
                reconciled += 1
        return reconciled

    async def backfill_terminal_results(self, *, limit: int) -> int:
        async with self._session_factory() as session:
            run_ids = await ScheduledJobResultService(session).list_terminal_projection_gaps(limit=limit)
        projected = 0
        for run_id in run_ids:
            async with self._session_factory() as session:
                async with session.begin():
                    if await ScheduledJobResultService(session).project_existing_terminal_run(scheduled_run_id=run_id):
                        projected += 1
        return projected

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
