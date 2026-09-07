from types import SimpleNamespace

import pytest

from yuxi.services import incoming_document_ingest_service as ingest_module
from yuxi.services.incoming_document_ingest_service import IncomingDocumentIngestService


class FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


class FakeIngestRepository:
    def __init__(self, _session, job):
        self.job = job
        self.bound_incoming_ids = []

    async def get_running_delivery(self, *, job_id, delivery_token):
        if self.job.job_id == job_id and self.job.delivery_token == delivery_token:
            return self.job
        return None

    async def bind_incoming_document(self, *, job_id, delivery_token, incoming_id):
        if await self.get_running_delivery(job_id=job_id, delivery_token=delivery_token) is None:
            return False
        self.bound_incoming_ids.append(incoming_id)
        return True


@pytest.mark.asyncio
async def test_execute_ingest_job_skips_stale_token_before_external_processing(monkeypatch):
    job = SimpleNamespace(job_id="ij_1", delivery_token="current-token")
    repository = FakeIngestRepository(object(), job)
    monkeypatch.setattr(ingest_module.pg_manager, "get_async_session_context", FakeSessionContext)
    monkeypatch.setattr(ingest_module, "IncomingIngestRepository", lambda _session: repository, raising=False)
    service = IncomingDocumentIngestService()

    async def unexpected_download(*_args, **_kwargs):
        raise AssertionError("旧令牌不应下载或解析来文")

    service.download_source_files = unexpected_download

    result = await service.execute_ingest_job("ij_1", "expired-token")

    assert result == {"job_id": "ij_1", "status": "stale"}
    assert repository.bound_incoming_ids == []


@pytest.mark.asyncio
async def test_execute_ingest_job_processes_manifest_for_current_delivery(monkeypatch):
    job = SimpleNamespace(
        job_id="ij_1",
        delivery_token="current-token",
        source_system="oa",
        source_document_id="DOC-1",
        document_metadata={"title": "通知"},
        file_manifest=[{"source_file_id": "main", "filename": "main.pdf", "source_url": "https://oa.test/main"}],
        created_by="admin",
    )
    repository = FakeIngestRepository(object(), job)
    monkeypatch.setattr(ingest_module.pg_manager, "get_async_session_context", FakeSessionContext)
    monkeypatch.setattr(ingest_module, "IncomingIngestRepository", lambda _session: repository, raising=False)
    service = IncomingDocumentIngestService()
    calls = []

    async def fake_download(*, files):
        calls.append(("download", files))
        return [{"source_file_id": "main", "filename": "main.pdf", "content": b"pdf"}]

    async def fake_ingest(**kwargs):
        calls.append(("ingest", kwargs))
        return {"incomingId": "inc_1", "status": "accepted"}

    async def fake_process(incoming_id, *, operator_id=None, publication_guard=None):
        calls.append(("process", incoming_id, operator_id))
        assert await publication_guard() is True
        return {"incoming_id": incoming_id, "status": "ready"}

    service.download_source_files = fake_download
    service.ingest_files = fake_ingest
    service.process_incoming_document = fake_process

    result = await service.execute_ingest_job("ij_1", "current-token")

    assert result == {"job_id": "ij_1", "incoming_id": "inc_1", "status": "ready"}
    assert calls[0][0] == "download"
    assert calls[1][0] == "ingest"
    assert calls[1][1]["enqueue_processing"] is False
    assert calls[2] == ("process", "inc_1", "admin")
    assert repository.bound_incoming_ids == ["inc_1"]
