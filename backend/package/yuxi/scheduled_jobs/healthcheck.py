"""Scheduler 与 Dispatcher 的 Docker 健康检查入口。"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from yuxi.scheduled_jobs.runtime import load_runtime_config


def _postgres_dsn() -> str:
    """健康检查只需要一个原生连接，不能为短命探针初始化应用级连接池。"""
    database_url = os.environ["POSTGRES_URL"]
    return database_url.replace("+asyncpg", "").replace("+psycopg", "")


async def is_healthy(service_type: str) -> bool:
    """心跳滞后即判为不健康，避免循环异常退出后容器仍被误判为存活。"""
    if service_type not in {"scheduler", "dispatcher"}:
        raise ValueError("service_type 必须是 scheduler 或 dispatcher")
    config = load_runtime_config()
    connection: asyncpg.Connection | None = None
    try:
        connection = await asyncpg.connect(_postgres_dsn(), timeout=2, command_timeout=2)
        return bool(
            await connection.fetchval(
                """
                SELECT last_seen_at >= NOW() - INTERVAL '30 seconds'
                FROM scheduled_service_heartbeats
                WHERE service_type = $1 AND instance_id = $2
                """,
                service_type,
                config.instance_id,
            )
        )
    except (TimeoutError, asyncpg.PostgresError, OSError):
        return False
    finally:
        if connection is not None:
            await connection.close()


def main() -> None:
    service_type = sys.argv[1] if len(sys.argv) == 2 else ""
    if not asyncio.run(is_healthy(service_type)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
