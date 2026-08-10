"""收件箱查询与已读用例。"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.inbox_repository import InboxRepository
from yuxi.storage.postgres.models_scheduled_jobs import InboxItem, ScheduledJob, ScheduledJobRun


class InboxDomainError(ValueError):
    pass


class InboxItemNotFoundError(InboxDomainError):
    pass


class InboxService:
    """服务层固定使用认证用户 UID，调用方负责事务边界。"""

    def __init__(self, db_session: AsyncSession):
        self.repository = InboxRepository(db_session)

    async def list_notifications(self, *, recipient_uid: str, cursor: str | None, limit: int) -> dict:
        rows = await self.repository.list_notifications(
            recipient_uid=recipient_uid,
            cursor=self._decode_notification_cursor(cursor) if cursor else None,
            limit=limit,
        )
        items = rows[:limit]
        return {
            "items": [self._serialize_notification(item) for item in items],
            "next_cursor": self._encode_notification_cursor(items[-1]) if len(rows) > limit else None,
        }

    async def list_tasks(self, *, owner_uid: str, cursor: str | None, limit: int) -> dict:
        rows = await self.repository.list_tasks(
            owner_uid=owner_uid,
            cursor=self._decode_task_cursor(cursor) if cursor else None,
            limit=limit,
        )
        items = rows[:limit]
        job_ids = [row[0].id for row in items]
        latest_runs, latest_unread_runs, unread_run_counts = await self.repository.task_run_summaries(
            job_ids=job_ids, owner_uid=owner_uid
        )
        return {
            "items": [
                self._serialize_task(
                    row,
                    latest_run=latest_runs.get(row[0].id),
                    latest_unread_run=latest_unread_runs.get(row[0].id),
                    unread_run_count=unread_run_counts.get(row[0].id, 0),
                )
                for row in items
            ],
            "next_cursor": self._encode_task_cursor(items[-1]) if len(rows) > limit else None,
        }

    async def unread_counts(self, *, recipient_uid: str) -> dict:
        notification_count, task_count = await self.repository.unread_counts(recipient_uid=recipient_uid)
        return {
            "notification_unread_count": notification_count,
            "task_unread_count": task_count,
            "total_unread_count": notification_count + task_count,
        }

    async def mark_notification_read(self, *, item_id: str, recipient_uid: str) -> int:
        if not await self.repository.notification_exists(item_id=item_id, recipient_uid=recipient_uid):
            raise InboxItemNotFoundError("notification_not_found")
        return await self.repository.mark_notification_read(item_id=item_id, recipient_uid=recipient_uid)

    async def mark_task_read(self, *, job_id: str, owner_uid: str) -> int:
        if not await self.repository.task_exists_for_owner(job_id=job_id, owner_uid=owner_uid):
            raise InboxItemNotFoundError("scheduled_job_not_found")
        return await self.repository.mark_task_read(job_id=job_id, owner_uid=owner_uid)

    async def mark_task_run_read(self, *, job_id: str, run_id: str, owner_uid: str) -> int:
        if not await self.repository.task_run_exists_for_owner(job_id=job_id, run_id=run_id, owner_uid=owner_uid):
            raise InboxItemNotFoundError("scheduled_job_run_not_found")
        return await self.repository.mark_task_run_read(job_id=job_id, run_id=run_id, owner_uid=owner_uid)

    async def mark_all_read(self, *, recipient_uid: str, category: Literal["notification", "task"]) -> int:
        return await self.repository.mark_all_read(recipient_uid=recipient_uid, category=category)

    @staticmethod
    def _serialize_notification(item: InboxItem) -> dict:
        return {
            "id": item.id,
            "scheduled_job_id": item.scheduled_job_id,
            "scheduled_job_run_id": item.scheduled_job_run_id,
            "item_type": item.item_type,
            "title": item.title,
            "content": item.content_snapshot,
            "is_read": item.is_read,
            "read_at": InboxService._format_datetime(item.read_at),
            "created_at": InboxService._format_datetime(item.created_at),
        }

    @staticmethod
    def _serialize_task(
        row: tuple,
        *,
        latest_run: ScheduledJobRun | None,
        latest_unread_run: ScheduledJobRun | None,
        unread_run_count: int,
    ) -> dict:
        job: ScheduledJob = row[0]
        latest_title, latest_content, latest_update_at, unread_count, _has_unread, sort_at = row[1:]
        action_data = job.action_data if isinstance(job.action_data, dict) else {}
        return {
            "job": {
                "id": job.id,
                "name": job.name,
                "source_type": job.source_type,
                "source_snapshot": job.source_snapshot,
                "schedule_kind": job.schedule_kind,
                "run_at": InboxService._format_datetime(job.run_at),
                "anchor_at": InboxService._format_datetime(job.anchor_at),
                "interval_seconds": job.interval_seconds,
                "cron_expression": job.cron_expression,
                "timezone": job.timezone,
                "action_type": job.action_type,
                "agent_slug": action_data.get("agent_slug"),
                "next_run_at": InboxService._format_datetime(job.next_run_at),
                "status": job.status,
                "updated_at": InboxService._format_datetime(job.updated_at),
            },
            "latest_update": (
                {
                    "title": latest_title,
                    "content": latest_content,
                    "created_at": InboxService._format_datetime(latest_update_at),
                }
                if latest_update_at is not None
                else None
            ),
            "unread_update_count": int(unread_count),
            "unread_run_count": unread_run_count,
            "latest_run": InboxService._serialize_run(latest_run),
            "latest_unread_run": InboxService._serialize_run(latest_unread_run),
            "sort_at": InboxService._format_datetime(sort_at),
        }

    @staticmethod
    def _serialize_run(run: ScheduledJobRun | None) -> dict | None:
        if run is None:
            return None
        result_data = run.result_data if isinstance(run.result_data, dict) else {}
        return {
            "id": run.id,
            "status": run.status,
            "scheduled_for": InboxService._format_datetime(run.scheduled_for),
            "started_at": InboxService._format_datetime(run.started_at),
            "finished_at": InboxService._format_datetime(run.finished_at),
            "conversation_thread_id": run.conversation_thread_id,
            "final_message_id": result_data.get("final_message_id"),
            "result_preview": result_data.get("result_preview"),
            "artifact_count": int(result_data.get("artifact_count") or 0),
            "error_code": run.error_code,
            "error_message": run.error_message,
        }

    @staticmethod
    def _format_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @classmethod
    def _decode_notification_cursor(cls, cursor: str) -> tuple[bool, datetime, str]:
        payload = cls._decode_cursor(cursor, "notification")
        is_read = payload.get("is_read")
        if not isinstance(is_read, bool):
            raise InboxDomainError("cursor 无效")
        return is_read, cls._cursor_datetime(payload), cls._cursor_id(payload)

    @classmethod
    def _decode_task_cursor(cls, cursor: str) -> tuple[int, datetime, str]:
        payload = cls._decode_cursor(cursor, "task")
        has_unread = payload.get("has_unread")
        if has_unread not in {0, 1}:
            raise InboxDomainError("cursor 无效")
        return has_unread, cls._cursor_datetime(payload), cls._cursor_id(payload)

    @staticmethod
    def _decode_cursor(cursor: str, category: str) -> dict:
        try:
            payload = json.loads(urlsafe_b64decode(cursor.encode()).decode())
        except (BinasciiError, TypeError, UnicodeDecodeError, ValueError) as error:
            raise InboxDomainError("cursor 无效") from error
        if not isinstance(payload, dict) or payload.get("category") != category:
            raise InboxDomainError("cursor 无效")
        return payload

    @staticmethod
    def _cursor_datetime(payload: dict) -> datetime:
        try:
            value = datetime.fromisoformat(payload["sort_at"])
        except (KeyError, TypeError, ValueError) as error:
            raise InboxDomainError("cursor 无效") from error
        if value.tzinfo is None or value.utcoffset() is None:
            raise InboxDomainError("cursor 无效")
        return value.astimezone(UTC)

    @staticmethod
    def _cursor_id(payload: dict) -> str:
        item_id = payload.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise InboxDomainError("cursor 无效")
        return item_id

    @staticmethod
    def _encode_notification_cursor(item: InboxItem) -> str:
        return InboxService._encode_cursor(
            {"category": "notification", "is_read": item.is_read, "sort_at": item.created_at.isoformat(), "id": item.id}
        )

    @staticmethod
    def _encode_task_cursor(row: tuple) -> str:
        job: ScheduledJob = row[0]
        has_unread, sort_at = int(row[5]), row[6]
        return InboxService._encode_cursor(
            {"category": "task", "has_unread": has_unread, "sort_at": sort_at.isoformat(), "id": job.id}
        )

    @staticmethod
    def _encode_cursor(payload: dict) -> str:
        return urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
