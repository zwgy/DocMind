"""第一版三类调度规则的确定性时间计算。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil
from zoneinfo import ZoneInfo

from croniter import croniter

from yuxi.scheduled_jobs.schemas import AtSchedule, CronSchedule, IntervalSchedule, Schedule


def next_run_at(schedule: Schedule, timezone: str, after: datetime, *, inclusive: bool = False) -> datetime:
    """计算规则在指定时点之后的下一次 UTC 触发时间。

    interval 始终以固定锚点为基准，不能以本次实际完成时间累计，避免停机或慢执行造成漂移。
    """
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("after 必须带时区")

    after_utc = after.astimezone(UTC)
    if isinstance(schedule, AtSchedule):
        run_at = schedule.run_at.astimezone(UTC)
        return run_at

    if isinstance(schedule, IntervalSchedule):
        anchor_at = schedule.anchor_at.astimezone(UTC)
        if after_utc < anchor_at or (inclusive and after_utc == anchor_at):
            return anchor_at
        elapsed_seconds = (after_utc - anchor_at).total_seconds()
        steps = ceil(elapsed_seconds / schedule.interval_seconds)
        if not inclusive and anchor_at + timedelta(seconds=steps * schedule.interval_seconds) == after_utc:
            steps += 1
        return anchor_at + timedelta(seconds=steps * schedule.interval_seconds)

    if isinstance(schedule, CronSchedule):
        local_after = after_utc.astimezone(ZoneInfo(timezone))
        if inclusive:
            local_after -= timedelta(minutes=1)
        return croniter(schedule.cron_expression, local_after).get_next(datetime).astimezone(UTC)

    raise TypeError(f"不支持的调度类型: {type(schedule)!r}")
