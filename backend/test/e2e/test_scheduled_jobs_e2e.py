"""真实 API 的个人定时通知闭环验收。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import (
    InboxItem,
    ScheduledJob,
    ScheduledJobAuditLog,
    ScheduledJobRecipient,
    ScheduledJobRun,
)
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def test_personal_scheduled_job_lifecycle_over_live_api(e2e_client):
    """创建、编辑和取消必须走与 Web/小助手相同的公开 HTTP 边界。"""
    suffix = uuid4().hex[:12]
    username = f"pytest_schedule_{suffix}"
    password = f"Pytest!{suffix}"
    uid = f"pytest_schedule_{suffix}"
    idempotency_key = f"pytest-scheduled-{suffix}"
    run_at = (datetime.now(UTC) + timedelta(minutes=10)).replace(second=0, microsecond=0)
    payload = {
        "name": f"pytest-scheduled-{suffix}",
        "schedule": {"kind": "at", "run_at": run_at.isoformat()},
        "action": {"type": "notification", "title": "E2E 定时通知", "content": "验证公开调度接口"},
        "timezone": "Asia/Shanghai",
    }
    async with pg_manager.get_async_session_context() as session:
        session.add(User(uid=uid, username=username, password_hash=AuthUtils.hash_password(password), role="user"))

    login = await e2e_client.post("/api/auth/token", data={"username": username, "password": password})
    assert login.status_code == 200, login.text
    e2e_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
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
        # E2E 测试直接创建临时账号，必须按任务外键依赖顺序完全清理。
        async with pg_manager.get_async_session_context() as session:
            job_ids = [job_id] if job_id else []
            if job_ids:
                run_ids = [
                    row[0]
                    for row in (
                        await session.execute(
                            ScheduledJobRun.__table__.select()
                            .with_only_columns(ScheduledJobRun.id)
                            .where(ScheduledJobRun.scheduled_job_id.in_(job_ids))
                        )
                    ).all()
                ]
                if run_ids:
                    await session.execute(delete(InboxItem).where(InboxItem.scheduled_job_run_id.in_(run_ids)))
                await session.execute(delete(InboxItem).where(InboxItem.scheduled_job_id.in_(job_ids)))
                await session.execute(delete(ScheduledJobAuditLog).where(ScheduledJobAuditLog.scheduled_job_id.in_(job_ids)))
                await session.execute(delete(ScheduledJobRun).where(ScheduledJobRun.scheduled_job_id.in_(job_ids)))
                await session.execute(delete(ScheduledJobRecipient).where(ScheduledJobRecipient.scheduled_job_id.in_(job_ids)))
                await session.execute(delete(ScheduledJob).where(ScheduledJob.id.in_(job_ids)))
            await session.execute(delete(User).where(User.uid == uid))
