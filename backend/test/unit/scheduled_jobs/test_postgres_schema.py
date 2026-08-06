from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from yuxi.storage.postgres.models_knowledge import IncomingDocument
from yuxi.storage.postgres.models_scheduled_jobs import InboxItem, ScheduledJob, ScheduledJobCandidate, ScheduledJobRun
from yuxi.storage.postgres.scheduled_jobs_migration import DOWNGRADE_SQL, UPGRADE_STATEMENTS


def test_scheduled_job_postgres_ddl_has_schedule_and_idempotency_guards():
    ddl = str(CreateTable(ScheduledJob.__table__).compile(dialect=postgresql.dialect()))
    indexes = "\n".join(
        str(CreateIndex(index).compile(dialect=postgresql.dialect())) for index in ScheduledJob.__table__.indexes
    )

    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "ck_sj_schedule_fields" in ddl
    assert "ck_sj_request_idempotency_pair" in ddl
    assert "ck_sj_source_candidate_type" in ddl
    assert "WHERE source_candidate_id IS NOT NULL" in indexes
    assert "WHERE status = 'active' AND next_run_at IS NOT NULL" in indexes


def test_run_and_inbox_postgres_ddl_protect_lease_and_event_idempotency():
    run_ddl = str(CreateTable(ScheduledJobRun.__table__).compile(dialect=postgresql.dialect()))
    inbox_indexes = "\n".join(
        str(CreateIndex(index).compile(dialect=postgresql.dialect())) for index in InboxItem.__table__.indexes
    )

    assert "ck_sjr_dispatching_lease" in run_ddl
    assert "ck_sjr_terminal_finished_at" in run_ddl
    assert "next_attempt_at TIMESTAMP WITH TIME ZONE" in run_ddl
    assert "next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL" not in run_ddl
    assert "CREATE UNIQUE INDEX uq_ibi_recipient_event_key ON inbox_items (recipient_uid, event_key)" in inbox_indexes


def test_scheduled_jobs_migration_has_idempotent_upgrade_and_manual_downgrade_sql():
    assert any("next_attempt_at DROP NOT NULL" in statement for statement in UPGRADE_STATEMENTS)
    assert any("notification_title" in statement for statement in UPGRADE_STATEMENTS)
    assert any("notification_content DROP NOT NULL" in statement for statement in UPGRADE_STATEMENTS)
    assert any("owner_uid DROP NOT NULL" in statement for statement in UPGRADE_STATEMENTS)
    assert any("ADD COLUMN IF NOT EXISTS version" in statement for statement in UPGRADE_STATEMENTS)
    for table_name, constraint_name in (
        ("scheduled_jobs", "ck_sj_action_type"),
        ("scheduled_job_runs", "ck_sjr_status"),
        ("scheduled_job_runs", "ck_sjr_terminal_finished_at"),
    ):
        drop_statement = f"ALTER TABLE IF EXISTS {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}"
        add_statement_prefix = f"ALTER TABLE IF EXISTS {table_name} ADD CONSTRAINT {constraint_name}"
        drop_index = UPGRADE_STATEMENTS.index(drop_statement)
        assert UPGRADE_STATEMENTS[drop_index + 1].startswith(add_statement_prefix)

    assert all(
        "IF EXISTS" in statement or "DROP NOT NULL" in statement or "ADD CONSTRAINT" in statement
        for statement in UPGRADE_STATEMENTS
    )
    assert "DROP TABLE IF EXISTS scheduled_jobs" in DOWNGRADE_SQL
    assert "DROP COLUMN IF EXISTS archived_at" in DOWNGRADE_SQL


def test_incoming_document_model_exposes_archival_fields_for_repository_use():
    assert "archived_at" in IncomingDocument.__table__.columns
    assert "archived_by" in IncomingDocument.__table__.columns


def test_candidate_ddl_keeps_incomplete_notification_draft_for_manual_confirmation():
    ddl = str(CreateTable(ScheduledJobCandidate.__table__).compile(dialect=postgresql.dialect()))

    assert "notification_title VARCHAR(100)" in ddl
    assert "notification_content TEXT NOT NULL" not in ddl
    assert "owner_uid VARCHAR(64) NOT NULL" not in ddl
    assert "ck_sjc_version" in ddl
