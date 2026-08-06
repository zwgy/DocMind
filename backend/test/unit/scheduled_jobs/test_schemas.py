from datetime import datetime

import pytest
from pydantic import ValidationError

from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.scheduled_jobs.schemas import IncomingTaskDraft, ScheduledJobDraft


def test_scheduled_job_draft_normalizes_naive_time_to_declared_timezone_and_minute():
    draft = ScheduledJobDraft.model_validate(
        {
            "name": "提交材料",
            "schedule": {"kind": "at", "run_at": "2026-08-06T08:17:45"},
            "action": {"type": "notification", "title": "提交材料", "content": "请提交材料"},
            "recipient_uids": ["user-1"],
            "timezone": "Asia/Shanghai",
        }
    )

    assert draft.schedule.run_at == datetime(2026, 8, 6, 8, 17, tzinfo=draft.schedule.run_at.tzinfo)
    assert draft.schedule.run_at.utcoffset().total_seconds() == 8 * 60 * 60


def test_scheduled_job_draft_rejects_offset_that_conflicts_with_timezone():
    with pytest.raises(ValidationError, match="实际偏移不一致"):
        ScheduledJobDraft.model_validate(
            {
                "name": "提交材料",
                "schedule": {"kind": "at", "run_at": "2026-08-06T08:17:00+00:00"},
                "action": {"type": "notification", "title": "提交材料", "content": "请提交材料"},
                "recipient_uids": ["user-1"],
                "timezone": "Asia/Shanghai",
            }
        )


@pytest.mark.parametrize("expression", ["* * * *", "* * * * * *", "not a cron"])
def test_scheduled_job_draft_rejects_non_five_field_cron(expression: str):
    with pytest.raises(ValidationError):
        ScheduledJobDraft.model_validate(
            {
                "name": "周期任务",
                "schedule": {"kind": "cron", "cron_expression": expression},
                "action": {"type": "notification", "title": "提醒", "content": "内容"},
                "recipient_uids": ["user-1"],
                "timezone": "Asia/Shanghai",
            }
        )


def test_incoming_task_draft_keeps_missing_schedule_for_manual_confirmation():
    draft = IncomingTaskDraft.model_validate(
        {
            "task_name": "待确认任务",
            "notification_title": "待确认",
            "notification_content": "请确认时间",
            "recipient_scope": "unknown",
            "source_quote": "请在适当时间完成",
            "source_file_id": "file-1",
        }
    )

    assert draft.schedule is None
    assert draft.recipient_names == []


def test_new_scheduled_job_id_uses_declared_prefix_only():
    assert new_scheduled_job_id("sj_").startswith("sj_")

    with pytest.raises(ValueError, match="不支持"):
        new_scheduled_job_id("unknown_")
