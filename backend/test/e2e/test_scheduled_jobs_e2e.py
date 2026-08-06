"""真实 API 的个人定时通知闭环验收。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def test_personal_scheduled_job_lifecycle_over_live_api(e2e_client, e2e_headers):
    """创建、编辑和取消必须走与 Web/小助手相同的公开 HTTP 边界。"""
    suffix = uuid4().hex[:12]
    idempotency_key = f"pytest-scheduled-{suffix}"
    run_at = (datetime.now(UTC) + timedelta(minutes=10)).replace(second=0, microsecond=0)
    payload = {
        "name": f"pytest-scheduled-{suffix}",
        "schedule": {"kind": "at", "run_at": run_at.isoformat()},
        "action": {"type": "notification", "title": "E2E 定时通知", "content": "验证公开调度接口"},
        "timezone": "Asia/Shanghai",
    }
    headers = {**e2e_headers, "Idempotency-Key": idempotency_key}
    job_id = None
    version = None

    try:
        preview = await e2e_client.post(
            "/api/scheduled-jobs/schedule-preview",
            json={"schedule": payload["schedule"], "timezone": payload["timezone"]},
            headers=e2e_headers,
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["occurrences"][0]["utc"]

        created = await e2e_client.post("/api/scheduled-jobs", json=payload, headers=headers)
        assert created.status_code == 201, created.text
        job = created.json()["job"]
        job_id = job["id"]
        version = job["version"]

        replay = await e2e_client.post("/api/scheduled-jobs", json=payload, headers=headers)
        assert replay.status_code == 201, replay.text
        assert replay.json()["job"]["id"] == job_id

        conflict_payload = {**payload, "name": f"pytest-conflict-{suffix}"}
        conflict = await e2e_client.post("/api/scheduled-jobs", json=conflict_payload, headers=headers)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"] == "idempotency_key_reused"

        listed = await e2e_client.get("/api/scheduled-jobs", params={"view": "ongoing", "limit": 20}, headers=e2e_headers)
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == job_id for item in listed.json()["items"])

        updated = await e2e_client.patch(
            f"/api/scheduled-jobs/{job_id}",
            json={**payload, "name": f"pytest-updated-{suffix}", "version": version},
            headers=e2e_headers,
        )
        assert updated.status_code == 200, updated.text
        updated_job = updated.json()["job"]
        assert updated_job["name"] == f"pytest-updated-{suffix}"
        version = updated_job["version"]

        inbox = await e2e_client.get("/api/inbox/unread-count", headers=e2e_headers)
        assert inbox.status_code == 200, inbox.text
        assert set(inbox.json()) >= {"notification_unread_count", "task_unread_count", "total_unread_count"}
    finally:
        if job_id and version:
            cancelled = await e2e_client.post(
                f"/api/scheduled-jobs/{job_id}/status",
                json={"action": "cancel", "version": version, "reason": "pytest_cleanup"},
                headers=e2e_headers,
            )
            assert cancelled.status_code in {200, 409}, cancelled.text
