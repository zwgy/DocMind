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
)

# 发布回滚前先停止 Scheduler/Dispatcher，并确认没有依赖这些表的生产数据。
# 保留为显式 SQL，避免业务代码在运行时执行不可逆删除。
DOWNGRADE_SQL = """
DROP TABLE IF EXISTS scheduled_service_heartbeats;
DROP TABLE IF EXISTS scheduled_job_audit_logs;
DROP TABLE IF EXISTS inbox_items;
DROP TABLE IF EXISTS scheduled_job_runs;
DROP TABLE IF EXISTS scheduled_job_recipients;
DROP TABLE IF EXISTS scheduled_jobs;
DROP TABLE IF EXISTS scheduled_job_candidates;
DROP TABLE IF EXISTS incoming_task_batches;
DROP INDEX IF EXISTS ix_incoming_documents_archived_at;
ALTER TABLE IF EXISTS incoming_documents DROP COLUMN IF EXISTS archived_by;
ALTER TABLE IF EXISTS incoming_documents DROP COLUMN IF EXISTS archived_at;
""".strip()


async def ensure_scheduled_jobs_schema(conn: AsyncConnection) -> None:
    """补齐与已有来文表相关的字段和索引，重复执行不会改变既有任务。"""
    for statement in UPGRADE_STATEMENTS:
        await conn.execute(text(statement))
