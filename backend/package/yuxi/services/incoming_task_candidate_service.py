"""来文抽取任务候选的构建、复验和启用用例。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.document_extraction.schemas import TaskItem
from yuxi.scheduled_jobs.ids import new_scheduled_job_id
from yuxi.scheduled_jobs.schemas import DEFAULT_TIMEZONE, NotificationAction, Schedule
from yuxi.scheduled_jobs.timing import next_run_at
from yuxi.services.personnel_directory_service import LocalUserPersonnelDirectory
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_knowledge import (
    DocumentBusinessExtractionItem,
    DocumentBusinessExtractionResult,
    DocumentBusinessExtractionRun,
    IncomingDocument,
)
from yuxi.storage.postgres.models_scheduled_jobs import (
    IncomingTaskBatch,
    ScheduledJob,
    ScheduledJobAuditLog,
    ScheduledJobCandidate,
    ScheduledJobRecipient,
)

_SCHEDULE_ADAPTER = TypeAdapter(Schedule)
_PROCESSABLE_BATCH_STATUSES = frozenset({"ready", "frozen"})


class IncomingTaskCandidateError(ValueError):
    """候选操作的预期领域错误，调用方负责映射为稳定 HTTP 错误。"""


class IncomingTaskBatchFrozenError(IncomingTaskCandidateError):
    """冻结批次不能替换抽取运行或重建候选成员。"""


class CandidateVersionConflictError(IncomingTaskCandidateError):
    """候选已被其他操作更新，客户端必须重新读取后再提交。"""


@dataclass(frozen=True, slots=True)
class CandidateValidation:
    """复验产物同时供持久化错误和启用时的 UID 快照使用。"""

    errors: list[dict[str, str]]
    warnings: list[dict[str, str]]
    recipient_resolution: dict[str, Any]
    recipients: list[User]
    schedule: Any | None
    timezone: str | None


@dataclass(frozen=True, slots=True)
class IncomingConfirmationResult:
    """来文确认的批量处理摘要，预期校验失败不影响确认提交。"""

    enabled_job_ids: tuple[str, ...]
    skipped_count: int
    invalid_candidate_ids: tuple[str, ...]

    @property
    def enabled_count(self) -> int:
        return len(self.enabled_job_ids)

    @property
    def invalid_count(self) -> int:
        return len(self.invalid_candidate_ids)


class IncomingTaskCandidateService:
    """所有跨表写入均复用调用方会话，防止来文确认出现半成品任务。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.directory = LocalUserPersonnelDirectory(db_session)

    async def ensure_batch_rebuildable(self, *, incoming_id: str) -> None:
        """在模型调用前检查冻结状态，避免无效重处理覆盖已启用任务的来源批次。"""
        batch = await self.db.scalar(
            select(IncomingTaskBatch).where(IncomingTaskBatch.incoming_id == incoming_id).with_for_update()
        )
        if batch is not None and batch.status == "frozen":
            raise IncomingTaskBatchFrozenError("来文任务批次已冻结，不能重新处理")

    async def build_batch_from_extraction(
        self,
        *,
        incoming_id: str,
        extraction_run_id: str,
        document_updates: dict[str, Any] | None = None,
    ) -> IncomingTaskBatch:
        """用指定抽取运行重建未冻结批次；绝不回退到“最新来文运行”。"""
        document = await self._lock_document(incoming_id)
        batch = await self._lock_or_create_batch(incoming_id)
        if batch.status == "frozen":
            raise IncomingTaskBatchFrozenError("来文任务批次已冻结，不能重新构建候选")

        run, result = await self._load_extraction_run(incoming_id=incoming_id, extraction_run_id=extraction_run_id)
        owner = await self._active_owner(document.created_by)
        batch.status = "building"
        batch.extraction_run_id = extraction_run_id
        batch.build_error_code = None
        batch.build_error_message = None
        document.status = "extracting"
        await self.db.flush()

        await self.db.execute(
            select(ScheduledJobCandidate)
            .where(ScheduledJobCandidate.batch_id == batch.id, ScheduledJobCandidate.status == "pending_confirmation")
            .with_for_update()
        )
        await self.db.execute(
            ScheduledJobCandidate.__table__.update()
            .where(
                ScheduledJobCandidate.batch_id == batch.id,
                ScheduledJobCandidate.status == "pending_confirmation",
            )
            .values(status="stale")
        )

        item_rows = await self.db.scalars(
            select(DocumentBusinessExtractionItem)
            .where(
                DocumentBusinessExtractionItem.result_id == result.id,
                DocumentBusinessExtractionItem.item_type == "task_item",
            )
            .order_by(DocumentBusinessExtractionItem.id.asc())
        )
        candidates: list[ScheduledJobCandidate] = []
        now = await self._database_now()
        for item in item_rows.all():
            candidate = await self._candidate_from_extraction_item(
                item=item,
                batch=batch,
                owner=owner,
                validation_now=now,
            )
            self.db.add(candidate)
            candidates.append(candidate)

        batch.candidate_count = len(candidates)
        batch.status = "ready"
        document.status = "ready"
        for field, value in (document_updates or {}).items():
            setattr(document, field, value)
        await self.db.flush()
        return batch

    async def mark_build_failed(self, *, incoming_id: str, message: str) -> None:
        """候选持久化失败必须显式结束 building，供重试入口识别为可恢复失败。"""
        document = await self._lock_document(incoming_id)
        batch = await self._lock_or_create_batch(incoming_id)
        if batch.status == "frozen":
            return
        await self._mark_batch_failed(
            batch=batch,
            document=document,
            code="candidate_build_failed",
            message=message[:4000],
        )

    async def enable_candidate(
        self, *, candidate_id: str, actor_uid: str, version: int | None = None
    ) -> ScheduledJob | None:
        """人工启用一个候选；首个启用会冻结批次，重复请求返回已有任务。"""
        candidate = await self._lock_candidate_after_batch(candidate_id)
        batch = await self._lock_batch(candidate.batch_id)
        # 先锁批次再重新锁候选，遵循来文批量确认的统一锁顺序。
        candidate = await self._lock_candidate(candidate.id)
        self._require_version(candidate=candidate, version=version)
        actor = await self._require_candidate_operator(actor_uid)
        return await self._enable_locked_candidate(candidate=candidate, batch=batch, actor=actor)

    async def update_candidate(
        self,
        *,
        candidate_id: str,
        actor_uid: str,
        version: int,
        name: str | None = None,
        notification_title: str | None = None,
        notification_content: str | None = None,
        schedule_data: dict[str, Any] | None = None,
        timezone: str | None = None,
        recipient_scope: str | None = None,
        recipient_names: list[str] | None = None,
    ) -> ScheduledJobCandidate:
        """只编辑草稿字段；状态迁移仍由专用启用和拒绝入口处理。"""
        candidate = await self._lock_candidate_after_batch(candidate_id)
        batch = await self._lock_batch(candidate.batch_id)
        candidate = await self._lock_candidate(candidate.id)
        self._require_version(candidate=candidate, version=version)
        await self._require_candidate_operator(actor_uid)
        if candidate.status != "pending_confirmation" or batch.status not in _PROCESSABLE_BATCH_STATUSES:
            raise IncomingTaskCandidateError("当前候选不能编辑")

        if name is not None:
            candidate.name = name
        if notification_title is not None:
            candidate.notification_title = notification_title
        if notification_content is not None:
            candidate.notification_content = notification_content
        if schedule_data is not None:
            candidate.schedule_data = schedule_data
        if timezone is not None:
            candidate.timezone = timezone
        if recipient_scope is not None:
            candidate.recipient_scope = recipient_scope
        if recipient_names is not None:
            candidate.raw_recipient_names = recipient_names

        validation = await self.validate_candidate(candidate=candidate)
        candidate.recipient_resolution = validation.recipient_resolution
        candidate.resolved_recipient_uids = [user.uid for user in validation.recipients]
        candidate.validation_errors = validation.errors
        candidate.validation_warnings = validation.warnings
        candidate.version += 1
        self._audit(
            actor_uid=actor_uid,
            action="candidate_updated",
            candidate_id=candidate.id,
            after_data={"version": candidate.version},
        )
        await self.db.flush()
        return candidate

    async def reject_candidate(
        self, *, candidate_id: str, actor_uid: str, version: int, reason: str
    ) -> ScheduledJobCandidate:
        """拒绝保留候选和审计，避免丢失抽取事实及人工判断依据。"""
        candidate = await self._lock_candidate_after_batch(candidate_id)
        await self._lock_batch(candidate.batch_id)
        candidate = await self._lock_candidate(candidate.id)
        self._require_version(candidate=candidate, version=version)
        actor = await self._require_candidate_operator(actor_uid)
        if candidate.status != "pending_confirmation":
            raise IncomingTaskCandidateError("当前候选不能拒绝")
        candidate.status = "rejected"
        candidate.rejected_at = await self._database_now()
        candidate.rejected_by_uid = actor.uid
        candidate.version += 1
        self._audit(
            actor_uid=actor.uid,
            action="candidate_rejected",
            candidate_id=candidate.id,
            after_data={"status": candidate.status, "version": candidate.version},
            reason=reason,
        )
        await self.db.flush()
        return candidate

    async def confirm_incoming(self, *, incoming_id: str, actor_uid: str) -> IncomingConfirmationResult:
        """确认来文并批量启用有效候选；领域校验错误按候选提交而非回滚整体。"""
        document = await self._lock_document(incoming_id)
        if document.status != "ready":
            raise IncomingTaskCandidateError("来文尚未处理完成，不能确认")
        actor = await self._require_candidate_operator(actor_uid)
        batch = await self.db.scalar(
            select(IncomingTaskBatch).where(IncomingTaskBatch.incoming_id == incoming_id).with_for_update()
        )
        if batch is None or batch.status not in _PROCESSABLE_BATCH_STATUSES:
            raise IncomingTaskCandidateError("来文任务批次尚未就绪，不能确认")

        if batch.status != "frozen":
            await self._freeze_batch(batch=batch, actor_uid=actor.uid)
        candidates = list(
            (
                await self.db.scalars(
                    select(ScheduledJobCandidate)
                    .where(ScheduledJobCandidate.batch_id == batch.id)
                    .order_by(ScheduledJobCandidate.id.asc())
                    .with_for_update()
                )
            ).all()
        )
        enabled_job_ids: list[str] = []
        invalid_candidate_ids: list[str] = []
        skipped_count = 0
        for candidate in candidates:
            if candidate.status in {"enabled", "rejected", "stale"}:
                skipped_count += 1
                continue
            job = await self._enable_locked_candidate(candidate=candidate, batch=batch, actor=actor)
            if job is None:
                invalid_candidate_ids.append(candidate.id)
            else:
                enabled_job_ids.append(job.id)

        document.review_status = "confirmed"
        document.confirmed_by = actor.uid
        document.confirmed_at = await self._database_now()
        await self.db.flush()
        return IncomingConfirmationResult(
            enabled_job_ids=tuple(enabled_job_ids),
            skipped_count=skipped_count,
            invalid_candidate_ids=tuple(invalid_candidate_ids),
        )

    async def validate_candidate(
        self, *, candidate: ScheduledJobCandidate, validation_now: datetime | None = None
    ) -> CandidateValidation:
        """候选构建、编辑和启用复用此校验，避免前端与服务端规则漂移。"""
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        now = validation_now or await self._database_now()
        title = (candidate.notification_title or "").strip()
        content = (candidate.notification_content or "").strip()
        evidence = candidate.evidence or {}
        if not title:
            errors.append(self._error("action.title", "required", "通知标题不能为空"))
        if not content:
            errors.append(self._error("action.content", "required", "通知正文不能为空"))
        if not str(evidence.get("source_quote") or "").strip():
            errors.append(self._error("evidence.source_quote", "source_evidence_missing", "缺少任务原文证据"))
        if not str(evidence.get("source_file_id") or "").strip():
            errors.append(self._error("evidence.source_file_id", "source_evidence_missing", "缺少任务来源文件"))

        schedule = None
        timezone = (candidate.timezone or "").strip()
        if not candidate.schedule_data:
            errors.append(self._error("schedule", "required", "缺少可执行的调度规则"))
        elif not timezone:
            errors.append(self._error("timezone", "required", "缺少任务时区"))
        else:
            try:
                schedule = _SCHEDULE_ADAPTER.validate_python(candidate.schedule_data)
                NotificationAction(title=title, content=content)
                next_at = next_run_at(schedule, timezone, now, inclusive=False)
                if schedule.kind == "at" and next_at <= now:
                    errors.append(self._error("schedule.run_at", "run_at_expired", "一次性触发时间必须晚于当前时间"))
                elif schedule.kind != "at" and next_at <= now:
                    errors.append(self._error("schedule", "no_future_run", "周期规则无法计算未来触发时间"))
                if schedule.kind == "interval" and schedule.interval_seconds < 300:
                    warnings.append(self._warning("schedule", "frequent_schedule", "任务触发间隔小于五分钟"))
            except ValidationError as exc:
                errors.extend(self._schedule_errors(exc))
            except ValueError as exc:
                errors.append(self._error("schedule", "invalid_schedule", str(exc)))

        owner = await self._active_owner(candidate.owner_uid)
        recipients, resolution, recipient_errors = await self._resolve_recipients(candidate=candidate, owner=owner)
        errors.extend(recipient_errors)
        if len(recipients) > 100:
            warnings.append(self._warning("recipients", "large_recipient_group", "接收人超过 100 人"))
        return CandidateValidation(
            errors=errors,
            warnings=warnings,
            recipient_resolution=resolution,
            recipients=recipients,
            schedule=schedule,
            timezone=timezone or None,
        )

    async def _candidate_from_extraction_item(
        self,
        *,
        item: DocumentBusinessExtractionItem,
        batch: IncomingTaskBatch,
        owner: User | None,
        validation_now: datetime,
    ) -> ScheduledJobCandidate:
        data = item.data or {}
        try:
            draft = TaskItem.model_validate(data)
        except ValidationError as exc:
            # 已持久化的结构化抽取条目理论上受 schema 保护；仍将兼容演进问题显式保留为候选错误。
            task_name = str(data.get("task_name") or "待补全任务")[:100]
            draft_data: dict[str, Any] = {"recipient_scope": "unknown", "recipient_names": []}
            parse_errors = [self._error("extraction", "invalid_schedule", str(exc))]
        else:
            task_name = draft.task_name
            draft_data = draft.model_dump(mode="json")
            parse_errors = []

        candidate = ScheduledJobCandidate(
            id=new_scheduled_job_id("sjc_"),
            batch_id=batch.id,
            extraction_item_id=item.item_id,
            incoming_id=batch.incoming_id,
            extraction_run_id=batch.extraction_run_id,
            owner_uid=owner.uid if owner is not None else None,
            name=task_name,
            notification_title=draft_data.get("notification_title"),
            notification_content=draft_data.get("notification_content"),
            schedule_data=draft_data.get("schedule"),
            timezone=draft_data.get("timezone") or DEFAULT_TIMEZONE,
            recipient_scope=draft_data.get("recipient_scope") or "unknown",
            raw_recipient_names=list(draft_data.get("recipient_names") or []),
            evidence={
                "source_quote": draft_data.get("source_quote"),
                "source_file_id": draft_data.get("source_file_id") or item.file_id,
                "source_location": draft_data.get("source_location"),
                "extraction_evidence": item.evidence or [],
                "raw_time_expression": draft_data.get("raw_time_expression") or draft_data.get("deadline"),
                "raw_recipient_expression": draft_data.get("raw_recipient_expression")
                or draft_data.get("recipient_expression"),
            },
            status="pending_confirmation",
        )
        validation = await self.validate_candidate(candidate=candidate, validation_now=validation_now)
        candidate.recipient_resolution = validation.recipient_resolution
        candidate.resolved_recipient_uids = [user.uid for user in validation.recipients]
        candidate.validation_errors = parse_errors + validation.errors
        candidate.validation_warnings = validation.warnings
        return candidate

    async def _enable_locked_candidate(
        self,
        *,
        candidate: ScheduledJobCandidate,
        batch: IncomingTaskBatch,
        actor: User,
    ) -> ScheduledJob | None:
        existing = await self.db.scalar(select(ScheduledJob).where(ScheduledJob.source_candidate_id == candidate.id))
        if existing is not None:
            if candidate.status != "enabled":
                candidate.status = "enabled"
                candidate.enabled_by_uid = actor.uid
            return existing
        if candidate.status != "pending_confirmation":
            return None
        if batch.status not in _PROCESSABLE_BATCH_STATUSES:
            raise IncomingTaskCandidateError("来文任务批次尚未就绪，不能启用候选")
        if batch.status != "frozen":
            await self._freeze_batch(batch=batch, actor_uid=actor.uid)

        now = await self._database_now()
        validation = await self.validate_candidate(candidate=candidate, validation_now=now)
        candidate.recipient_resolution = validation.recipient_resolution
        candidate.resolved_recipient_uids = [user.uid for user in validation.recipients]
        candidate.validation_errors = validation.errors
        candidate.validation_warnings = validation.warnings
        if validation.errors or validation.schedule is None or validation.timezone is None:
            await self.db.flush()
            return None

        document = await self.db.scalar(
            select(IncomingDocument).where(IncomingDocument.incoming_id == candidate.incoming_id)
        )
        if document is None:
            raise IncomingTaskCandidateError("候选来源来文不存在")
        source_snapshot = self._source_snapshot(document=document, candidate=candidate)
        job = ScheduledJob(
            id=new_scheduled_job_id("sj_"),
            # 上传者只是来源操作者，来文任务由管理员管理，不能借 owner_uid 进入个人任务接口。
            owner_uid=None,
            source_type="incoming",
            source_candidate_id=candidate.id,
            source_snapshot=source_snapshot,
            name=candidate.name,
            schedule_kind=validation.schedule.kind,
            run_at=getattr(validation.schedule, "run_at", None),
            anchor_at=getattr(validation.schedule, "anchor_at", None),
            interval_seconds=getattr(validation.schedule, "interval_seconds", None),
            cron_expression=getattr(validation.schedule, "cron_expression", None),
            timezone=validation.timezone,
            next_run_at=next_run_at(validation.schedule, validation.timezone, now, inclusive=False),
            action_type="notification",
            action_data={
                "type": "notification",
                "title": candidate.notification_title,
                "content": candidate.notification_content,
            },
            status="active",
            created_by_uid=actor.uid,
        )
        self.db.add(job)
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(ScheduledJob).where(ScheduledJob.source_candidate_id == candidate.id)
            )
            if existing is None:
                raise
            candidate.status = "enabled"
            candidate.enabled_by_uid = actor.uid
            return existing

        for recipient in validation.recipients:
            self.db.add(
                ScheduledJobRecipient(
                    scheduled_job_id=job.id,
                    recipient_uid=recipient.uid,
                    recipient_name_snapshot=recipient.username,
                )
            )
        candidate.status = "enabled"
        candidate.enabled_at = now
        candidate.enabled_by_uid = actor.uid
        candidate.version += 1
        self._audit(
            actor_uid=actor.uid,
            action="candidate_enabled",
            candidate_id=candidate.id,
            after_data={"scheduled_job_id": job.id, "status": candidate.status},
        )
        self._audit(
            actor_uid=actor.uid,
            action="created",
            job_id=job.id,
            after_data={"status": job.status, "source_candidate_id": candidate.id},
        )
        await self.db.flush()
        return job

    async def _resolve_recipients(
        self, *, candidate: ScheduledJobCandidate, owner: User | None
    ) -> tuple[list[User], dict[str, Any], list[dict[str, str]]]:
        if owner is None:
            return [], {}, [self._error("owner", "recipient_forbidden", "来文上传账号不存在或已删除")]
        if candidate.recipient_scope == "unknown":
            return [], {"scope": "unknown"}, [self._error("recipients", "recipient_missing", "未能确定接收人范围")]
        if candidate.recipient_scope == "all":
            users = await self.directory.list_active_users(operator=owner)
            resolution = {"scope": "all", "users": [self._user_summary(user) for user in users]}
            if not users:
                return [], resolution, [self._error("recipients", "empty_recipient_scope", "全体人员范围为空")]
            return users, resolution, []

        names = list(dict.fromkeys(name.strip() for name in (candidate.raw_recipient_names or []) if name.strip()))
        if not names:
            return (
                [],
                {"scope": "named", "names": []},
                [self._error("recipients", "recipient_missing", "缺少接收人姓名")],
            )
        recipients: list[User] = []
        details: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for name in names:
            resolution = await self.directory.find_by_name(name=name, operator=owner)
            details.append(
                {
                    "name": name,
                    "status": resolution.status,
                    "users": [self._user_summary(user) for user in resolution.users],
                }
            )
            if resolution.status == "resolved" and resolution.users:
                recipients.append(resolution.users[0])
            elif resolution.status == "ambiguous":
                errors.append(self._error("recipients", "recipient_ambiguous", f"接收人“{name}”存在多个匹配"))
            else:
                errors.append(self._error("recipients", "recipient_not_found", f"未找到接收人“{name}”"))
        unique = {user.uid: user for user in recipients}
        return list(unique.values()), {"scope": "named", "names": details}, errors

    async def _load_extraction_run(
        self, *, incoming_id: str, extraction_run_id: str
    ) -> tuple[DocumentBusinessExtractionRun, DocumentBusinessExtractionResult]:
        pair = (
            await self.db.execute(
                select(DocumentBusinessExtractionRun, DocumentBusinessExtractionResult)
                .join(
                    DocumentBusinessExtractionResult,
                    DocumentBusinessExtractionResult.run_id == DocumentBusinessExtractionRun.run_id,
                )
                .where(
                    DocumentBusinessExtractionRun.run_id == extraction_run_id,
                    DocumentBusinessExtractionRun.document_scope == "incoming",
                    DocumentBusinessExtractionRun.incoming_id == incoming_id,
                    DocumentBusinessExtractionRun.status == "success",
                    DocumentBusinessExtractionResult.incoming_id == incoming_id,
                )
            )
        ).one_or_none()
        if pair is None:
            raise IncomingTaskCandidateError("指定来文抽取运行不存在或尚未成功")
        return pair

    async def _lock_document(self, incoming_id: str) -> IncomingDocument:
        document = await self.db.scalar(
            select(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id).with_for_update()
        )
        if document is None:
            raise IncomingTaskCandidateError("来文不存在")
        return document

    async def _lock_or_create_batch(self, incoming_id: str) -> IncomingTaskBatch:
        batch = await self.db.scalar(
            select(IncomingTaskBatch).where(IncomingTaskBatch.incoming_id == incoming_id).with_for_update()
        )
        if batch is not None:
            return batch
        batch = IncomingTaskBatch(id=new_scheduled_job_id("sjb_"), incoming_id=incoming_id, status="building")
        self.db.add(batch)
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            batch = await self.db.scalar(
                select(IncomingTaskBatch).where(IncomingTaskBatch.incoming_id == incoming_id).with_for_update()
            )
            if batch is None:
                raise
        return batch

    async def _lock_batch(self, batch_id: str) -> IncomingTaskBatch:
        batch = await self.db.scalar(
            select(IncomingTaskBatch).where(IncomingTaskBatch.id == batch_id).with_for_update()
        )
        if batch is None:
            raise IncomingTaskCandidateError("候选批次不存在")
        return batch

    async def _lock_candidate_after_batch(self, candidate_id: str) -> ScheduledJobCandidate:
        candidate = await self.db.scalar(select(ScheduledJobCandidate).where(ScheduledJobCandidate.id == candidate_id))
        if candidate is None:
            raise IncomingTaskCandidateError("候选不存在")
        return candidate

    async def _lock_candidate(self, candidate_id: str) -> ScheduledJobCandidate:
        candidate = await self.db.scalar(
            select(ScheduledJobCandidate).where(ScheduledJobCandidate.id == candidate_id).with_for_update()
        )
        if candidate is None:
            raise IncomingTaskCandidateError("候选不存在")
        return candidate

    async def _freeze_batch(self, *, batch: IncomingTaskBatch, actor_uid: str) -> None:
        if batch.status == "frozen":
            return
        if batch.status != "ready":
            raise IncomingTaskCandidateError("来文任务批次尚未就绪，不能冻结")
        batch.status = "frozen"
        batch.frozen_at = await self._database_now()
        batch.frozen_by_uid = actor_uid

    @staticmethod
    def _require_version(*, candidate: ScheduledJobCandidate, version: int | None) -> None:
        if version is not None and candidate.version != version:
            raise CandidateVersionConflictError("候选已被其他操作更新")

    async def _mark_batch_failed(
        self, *, batch: IncomingTaskBatch, document: IncomingDocument, code: str, message: str
    ) -> None:
        batch.status = "failed"
        batch.candidate_count = 0
        batch.build_error_code = code
        batch.build_error_message = message
        document.status = "failed"
        document.processing_error = message
        await self.db.flush()

    async def _active_owner(self, uid: str | None) -> User | None:
        if not uid:
            return None
        return await self.db.scalar(select(User).where(User.uid == uid, User.is_deleted == 0))

    async def _require_candidate_operator(self, uid: str) -> User:
        user = await self._active_owner(uid)
        if user is None or user.role not in {"admin", "superadmin"}:
            raise IncomingTaskCandidateError("当前用户无权处理来文任务候选")
        return user

    async def _database_now(self) -> datetime:
        now = await self.db.scalar(select(func.now()))
        if now is None:
            raise RuntimeError("无法读取 PostgreSQL 当前时间")
        return now.astimezone(UTC)

    @staticmethod
    def _source_snapshot(*, document: IncomingDocument, candidate: ScheduledJobCandidate) -> dict[str, Any]:
        metadata = document.document_metadata or {}
        return {
            "incoming_id": document.incoming_id,
            "source_document_id": document.source_document_id,
            "title": metadata.get("title"),
            "document_number": metadata.get("document_number"),
            "incoming_date": metadata.get("incoming_date"),
            "evidence_summary": str((candidate.evidence or {}).get("source_quote") or "")[:1000] or None,
        }

    def _audit(
        self,
        *,
        actor_uid: str,
        action: str,
        job_id: str | None = None,
        candidate_id: str | None = None,
        after_data: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        self.db.add(
            ScheduledJobAuditLog(
                id=new_scheduled_job_id("sja_"),
                scheduled_job_id=job_id,
                candidate_id=candidate_id,
                actor_uid=actor_uid,
                action=action,
                after_data=after_data,
                reason=reason,
            )
        )

    @staticmethod
    def _user_summary(user: User) -> dict[str, str]:
        return {"uid": user.uid, "name": user.username}

    @staticmethod
    def _error(field: str, code: str, message: str) -> dict[str, str]:
        return {"field": field, "code": code, "message": message}

    @staticmethod
    def _warning(field: str, code: str, message: str) -> dict[str, str]:
        return {"field": field, "code": code, "message": message}

    def _schedule_errors(self, error: ValidationError) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        for item in error.errors():
            location = ".".join(str(value) for value in item["loc"] if value not in {"at", "interval", "cron"})
            message = str(item["msg"])
            if "timezone" in message:
                code = "invalid_timezone"
            elif "interval_seconds" in location and ("greater than or equal" in message or "multiple" in message):
                code = "interval_too_short"
            else:
                code = "invalid_schedule"
            errors.append(self._error(f"schedule.{location}".rstrip("."), code, message))
        return errors
