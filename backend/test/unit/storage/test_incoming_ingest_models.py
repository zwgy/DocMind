from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from yuxi.storage.postgres.models_knowledge import (
    IncomingDocument,
    IncomingIngestBatch,
    IncomingIngestBatchItem,
    IncomingIngestJob,
)


def _postgresql_ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))


def test_incoming_ingest_models_persist_batch_job_identity_and_delivery_state():
    batch_ddl = _postgresql_ddl(IncomingIngestBatch)
    item_ddl = _postgresql_ddl(IncomingIngestBatchItem)
    job_ddl = _postgresql_ddl(IncomingIngestJob)

    assert "incoming_ingest_batches" in batch_ddl
    assert "batch_key" in batch_ddl
    assert "incoming_ingest_batch_items" in item_ddl
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
