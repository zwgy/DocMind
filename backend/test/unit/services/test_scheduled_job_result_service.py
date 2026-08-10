from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services.scheduled_job_result_service import ScheduledJobResultService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_result_projection_uses_last_visible_message_and_deduplicates_artifacts():
    messages = [
        SimpleNamespace(id=1, content=" earlier ", extra_metadata={"presented_artifacts": ["/a.pdf"]}),
        SimpleNamespace(
            id=2,
            content="  最终\r\n结果  ",
            extra_metadata={"presented_artifacts": ["/a.pdf", "/b.xlsx", "/b.xlsx"]},
        ),
    ]
    scalar_result = SimpleNamespace(all=lambda: messages)
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(id=7)), scalars=AsyncMock(return_value=scalar_result)
    )
    service = ScheduledJobResultService(db)

    projection = await service._build_projection(
        SimpleNamespace(
            id="run-1",
            conversation_id=7,
            thread_id="thread-1",
            uid="alice",
            agent_id="agent-1",
            output_message_id=2,
        ),
        "completed",
    )

    assert projection == {
        "final_message_id": 2,
        "result_preview": "最终 结果",
        "artifact_count": 2,
    }


def test_result_preview_is_deterministic_and_truncated():
    value = "  " + "a" * 301 + "\n"

    preview = ScheduledJobResultService._normalize_preview(value)

    assert preview == "a" * 300 + "…"
