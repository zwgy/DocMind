"""个人定时任务 HTTP 边界。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.scheduled_jobs.schemas import PersonalScheduledJobRequest
from yuxi.services.scheduled_job_service import (
    IdempotencyKeyReusedError,
    JobAlreadyTriggeredError,
    JobVersionConflictError,
    ScheduledJobDomainError,
    ScheduledJobService,
)
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledJob

scheduled_jobs = APIRouter(prefix="/scheduled-jobs", tags=["scheduled-jobs"])


class StatusChangeRequest(BaseModel):
    action: Literal["pause", "resume", "cancel"]
    version: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=128)


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _serialize_job(job: ScheduledJob) -> dict:
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
        "action_data": job.action_data,
        "status": job.status,
        "version": job.version,
        "last_run_at": _format_datetime(job.last_run_at),
        "paused_at": _format_datetime(job.paused_at),
        "cancelled_at": _format_datetime(job.cancelled_at),
        "created_at": _format_datetime(job.created_at),
        "updated_at": _format_datetime(job.updated_at),
    }


def _raise_domain_error(error: ScheduledJobDomainError) -> None:
    if isinstance(error, IdempotencyKeyReusedError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="idempotency_key_reused") from error
    if isinstance(error, JobVersionConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="version_conflict") from error
    if isinstance(error, JobAlreadyTriggeredError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_already_triggered") from error
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@scheduled_jobs.post("", status_code=status.HTTP_201_CREATED)
async def create_scheduled_job(
    payload: PersonalScheduledJobRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        async with db.begin():
            job = await ScheduledJobService(db).create_personal_job(
                owner_uid=current_user.uid,
                request=payload,
                idempotency_key=idempotency_key,
            )
    except ScheduledJobDomainError as error:
        _raise_domain_error(error)
    return {"job": _serialize_job(job)}


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


@scheduled_jobs.post("/{job_id}/status")
async def change_scheduled_job_status(
    job_id: str,
    payload: StatusChangeRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        async with db.begin():
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
    except ScheduledJobDomainError as error:
        _raise_domain_error(error)
    return {"job": _serialize_job(job)}
