"""Scheduler 和 Dispatcher 共用的启动期运行配置。"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须为正整数")
    return value


@dataclass(frozen=True, slots=True)
class ScheduledJobsRuntimeConfig:
    schedule_poll_seconds: int
    schedule_batch_size: int
    dispatch_poll_seconds: int
    dispatch_batch_size: int
    dispatch_concurrency: int
    dispatch_lease_seconds: int
    dispatch_action_timeout_seconds: int
    dispatch_max_attempts: int
    default_timezone: str
    instance_id: str


def load_runtime_config() -> ScheduledJobsRuntimeConfig:
    """非法配置直接阻止循环启动，避免多个实例以不一致语义领取同一运行。"""
    config = ScheduledJobsRuntimeConfig(
        schedule_poll_seconds=_positive_int("SCHEDULE_POLL_SECONDS", 5),
        schedule_batch_size=_positive_int("SCHEDULE_BATCH_SIZE", 100),
        dispatch_poll_seconds=_positive_int("DISPATCH_POLL_SECONDS", 2),
        dispatch_batch_size=_positive_int("DISPATCH_BATCH_SIZE", 100),
        dispatch_concurrency=_positive_int("DISPATCH_CONCURRENCY", 10),
        dispatch_lease_seconds=_positive_int("DISPATCH_LEASE_SECONDS", 60),
        dispatch_action_timeout_seconds=_positive_int("DISPATCH_ACTION_TIMEOUT_SECONDS", 30),
        dispatch_max_attempts=_positive_int("DISPATCH_MAX_ATTEMPTS", 5),
        default_timezone=(os.getenv("SCHEDULE_DEFAULT_TIMEZONE") or "Asia/Shanghai").strip(),
        instance_id=(os.getenv("SCHEDULE_INSTANCE_ID") or socket.gethostname()).strip(),
    )
    if config.dispatch_lease_seconds <= config.dispatch_action_timeout_seconds:
        raise ValueError("DISPATCH_LEASE_SECONDS 必须大于 DISPATCH_ACTION_TIMEOUT_SECONDS")
    if not config.default_timezone or not config.instance_id:
        raise ValueError("SCHEDULE_DEFAULT_TIMEZONE 和 SCHEDULE_INSTANCE_ID 不能为空")
    return config
