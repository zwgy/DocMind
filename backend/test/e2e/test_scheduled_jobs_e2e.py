"""真实 API 的个人定时通知闭环验收。"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, OperationLog, User
from yuxi.storage.postgres.models_knowledge import IncomingDocument
from yuxi.storage.postgres.models_scheduled_jobs import (
    InboxItem,
    IncomingTaskBatch,
    ScheduledJob,
    ScheduledJobAuditLog,
    ScheduledJobCandidate,
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
    run_at = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(minutes=10)).replace(second=0, microsecond=0)
    payload = {
        "name": f"pytest-scheduled-{suffix}",
        "schedule": {"kind": "at", "run_at": run_at.isoformat()},
        "action": {"type": "notification", "title": "E2E 定时通知", "content": "验证公开调度接口"},
        "timezone": "Asia/Shanghai",
    }
    async with pg_manager.get_async_session_context() as session:
        department = Department(name=f"pytest_schedule_{suffix}")
        session.add(department)
        await session.flush()
        department_id = department.id
        user = User(
            uid=uid,
            username=username,
            password_hash=AuthUtils.hash_password(password),
            role="user",
            department_id=department_id,
        )
        session.add(user)
        await session.flush()
        user_id = user.id

    e2e_headers = None
    job_id = None
    version = None

    try:
        login = await e2e_client.post("/api/auth/token", data={"username": username, "password": password})
        assert login.status_code == 200, login.text
        e2e_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        headers = {**e2e_headers, "Idempotency-Key": idempotency_key}
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

        listed = await e2e_client.get(
            "/api/scheduled-jobs",
            params={"view": "ongoing", "limit": 20},
            headers=e2e_headers,
        )
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
        if job_id and version and e2e_headers:
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
                await session.execute(
                    delete(ScheduledJobAuditLog).where(ScheduledJobAuditLog.scheduled_job_id.in_(job_ids))
                )
                await session.execute(delete(ScheduledJobRun).where(ScheduledJobRun.scheduled_job_id.in_(job_ids)))
                await session.execute(
                    delete(ScheduledJobRecipient).where(ScheduledJobRecipient.scheduled_job_id.in_(job_ids))
                )
                await session.execute(delete(ScheduledJob).where(ScheduledJob.id.in_(job_ids)))
            # 批量删除不会触发 ORM 关系级联，登录留下的操作日志须先清理。
            await session.execute(delete(OperationLog).where(OperationLog.user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.execute(delete(Department).where(Department.id == department_id))
        await pg_manager.close()


async def test_pending_candidate_is_only_visible_to_admin_review_api(e2e_client):
    """候选只能出现在待确认接口，不能被收件箱或普通用户访问。"""
    suffix = uuid4().hex[:12]
    admin_uid = f"pytest_candidate_admin_{suffix}"
    viewer_uid = f"pytest_candidate_viewer_{suffix}"
    department_id = None
    admin_id = None
    viewer_id = None
    incoming_id = f"inc_e2e_candidate_{suffix}"
    batch_id = f"sjb_e2e_candidate_{suffix}"
    candidate_id = f"sjc_e2e_candidate_{suffix}"
    admin_password = f"Pytest!Admin{suffix}"
    viewer_password = f"Pytest!Viewer{suffix}"
    candidate_run_at = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(hours=1)).replace(
        second=0,
        microsecond=0,
    )

    async with pg_manager.get_async_session_context() as session:
        department = Department(name=f"pytest_candidate_{suffix}")
        session.add(department)
        await session.flush()
        department_id = department.id
        admin = User(
            uid=admin_uid,
            username=f"pytest_candidate_admin_{suffix}",
            password_hash=AuthUtils.hash_password(admin_password),
            role="superadmin",
            department_id=department_id,
        )
        viewer = User(
            uid=viewer_uid,
            username=f"pytest_candidate_viewer_{suffix}",
            password_hash=AuthUtils.hash_password(viewer_password),
            role="user",
            department_id=department_id,
        )
        session.add_all([admin, viewer])
        await session.flush()
        admin_id = admin.id
        viewer_id = viewer.id
        session.add_all(
            [
                IncomingDocument(
                    incoming_id=incoming_id,
                    source_system="e2e-test",
                    source_function_id="scheduled-jobs",
                    source_document_id=suffix,
                    document_metadata={"title": "候选隔离验收"},
                    status="extracted",
                    created_by=admin_uid,
                ),
                IncomingTaskBatch(
                    id=batch_id,
                    incoming_id=incoming_id,
                    extraction_run_id=f"run_e2e_candidate_{suffix}",
                    status="ready",
                    candidate_count=1,
                ),
                ScheduledJobCandidate(
                    id=candidate_id,
                    batch_id=batch_id,
                    extraction_item_id=f"item_e2e_candidate_{suffix}",
                    incoming_id=incoming_id,
                    extraction_run_id=f"run_e2e_candidate_{suffix}",
                    owner_uid=admin_uid,
                    name="候选隔离验收任务",
                    notification_title="候选隔离验收通知",
                    notification_content="候选尚未启用，不应进入收件箱。",
                    schedule_data={"kind": "at", "run_at": candidate_run_at.isoformat()},
                    timezone="Asia/Shanghai",
                    recipient_scope="named",
                    raw_recipient_names=[admin.username],
                    recipient_resolution={admin.username: admin_uid},
                    resolved_recipient_uids=[admin_uid],
                    evidence={"source_file_id": "e2e_candidate_file"},
                    validation_errors=[],
                    validation_warnings=[],
                    status="pending_confirmation",
                ),
            ]
        )

    try:
        admin_login = await e2e_client.post(
            "/api/auth/token",
            data={"username": f"pytest_candidate_admin_{suffix}", "password": admin_password},
        )
        assert admin_login.status_code == 200, admin_login.text
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        viewer_login = await e2e_client.post(
            "/api/auth/token",
            data={"username": f"pytest_candidate_viewer_{suffix}", "password": viewer_password},
        )
        assert viewer_login.status_code == 200, viewer_login.text
        viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}

        candidates = await e2e_client.get(
            "/api/scheduled-job-candidates",
            params={"status": "pending_confirmation"},
            headers=admin_headers,
        )
        assert candidates.status_code == 200, candidates.text
        assert any(item["id"] == candidate_id for item in candidates.json()["items"])

        forbidden = await e2e_client.get("/api/scheduled-job-candidates", headers=viewer_headers)
        assert forbidden.status_code == 403, forbidden.text

        notifications = await e2e_client.get("/api/inbox/notifications", headers=admin_headers)
        assert notifications.status_code == 200, notifications.text
        assert notifications.json()["items"] == []
        tasks = await e2e_client.get("/api/inbox/tasks", headers=admin_headers)
        assert tasks.status_code == 200, tasks.text
        assert tasks.json()["items"] == []
        unread = await e2e_client.get("/api/inbox/unread-count", headers=admin_headers)
        assert unread.status_code == 200, unread.text
        assert unread.json() == {
            "notification_unread_count": 0,
            "task_unread_count": 0,
            "total_unread_count": 0,
        }
    finally:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(ScheduledJobCandidate).where(ScheduledJobCandidate.id == candidate_id))
            await session.execute(delete(IncomingTaskBatch).where(IncomingTaskBatch.id == batch_id))
            await session.execute(delete(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id))
            await session.execute(delete(OperationLog).where(OperationLog.user_id.in_([admin_id, viewer_id])))
            await session.execute(delete(User).where(User.id.in_([admin_id, viewer_id])))
            await session.execute(delete(Department).where(Department.id == department_id))
        await pg_manager.close()
