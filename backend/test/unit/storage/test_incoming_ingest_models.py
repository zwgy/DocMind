from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from yuxi.storage.postgres.models_knowledge import (
    IncomingDocument,
    IncomingIngestJob,
)


def _postgresql_ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))


def test_incoming_ingest_job_persists_source_identity_and_delivery_state():
    job_ddl = _postgresql_ddl(IncomingIngestJob)

    assert "incoming_ingest_jobs" in job_ddl
    assert "source_system" in job_ddl
    assert "source_document_id" in job_ddl
    assert "delivery_token" in job_ddl
    assert "lease_expires_at" in job_ddl
    assert "input_ready" in job_ddl
    assert any(
        constraint.name == "uq_incoming_ingest_jobs_source_identity"
        for constraint in IncomingIngestJob.__table__.constraints
    )


def test_incoming_document_tracks_the_published_extraction_run():
    assert "published_extraction_run_id" in IncomingDocument.__table__.columns
