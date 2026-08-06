"""来文定时任务候选的 HTTP 边界。"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db
from yuxi.scheduled_jobs.schemas import NotificationAction, Schedule
from yuxi.services.incoming_task_candidate_service import (
    CandidateVersionConflictError,
    IncomingTaskBatchFrozenError,
    IncomingTaskCandidateError,
    IncomingTaskCandidateService,
)
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledJobCandidate

scheduled_job_candidates = APIRouter(prefix="/scheduled-job-candidates", tags=["scheduled-job-candidates"])


class CandidatePatchRequest(BaseModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    action: NotificationAction | None = None
    schedule: Schedule | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    recipient_scope: Literal["named", "all", "unknown"] | None = None
    recipient_names: list[str] | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def validate_recipient_selection(self) -> CandidatePatchRequest:
        if self.recipient_scope == "named" and not self.recipient_names:
            raise ValueError("recipient_scope 为 named 时 recipient_names 不能为空")
        if self.recipient_scope in {"all", "unknown"} and self.recipient_names:
            raise ValueError("recipient_scope 为 all 或 unknown 时 recipient_names 必须为空")
        return self


class CandidateEnableRequest(BaseModel):
    version: int = Field(ge=1)


class CandidateRejectRequest(BaseModel):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=512)


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _serialize_candidate(candidate: ScheduledJobCandidate) -> dict:
    return {
        "id": candidate.id,
        "batch_id": candidate.batch_id,
        "incoming_id": candidate.incoming_id,
        "extraction_run_id": candidate.extraction_run_id,
        "extraction_item_id": candidate.extraction_item_id,
        "owner_uid": candidate.owner_uid,
        "name": candidate.name,
        "action": {
            "type": "notification",
            "title": candidate.notification_title,
            "content": candidate.notification_content,
        },
        "schedule": candidate.schedule_data,
        "timezone": candidate.timezone,
        "recipient_scope": candidate.recipient_scope,
        "recipient_names": candidate.raw_recipient_names or [],
        "recipient_resolution": candidate.recipient_resolution or {},
        "resolved_recipient_uids": candidate.resolved_recipient_uids or [],
        "evidence": candidate.evidence or {},
        "validation_errors": candidate.validation_errors or [],
        "validation_warnings": candidate.validation_warnings or [],
        "status": candidate.status,
        "version": candidate.version,
        "enabled_at": _format_datetime(candidate.enabled_at),
        "rejected_at": _format_datetime(candidate.rejected_at),
        "created_at": _format_datetime(candidate.created_at),
        "updated_at": _format_datetime(candidate.updated_at),
    }


def _encode_cursor(candidate: ScheduledJobCandidate) -> str:
    payload = json.dumps(
        {"updated_at": candidate.updated_at.astimezone(UTC).isoformat(), "id": candidate.id},
        separators=(",", ":"),
    )
    return urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        payload = json.loads(urlsafe_b64decode(cursor.encode()).decode())
        updated_at = datetime.fromisoformat(payload["updated_at"])
        candidate_id = payload["id"]
    except (BinasciiError, KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_cursor") from error
    if updated_at.tzinfo is None or updated_at.utcoffset() is None or not isinstance(candidate_id, str) or not candidate_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_cursor")
    return updated_at.astimezone(UTC), candidate_id


def _raise_candidate_error(error: IncomingTaskCandidateError) -> None:
    if isinstance(error, (CandidateVersionConflictError, IncomingTaskBatchFrozenError)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@scheduled_job_candidates.get("")
async def list_scheduled_job_candidates(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status", max_length=24),
    incoming_id: str | None = Query(default=None, max_length=64),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
):
    del current_user
    statement = select(ScheduledJobCandidate)
    if status_filter:
        statement = statement.where(ScheduledJobCandidate.status == status_filter)
    if incoming_id:
        statement = statement.where(ScheduledJobCandidate.incoming_id == incoming_id)
    if cursor:
        updated_at, candidate_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                ScheduledJobCandidate.updated_at < updated_at,
                and_(ScheduledJobCandidate.updated_at == updated_at, ScheduledJobCandidate.id < candidate_id),
            )
        )
    rows = await db.scalars(
        statement.order_by(ScheduledJobCandidate.updated_at.desc(), ScheduledJobCandidate.id.desc()).limit(limit + 1)
    )
    candidates = list(rows.all())
    page = candidates[:limit]
    return {
        "items": [_serialize_candidate(candidate) for candidate in page],
        "next_cursor": _encode_cursor(page[-1]) if len(candidates) > limit else None,
    }


@scheduled_job_candidates.get("/{candidate_id}")
async def get_scheduled_job_candidate(
    candidate_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    candidate = await db.scalar(select(ScheduledJobCandidate).where(ScheduledJobCandidate.id == candidate_id))
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scheduled_job_candidate_not_found")
    return {"candidate": _serialize_candidate(candidate)}


@scheduled_job_candidates.patch("/{candidate_id}")
async def update_scheduled_job_candidate(
    candidate_id: str,
    payload: CandidatePatchRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    fields = payload.model_fields_set
    try:
        async with db.begin():
            candidate = await IncomingTaskCandidateService(db).update_candidate(
                candidate_id=candidate_id,
                actor_uid=current_user.uid,
                version=payload.version,
                name=payload.name if "name" in fields else None,
                notification_title=payload.action.title if payload.action is not None else None,
                notification_content=payload.action.content if payload.action is not None else None,
                schedule_data=payload.schedule.model_dump(mode="json") if payload.schedule is not None else None,
                timezone=payload.timezone if "timezone" in fields else None,
                recipient_scope=payload.recipient_scope if "recipient_scope" in fields else None,
                recipient_names=payload.recipient_names if "recipient_names" in fields else None,
            )
    except IncomingTaskCandidateError as error:
        _raise_candidate_error(error)
    return {"candidate": _serialize_candidate(candidate)}


@scheduled_job_candidates.post("/{candidate_id}/enable")
async def enable_scheduled_job_candidate(
    candidate_id: str,
    payload: CandidateEnableRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        async with db.begin():
            job = await IncomingTaskCandidateService(db).enable_candidate(
                candidate_id=candidate_id,
                actor_uid=current_user.uid,
                version=payload.version,
            )
    except IncomingTaskCandidateError as error:
        _raise_candidate_error(error)
    if job is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="candidate_validation_failed")
    return {"scheduled_job_id": job.id}


@scheduled_job_candidates.post("/{candidate_id}/reject")
async def reject_scheduled_job_candidate(
    candidate_id: str,
    payload: CandidateRejectRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        async with db.begin():
            candidate = await IncomingTaskCandidateService(db).reject_candidate(
                candidate_id=candidate_id,
                actor_uid=current_user.uid,
                version=payload.version,
                reason=payload.reason,
            )
    except IncomingTaskCandidateError as error:
        _raise_candidate_error(error)
    return {"candidate": _serialize_candidate(candidate)}
