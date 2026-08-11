"""定时任务 schema 的显式、幂等补齐语句及人工降级 SQL。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# 新表由 SQLAlchemy metadata.create_all 创建。此处只处理既有来文表的字段补齐及
# PostgreSQL 特有索引，以便滚动发布时 API、Worker 和后续独立服务使用同一 schema。
UPGRADE_STATEMENTS = (
    "ALTER TABLE IF EXISTS incoming_documents ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
    "ALTER TABLE IF EXISTS incoming_documents ADD COLUMN IF NOT EXISTS archived_by VARCHAR(64)",
    "CREATE INDEX IF NOT EXISTS ix_incoming_documents_archived_at ON incoming_documents(archived_at)",
    "ALTER TABLE IF EXISTS scheduled_job_runs ALTER COLUMN next_attempt_at DROP NOT NULL",
    "ALTER TABLE IF EXISTS scheduled_job_runs ADD COLUMN IF NOT EXISTS conversation_thread_id VARCHAR(64)",
    (
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM scheduled_job_runs AS run "
        "JOIN conversations AS conversation ON run.conversation_id = conversation.id::text "
        "JOIN scheduled_jobs AS job ON job.id = run.scheduled_job_id "
        "WHERE run.conversation_thread_id IS NULL AND conversation.uid <> job.owner_uid) "
        "THEN RAISE EXCEPTION 'scheduled_job_run_conversation_owner_mismatch'; END IF; END $$"
    ),
    (
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM scheduled_job_runs AS run "
        "JOIN scheduled_jobs AS job ON job.id = run.scheduled_job_id "
        "LEFT JOIN conversations AS conversation ON run.conversation_thread_id = conversation.thread_id "
        "WHERE run.conversation_thread_id IS NOT NULL "
        "AND (conversation.id IS NULL OR conversation.uid <> job.owner_uid)) "
        "THEN RAISE EXCEPTION 'scheduled_job_run_conversation_thread_invalid'; END IF; END $$"
    ),
    (
        "DO $$ BEGIN IF EXISTS (SELECT target_thread_id FROM ("
        "SELECT COALESCE(run.conversation_thread_id, conversation.thread_id) AS target_thread_id "
        "FROM scheduled_job_runs AS run "
        "LEFT JOIN conversations AS conversation ON run.conversation_id = conversation.id::text"
        ") AS candidates WHERE target_thread_id IS NOT NULL GROUP BY target_thread_id HAVING COUNT(*) > 1) "
        "THEN RAISE EXCEPTION 'scheduled_job_run_conversation_thread_duplicate'; END IF; END $$"
    ),
    (
        "UPDATE scheduled_job_runs AS run SET conversation_thread_id = conversation.thread_id "
        "FROM conversations AS conversation, scheduled_jobs AS job "
        "WHERE run.conversation_thread_id IS NULL AND run.conversation_id = conversation.id::text "
        "AND job.id = run.scheduled_job_id AND conversation.uid = job.owner_uid"
    ),
    (
        "UPDATE conversations AS conversation SET extra_metadata = jsonb_set("
        "COALESCE(conversation.extra_metadata::jsonb, '{}'::jsonb), '{scheduled_source_type}', "
        "to_jsonb(job.source_type), true)::json FROM scheduled_job_runs AS run, scheduled_jobs AS job "
        "WHERE run.conversation_id = conversation.id::text AND job.id = run.scheduled_job_id "
        "AND conversation.extra_metadata->>'source' = 'scheduled_job' "
        "AND conversation.extra_metadata->>'scheduled_source_type' IS NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sjr_conversation_thread_id "
        "ON scheduled_job_runs(conversation_thread_id) WHERE conversation_thread_id IS NOT NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_sjr_agent_reconcile ON scheduled_job_runs(status, updated_at, id) "
        "WHERE action_type = 'agent' AND status IN ('queued', 'running')"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_conversations_scheduled_owner_updated "
        "ON conversations(uid, updated_at DESC, id DESC) "
        "WHERE status = 'active' AND extra_metadata->>'source' = 'scheduled_job'"
    ),
    "ALTER TABLE IF EXISTS scheduled_job_candidates ADD COLUMN IF NOT EXISTS notification_title VARCHAR(100)",
    "ALTER TABLE IF EXISTS scheduled_job_candidates ALTER COLUMN notification_content DROP NOT NULL",
    "ALTER TABLE IF EXISTS scheduled_job_candidates ALTER COLUMN owner_uid DROP NOT NULL",
    "ALTER TABLE IF EXISTS scheduled_job_candidates ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE IF EXISTS inbox_items ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ",
    (
        "CREATE INDEX IF NOT EXISTS ix_ibi_recipient_category_hidden_read_created "
        "ON inbox_items(recipient_uid, category, hidden_at, is_read, created_at DESC, id DESC)"
    ),
    "ALTER TABLE IF EXISTS scheduled_jobs ALTER COLUMN owner_uid DROP NOT NULL",
    (
        "UPDATE scheduled_jobs SET owner_uid = NULL "
        "WHERE source_type = 'incoming' AND action_type = 'notification' AND owner_uid IS NOT NULL"
    ),
    "ALTER TABLE IF EXISTS scheduled_jobs DROP CONSTRAINT IF EXISTS ck_sj_source_owner",
    (
        "ALTER TABLE IF EXISTS scheduled_jobs ADD CONSTRAINT ck_sj_source_owner CHECK ("
        "(source_type = 'personal' AND owner_uid IS NOT NULL) OR "
        "(source_type = 'incoming' AND action_type = 'notification' AND owner_uid IS NULL) OR "
        "(source_type = 'incoming' AND action_type = 'agent' AND owner_uid IS NOT NULL)) NOT VALID"
    ),
    "ALTER TABLE IF EXISTS scheduled_jobs DROP CONSTRAINT IF EXISTS ck_sj_action_type",
    (
        "ALTER TABLE IF EXISTS scheduled_jobs ADD CONSTRAINT ck_sj_action_type "
        "CHECK (action_type IN ('notification', 'agent'))"
    ),
    "ALTER TABLE IF EXISTS scheduled_jobs DROP CONSTRAINT IF EXISTS ck_sj_incoming_notification_only",
    (
        "ALTER TABLE IF EXISTS scheduled_jobs ADD CONSTRAINT ck_sj_incoming_notification_only "
        "CHECK (source_type <> 'incoming' OR action_type = 'notification') NOT VALID"
    ),
    "ALTER TABLE IF EXISTS scheduled_job_runs DROP CONSTRAINT IF EXISTS ck_sjr_status",
    (
        "ALTER TABLE IF EXISTS scheduled_job_runs ADD CONSTRAINT ck_sjr_status "
        "CHECK (status IN ('pending', 'dispatching', 'queued', 'running', 'succeeded', "
        "'partial', 'failed', 'cancelled', 'skipped'))"
    ),
    "ALTER TABLE IF EXISTS scheduled_job_runs DROP CONSTRAINT IF EXISTS ck_sjr_terminal_finished_at",
    (
        "ALTER TABLE IF EXISTS scheduled_job_runs ADD CONSTRAINT ck_sjr_terminal_finished_at "
        "CHECK ((status IN ('succeeded', 'partial', 'failed', 'cancelled', 'skipped') "
        "AND finished_at IS NOT NULL) OR (status NOT IN ('succeeded', 'partial', 'failed', "
        "'cancelled', 'skipped') AND finished_at IS NULL))"
    ),
)

# 发布回滚前先停止 Scheduler/Dispatcher，并确认没有依赖这些表的生产数据。
# 保留为显式 SQL，避免业务代码在运行时执行不可逆删除。
DOWNGRADE_SQL = """
DROP TABLE IF EXISTS scheduled_service_heartbeats;
DROP TABLE IF EXISTS scheduled_job_user_states;
DROP TABLE IF EXISTS scheduled_job_audit_logs;
DROP TABLE IF EXISTS inbox_items;
DROP TABLE IF EXISTS scheduled_job_runs;
DROP TABLE IF EXISTS scheduled_job_recipients;
DROP TABLE IF EXISTS scheduled_jobs;
DROP TABLE IF EXISTS scheduled_job_candidates;
DROP TABLE IF EXISTS incoming_task_batches;
DROP INDEX IF EXISTS ix_conversations_scheduled_owner_updated;
DROP INDEX IF EXISTS ix_incoming_documents_archived_at;
ALTER TABLE IF EXISTS incoming_documents DROP COLUMN IF EXISTS archived_by;
ALTER TABLE IF EXISTS incoming_documents DROP COLUMN IF EXISTS archived_at;
""".strip()


async def ensure_scheduled_jobs_schema(conn: AsyncConnection) -> None:
    """补齐与已有来文表相关的字段和索引，重复执行不会改变既有任务。"""
    for statement in UPGRADE_STATEMENTS:
        await conn.execute(text(statement))
