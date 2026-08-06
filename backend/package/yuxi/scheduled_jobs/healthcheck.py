"""Scheduler 与 Dispatcher 的 Docker 健康检查入口。"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository
from yuxi.scheduled_jobs.runtime import load_runtime_config
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledServiceHeartbeat


async def is_healthy(service_type: str) -> bool:
    """心跳滞后即判为不健康，避免循环异常退出后容器仍被误判为存活。"""
    if service_type not in {"scheduler", "dispatcher"}:
        raise ValueError("service_type 必须是 scheduler 或 dispatcher")
    config = load_runtime_config()
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as session:
        repository = ScheduledJobRepository(session)
        now = await repository.database_now()
        heartbeat = await session.scalar(
            select(ScheduledServiceHeartbeat).where(
                ScheduledServiceHeartbeat.service_type == service_type,
                ScheduledServiceHeartbeat.instance_id == config.instance_id,
            )
        )
        return heartbeat is not None and (now - heartbeat.last_seen_at).total_seconds() <= 30


def main() -> None:
    service_type = sys.argv[1] if len(sys.argv) == 2 else ""
    if not asyncio.run(is_healthy(service_type)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
