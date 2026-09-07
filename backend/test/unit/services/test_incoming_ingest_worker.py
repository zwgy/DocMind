import pytest

from yuxi.services.incoming_ingest_worker import IncomingIngestWorkerService


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def begin(self):
        return self


class StaleTokenRepository:
    def __init__(self, _session):
        pass

    async def start_delivery(self, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_worker_skips_stale_delivery_before_calling_ingest_service():
    """延迟到达的旧 Redis 消息不得再次下载、OCR 或覆盖当前来文结果。"""
    called = False

    async def execute_job(*_args):
        nonlocal called
        called = True

    service = IncomingIngestWorkerService(
        session_factory=FakeSession,
        execute_job=execute_job,
        repository_factory=StaleTokenRepository,
    )

    assert await service.process(job_id="ij_1", delivery_token="expired-token") is False
    assert called is False
