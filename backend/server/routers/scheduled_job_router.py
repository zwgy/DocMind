"""个人定时任务 HTTP 边界。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository
from yuxi.scheduled_jobs.schemas import AtSchedule, PersonalScheduledJobRequest, Schedule, ScheduledJobDraft
from yuxi.scheduled_jobs.timing import next_run_at
from yuxi.services.scheduled_job_service import (
    IdempotencyKeyReusedError,
    JobAlreadyTriggeredError,
    JobVersionConflictError,
    ScheduledJobDomainError,
    ScheduledJobService,
)
from yuxi.services.run_queue_service import publish_cancel_signal
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledJob

scheduled_jobs = APIRouter(prefix="/scheduled-jobs", tags=["scheduled-jobs"])


class StatusChangeRequest(BaseModel):
    action: Literal["pause", "resume", "cancel"]
    version: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=128)


class ScheduledJobPatchRequest(PersonalScheduledJobRequest):
    version: int = Field(ge=1)


class SchedulePreviewRequest(BaseModel):
    """只校验和展示规则，不创建任务，供候选和任务编辑共用。"""

    schedule: Schedule
    timezone: str = Field(min_length=1, max_length=64)

    def model_post_init(self, __context) -> None:
        ScheduledJobDraft.validate_timezone(self.timezone)


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _serialize_job(job: ScheduledJob) -> dict:
    action_data = dict(job.action_data or {})
    # 配置摘要仅用于数据库审计，不能成为客户端可回传的运行时配置。
    action_data.pop("agent_config_snapshot", None)
    return {
        "id": job.id,
        "name": job.name,
        "source_type": job.source_type,
        "schedule_kind": job.schedule_kind,
        "run_at": _format_datetime(job.run_at),
        "anchor_at": _format_datetime(job.anchor_at),
        "interval_seconds": job.interval_seconds,
        "cron_expression": job.cron_expression,
        "timezone": job.timezone,
        "next_run_at": _format_datetime(job.next_run_at),
        "action_type": job.action_type,
        "action_data": action_data,
        "status": job.status,
        "version": job.version,
        "last_run_at": _format_datetime(job.last_run_at),
        "paused_at": _format_datetime(job.paused_at),
        "cancelled_at": _format_datetime(job.cancelled_at),
        "created_at": _format_datetime(job.created_at),
        "updated_at": _format_datetime(job.updated_at),
    }


def _serialize_run(run) -> dict:
    return {
        "id": run.id,
        "scheduled_for": _format_datetime(run.scheduled_for),
        "status": run.status,
        "attempt_count": run.attempt_count,
        "result_data": run.result_data,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "started_at": _format_datetime(run.started_at),
        "finished_at": _format_datetime(run.finished_at),
        "agent_run_id": run.agent_run_id,
        "conversation_id": run.conversation_id,
        "created_at": _format_datetime(run.created_at),
    }


def _raise_domain_error(error: ScheduledJobDomainError) -> None:
    if isinstance(error, IdempotencyKeyReusedError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="idempotency_key_reused") from error
    if isinstance(error, JobVersionConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="version_conflict") from error
    if isinstance(error, JobAlreadyTriggeredError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_already_triggered") from error
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


def _schedule_preview(*, schedule: Schedule, timezone: str, now: datetime) -> dict:
    """按同一条时间规则生成最多三次将来触发，避免 UI 自行解释 Cron。"""
    first_run_at = next_run_at(schedule, timezone, now, inclusive=False)
    if isinstance(schedule, AtSchedule) and first_run_at <= now:
        raise ScheduledJobDomainError("一次性任务触发时间必须晚于当前时间")

    occurrences: list[datetime] = [first_run_at]
    if not isinstance(schedule, AtSchedule):
        cursor = first_run_at
        for _ in range(2):
            cursor = next_run_at(schedule, timezone, cursor, inclusive=False)
            occurrences.append(cursor)

    display_timezone = ZoneInfo(timezone)
    return {
        "schedule": schedule.model_dump(mode="json"),
        "timezone": timezone,
        "next_run_at": first_run_at.astimezone(UTC).isoformat(),
        "occurrences": [
            {
                "utc": occurrence.astimezone(UTC).isoformat(),
                "local": occurrence.astimezone(display_timezone).isoformat(),
            }
            for occurrence in occurrences
        ],
    }


@scheduled_jobs.post("", status_code=status.HTTP_201_CREATED)
async def create_scheduled_job(
    payload: PersonalScheduledJobRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        job = await ScheduledJobService(db).create_personal_job(
            owner_uid=current_user.uid,
            request=payload,
            idempotency_key=idempotency_key,
        )
        await db.commit()
    except ScheduledJobDomainError as error:
        await db.rollback()
        _raise_domain_error(error)
    return {"job": _serialize_job(job)}


@scheduled_jobs.post("/schedule-preview")
async def preview_schedule(
    payload: SchedulePreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """必须置于 ``/{job_id}`` 前，避免将固定路径误识别为任务 ID。"""
    try:
        now = await ScheduledJobRepository(db).database_now()
        return _schedule_preview(schedule=payload.schedule, timezone=payload.timezone, now=now)
    except ScheduledJobDomainError as error:
        _raise_domain_error(error)


@scheduled_jobs.get("")
async def list_scheduled_jobs(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
    view: Literal["ongoing", "paused", "history"] = Query(default="ongoing"),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
):
    statuses = {
        "ongoing": ("active",),
        "paused": ("paused",),
        "history": ("completed", "cancelled"),
    }[view]
    try:
        jobs, next_cursor = await ScheduledJobService(db).list_owned_jobs(
            owner_uid=current_user.uid,
            statuses=statuses,
            cursor=cursor,
            limit=limit,
        )
    except ScheduledJobDomainError as error:
        _raise_domain_error(error)
    return {"items": [_serialize_job(job) for job in jobs], "next_cursor": next_cursor}


@scheduled_jobs.get("/{job_id}")
async def get_scheduled_job(
    job_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    job = await ScheduledJobService(db).get_owned_job(job_id=job_id, owner_uid=current_user.uid)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scheduled_job_not_found")
    return {"job": _serialize_job(job)}


@scheduled_jobs.patch("/{job_id}")
async def update_scheduled_job(
    job_id: str,
    payload: ScheduledJobPatchRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        request = PersonalScheduledJobRequest.model_validate(payload.model_dump(exclude={"version"}))
        job = await ScheduledJobService(db).update_personal_job(
            job_id=job_id, owner_uid=current_user.uid, version=payload.version, request=request
        )
        await db.commit()
    except ScheduledJobDomainError as error:
        await db.rollback()
        _raise_domain_error(error)
    return {"job": _serialize_job(job)}


@scheduled_jobs.get("/{job_id}/runs")
async def list_scheduled_job_runs(
    job_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
):
    try:
        runs, next_cursor = await ScheduledJobService(db).list_owned_runs(
            job_id=job_id, owner_uid=current_user.uid, cursor=cursor, limit=limit
        )
    except ScheduledJobDomainError as error:
        _raise_domain_error(error)
    return {"items": [_serialize_run(run) for run in runs], "next_cursor": next_cursor}


@scheduled_jobs.post("/{job_id}/status")
async def change_scheduled_job_status(
    job_id: str,
    payload: StatusChangeRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        service = ScheduledJobService(db)
        if payload.action == "pause":
            job = await service.pause(job_id=job_id, owner_uid=current_user.uid, version=payload.version)
        elif payload.action == "resume":
            job = await service.resume(job_id=job_id, owner_uid=current_user.uid, version=payload.version)
        else:
            job = await service.cancel(
                job_id=job_id,
                owner_uid=current_user.uid,
                version=payload.version,
                reason=payload.reason,
            )
        await db.commit()
        for agent_run_id in service.cancelled_agent_run_ids:
            await publish_cancel_signal(agent_run_id)
    except ScheduledJobDomainError as error:
        await db.rollback()
        _raise_domain_error(error)
    return {"job": _serialize_job(job)}
