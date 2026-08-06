"""定时任务第一版的 PostgreSQL 业务实体。"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


class IncomingTaskBatch(Base):
    """固定来文候选集合，避免未冻结重处理覆盖已启用任务。"""

    __tablename__ = "incoming_task_batches"

    id = Column(String(64), primary_key=True)
    incoming_id = Column(String(64), ForeignKey("incoming_documents.incoming_id", ondelete="RESTRICT"), nullable=False)
    extraction_run_id = Column(String(64))
    status = Column(String(16), nullable=False, default="building")
    candidate_count = Column(Integer, nullable=False, default=0)
    build_error_code = Column(String(128))
    build_error_message = Column(Text)
    version = Column(Integer, nullable=False, default=1)
    frozen_at = Column(DateTime(timezone=True))
    frozen_by_uid = Column(String(64), ForeignKey("users.uid", ondelete="RESTRICT"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint("status IN ('building', 'ready', 'failed', 'frozen')", name="ck_incoming_task_batches_status"),
        CheckConstraint("candidate_count >= 0", name="ck_incoming_task_batches_candidate_count"),
        Index("uq_incoming_task_batches_incoming_id", "incoming_id", unique=True),
    )


class ScheduledJobCandidate(Base):
    """来文候选与最终调度任务分离，候选永远先接受人工确认。"""

    __tablename__ = "scheduled_job_candidates"

    id = Column(String(64), primary_key=True)
    batch_id = Column(String(64), ForeignKey("incoming_task_batches.id", ondelete="RESTRICT"), nullable=False)
    extraction_item_id = Column(String(128), nullable=False)
    incoming_id = Column(String(64), ForeignKey("incoming_documents.incoming_id", ondelete="RESTRICT"), nullable=False)
    extraction_run_id = Column(String(64), nullable=False)
    owner_uid = Column(String(64), ForeignKey("users.uid", ondelete="RESTRICT"), nullable=False)
    name = Column(String(100), nullable=False)
    # 抽取不完整时仍要保留候选供人工补全，不能因通知字段缺失而丢弃来源事实。
    notification_title = Column(String(100))
    notification_content = Column(Text)
    schedule_data = Column(JSON_VALUE, nullable=True)
    timezone = Column(String(64), nullable=True)
    recipient_scope = Column(String(16), nullable=False)
    raw_recipient_names = Column(JSON_VALUE, nullable=False, default=list)
    recipient_resolution = Column(JSON_VALUE, nullable=False, default=dict)
    resolved_recipient_uids = Column(JSON_VALUE, nullable=False, default=list)
    evidence = Column(JSON_VALUE, nullable=False, default=dict)
    validation_errors = Column(JSON_VALUE, nullable=False, default=list)
    validation_warnings = Column(JSON_VALUE, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="pending_confirmation")
    enabled_at = Column(DateTime(timezone=True))
    enabled_by_uid = Column(String(64), ForeignKey("users.uid", ondelete="RESTRICT"))
    rejected_at = Column(DateTime(timezone=True))
    rejected_by_uid = Column(String(64), ForeignKey("users.uid", ondelete="RESTRICT"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint("recipient_scope IN ('named', 'all', 'unknown')", name="ck_sjc_recipient_scope"),
        CheckConstraint("status IN ('pending_confirmation', 'enabled', 'rejected', 'stale')", name="ck_sjc_status"),
        Index("uq_sjc_batch_extraction_item", "batch_id", "extraction_item_id", unique=True),
        Index("ix_sjc_status_updated_at", "status", text("updated_at DESC")),
    )


class ScheduledJob(Base):
    """可编辑的调度定义；运行记录和收件箱条目由其派生而非混存。"""

    __tablename__ = "scheduled_jobs"

    id = Column(String(64), primary_key=True)
    owner_uid = Column(String(64), ForeignKey("users.uid", ondelete="RESTRICT"), nullable=False)
    source_type = Column(String(16), nullable=False)
    source_candidate_id = Column(String(64), ForeignKey("scheduled_job_candidates.id", ondelete="RESTRICT"))
    create_request_key = Column(String(128))
    create_request_hash = Column(String(64))
    source_snapshot = Column(JSON_VALUE, nullable=False)
    name = Column(String(100), nullable=False)
    schedule_kind = Column(String(16), nullable=False)
    run_at = Column(DateTime(timezone=True))
    anchor_at = Column(DateTime(timezone=True))
    interval_seconds = Column(Integer)
    cron_expression = Column(String(100))
    timezone = Column(String(64), nullable=False)
    next_run_at = Column(DateTime(timezone=True))
    action_type = Column(String(16), nullable=False)
    action_data = Column(JSON_VALUE, nullable=False)
    status = Column(String(16), nullable=False, default="active")
    version = Column(Integer, nullable=False, default=1)
    last_run_at = Column(DateTime(timezone=True))
    created_by_uid = Column(String(64), ForeignKey("users.uid", ondelete="RESTRICT"), nullable=False)
    paused_at = Column(DateTime(timezone=True))
    cancelled_at = Column(DateTime(timezone=True))
    cancelled_reason = Column(String(128))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint("source_type IN ('incoming', 'personal')", name="ck_sj_source_type"),
        CheckConstraint(
            "(source_type = 'incoming' AND source_candidate_id IS NOT NULL) OR "
            "(source_type = 'personal' AND source_candidate_id IS NULL)",
            name="ck_sj_source_candidate_type",
        ),
        CheckConstraint("schedule_kind IN ('at', 'interval', 'cron')", name="ck_sj_schedule_kind"),
        CheckConstraint("action_type IN ('notification')", name="ck_sj_action_type"),
        CheckConstraint("status IN ('active', 'paused', 'completed', 'cancelled')", name="ck_sj_status"),
        CheckConstraint("version > 0", name="ck_sj_version"),
        CheckConstraint(
            "(create_request_key IS NULL AND create_request_hash IS NULL) OR "
            "(create_request_key IS NOT NULL AND create_request_hash IS NOT NULL)",
            name="ck_sj_request_idempotency_pair",
        ),
        CheckConstraint(
            "(schedule_kind = 'at' AND run_at IS NOT NULL AND anchor_at IS NULL "
            "AND interval_seconds IS NULL AND cron_expression IS NULL) OR "
            "(schedule_kind = 'interval' AND run_at IS NULL AND anchor_at IS NOT NULL "
            "AND interval_seconds >= 60 AND interval_seconds % 60 = 0 AND cron_expression IS NULL) OR "
            "(schedule_kind = 'cron' AND run_at IS NULL AND anchor_at IS NULL "
            "AND interval_seconds IS NULL AND cron_expression IS NOT NULL)",
            name="ck_sj_schedule_fields",
        ),
        Index(
            "uq_sj_source_candidate_id",
            "source_candidate_id",
            unique=True,
            postgresql_where=text("source_candidate_id IS NOT NULL"),
        ),
        Index(
            "uq_sj_owner_create_request_key",
            "owner_uid",
            "create_request_key",
            unique=True,
            postgresql_where=text("create_request_key IS NOT NULL"),
        ),
        Index(
            "ix_sj_active_next_run_at",
            "status",
            "next_run_at",
            "id",
            postgresql_where=text("status = 'active' AND next_run_at IS NOT NULL"),
        ),
        Index("ix_sj_owner_status_updated_at", "owner_uid", "status", text("updated_at DESC")),
    )


class ScheduledJobRecipient(Base):
    """接收人 UID 快照使人员目录变化不会改写既有任务语义。"""

    __tablename__ = "scheduled_job_recipients"

    scheduled_job_id = Column(String(64), ForeignKey("scheduled_jobs.id", ondelete="RESTRICT"), primary_key=True)
    recipient_uid = Column(String(64), ForeignKey("users.uid", ondelete="RESTRICT"), primary_key=True)
    recipient_name_snapshot = Column(String(100), nullable=False)
    source_name = Column(String(100))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (Index("ix_sjr_recipient_uid", "recipient_uid"),)


class ScheduledJobRun(Base):
    """某个计划时点的不可变运行快照，供 Scheduler 和 Dispatcher 竞争认领。"""

    __tablename__ = "scheduled_job_runs"

    id = Column(String(64), primary_key=True)
    scheduled_job_id = Column(String(64), ForeignKey("scheduled_jobs.id", ondelete="RESTRICT"), nullable=False)
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    # 终态运行不再参与 Dispatcher 扫描，清空该字段避免把历史时间误作待重试时间。
    next_attempt_at = Column(DateTime(timezone=True))
    lease_owner = Column(String(128))
    lease_expires_at = Column(DateTime(timezone=True))
    action_type = Column(String(16), nullable=False)
    action_snapshot = Column(JSON_VALUE, nullable=False)
    recipient_snapshot = Column(JSON_VALUE, nullable=False)
    result_data = Column(JSON_VALUE)
    error_code = Column(String(128))
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    agent_run_id = Column(String(64))
    conversation_id = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'dispatching', 'succeeded', 'partial', 'failed', 'skipped')",
            name="ck_sjr_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_sjr_attempt_count"),
        CheckConstraint(
            "(status = 'dispatching' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'dispatching' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_sjr_dispatching_lease",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'partial', 'failed', 'skipped') AND finished_at IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'partial', 'failed', 'skipped') AND finished_at IS NULL)",
            name="ck_sjr_terminal_finished_at",
        ),
        Index("uq_sjr_job_scheduled_for", "scheduled_job_id", "scheduled_for", unique=True),
        Index("ix_sjr_status_next_attempt_at", "status", "next_attempt_at", "id"),
        Index("ix_sjr_status_lease_expires_at", "status", "lease_expires_at", "id"),
    )


class InboxItem(Base):
    """用户已读状态独立保存，不能取代任务与运行的业务真值。"""

    __tablename__ = "inbox_items"

    id = Column(String(64), primary_key=True)
    recipient_uid = Column(String(64), ForeignKey("users.uid", ondelete="RESTRICT"), nullable=False)
    scheduled_job_id = Column(String(64), ForeignKey("scheduled_jobs.id", ondelete="RESTRICT"), nullable=False)
    scheduled_job_run_id = Column(String(64), ForeignKey("scheduled_job_runs.id", ondelete="RESTRICT"))
    category = Column(String(16), nullable=False)
    item_type = Column(String(64), nullable=False)
    event_key = Column(String(160), nullable=False)
    title = Column(String(100), nullable=False)
    content_snapshot = Column(Text, nullable=False)
    is_read = Column(Boolean, nullable=False, default=False)
    read_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint("category IN ('notification', 'task')", name="ck_ibi_category"),
        CheckConstraint(
            "(is_read IS TRUE AND read_at IS NOT NULL) OR (is_read IS FALSE AND read_at IS NULL)",
            name="ck_ibi_read_at",
        ),
        Index("uq_ibi_recipient_event_key", "recipient_uid", "event_key", unique=True),
        Index(
            "ix_ibi_recipient_category_read_created",
            "recipient_uid",
            "category",
            "is_read",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index("ix_ibi_recipient_job_category_read", "recipient_uid", "scheduled_job_id", "category", "is_read"),
    )


class ScheduledJobAuditLog(Base):
    """仅保存必要快照的不可变审计记录。"""

    __tablename__ = "scheduled_job_audit_logs"

    id = Column(String(64), primary_key=True)
    scheduled_job_id = Column(String(64), ForeignKey("scheduled_jobs.id", ondelete="RESTRICT"))
    candidate_id = Column(String(64), ForeignKey("scheduled_job_candidates.id", ondelete="RESTRICT"))
    actor_uid = Column(String(64), nullable=False)
    action = Column(String(32), nullable=False)
    before_data = Column(JSON_VALUE)
    after_data = Column(JSON_VALUE)
    reason = Column(String(512))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("scheduled_job_id IS NOT NULL OR candidate_id IS NOT NULL", name="ck_sja_source"),
        Index("ix_sja_job_created_at", "scheduled_job_id", text("created_at DESC")),
        Index("ix_sja_candidate_created_at", "candidate_id", text("created_at DESC")),
    )


class ScheduledServiceHeartbeat(Base):
    """循环心跳与业务审计分开，健康检查据此发现假存活进程。"""

    __tablename__ = "scheduled_service_heartbeats"

    service_type = Column(String(16), primary_key=True)
    instance_id = Column(String(128), primary_key=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_error_code = Column(String(128))

    __table_args__ = (CheckConstraint("service_type IN ('scheduler', 'dispatcher')", name="ck_ssh_service_type"),)
