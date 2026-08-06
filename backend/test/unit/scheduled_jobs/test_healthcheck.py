from __future__ import annotations

from unittest.mock import AsyncMock

import asyncpg
import pytest

from yuxi.scheduled_jobs import healthcheck

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_healthcheck_uses_one_short_lived_connection(monkeypatch: pytest.MonkeyPatch):
    connection = AsyncMock()
    connection.fetchval.return_value = True
    connect = AsyncMock(return_value=connection)
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@db:5432/yuxi")
    monkeypatch.setenv("SCHEDULE_INSTANCE_ID", "scheduler-one")
    monkeypatch.setattr(healthcheck.asyncpg, "connect", connect)

    assert await healthcheck.is_healthy("scheduler") is True
    connect.assert_awaited_once_with("postgresql://postgres:postgres@db:5432/yuxi", timeout=2, command_timeout=2)
    connection.fetchval.assert_awaited_once()
    connection.close.assert_awaited_once()


async def test_healthcheck_returns_false_when_postgres_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@db:5432/yuxi")
    monkeypatch.setattr(healthcheck.asyncpg, "connect", AsyncMock(side_effect=asyncpg.PostgresError("offline")))

    assert await healthcheck.is_healthy("dispatcher") is False
