"""统一收件箱 HTTP 边界。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.inbox_service import InboxDomainError, InboxItemNotFoundError, InboxService
from yuxi.storage.postgres.models_business import User

inbox = APIRouter(prefix="/inbox", tags=["inbox"])


class ReadAllRequest(BaseModel):
    category: Literal["notification", "task"]


def _raise_inbox_error(error: InboxDomainError) -> None:
    if isinstance(error, InboxItemNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@inbox.get("/notifications")
async def list_notifications(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
):
    try:
        return await InboxService(db).list_notifications(recipient_uid=current_user.uid, cursor=cursor, limit=limit)
    except InboxDomainError as error:
        _raise_inbox_error(error)


@inbox.get("/tasks")
async def list_tasks(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
):
    try:
        return await InboxService(db).list_tasks(owner_uid=current_user.uid, cursor=cursor, limit=limit)
    except InboxDomainError as error:
        _raise_inbox_error(error)


@inbox.get("/unread-count")
async def unread_count(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await InboxService(db).unread_counts(recipient_uid=current_user.uid)


@inbox.post("/notifications/{item_id}/read")
async def mark_notification_read(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        async with db.begin():
            marked_count = await InboxService(db).mark_notification_read(
                item_id=item_id, recipient_uid=current_user.uid
            )
    except InboxDomainError as error:
        _raise_inbox_error(error)
    return {"marked_count": marked_count}


@inbox.post("/tasks/{job_id}/read")
async def mark_task_read(
    job_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        async with db.begin():
            marked_count = await InboxService(db).mark_task_read(job_id=job_id, owner_uid=current_user.uid)
    except InboxDomainError as error:
        _raise_inbox_error(error)
    return {"marked_count": marked_count}


@inbox.post("/read-all")
async def mark_all_read(
    payload: ReadAllRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    async with db.begin():
        marked_count = await InboxService(db).mark_all_read(recipient_uid=current_user.uid, category=payload.category)
    return {"marked_count": marked_count}
