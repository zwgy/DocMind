from datetime import UTC, datetime

import pytest

from yuxi.scheduled_jobs.schemas import AtSchedule, CronSchedule, IntervalSchedule
from yuxi.scheduled_jobs.timing import next_run_at


def test_at_schedule_keeps_original_utc_time_for_misfire_handling():
    run_at = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)

    assert next_run_at(AtSchedule(run_at=run_at), "Asia/Shanghai", datetime(2026, 8, 6, 10, 0, tzinfo=UTC)) == run_at


def test_interval_schedule_advances_from_anchor_without_execution_drift():
    anchor_at = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)
    schedule = IntervalSchedule(interval_seconds=300, anchor_at=anchor_at)

    assert next_run_at(schedule, "Asia/Shanghai", datetime(2026, 8, 6, 8, 17, tzinfo=UTC)) == datetime(
        2026, 8, 6, 8, 20, tzinfo=UTC
    )
    assert next_run_at(schedule, "Asia/Shanghai", datetime(2026, 8, 6, 8, 20, tzinfo=UTC)) == datetime(
        2026, 8, 6, 8, 25, tzinfo=UTC
    )


def test_cron_schedule_uses_declared_timezone():
    schedule = CronSchedule(cron_expression="0 9 * * 1-5")

    assert next_run_at(schedule, "Asia/Shanghai", datetime(2026, 8, 7, 2, 0, tzinfo=UTC)) == datetime(
        2026, 8, 10, 1, 0, tzinfo=UTC
    )


def test_next_run_at_rejects_naive_reference_time():
    with pytest.raises(ValueError, match="带时区"):
        next_run_at(
            AtSchedule(run_at=datetime(2026, 8, 6, 9, 0, tzinfo=UTC)),
            "Asia/Shanghai",
            datetime(2026, 8, 6, 8, 0),
        )
