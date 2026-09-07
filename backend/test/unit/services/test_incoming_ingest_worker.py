import asyncio

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


class StartedDeliveryRepository:
    def __init__(self, _session):
        self.finished = []

    async def start_delivery(self, **_kwargs):
        return object()

    async def finish_delivery(self, **kwargs):
        self.finished.append(kwargs)
        return True


class HeartbeatRepository(StartedDeliveryRepository):
    def __init__(self, session):
        super().__init__(session)
        self.renewals = []

    async def renew_lease(self, **kwargs):
        self.renewals.append(kwargs)
        return True


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


@pytest.mark.asyncio
async def test_worker_marks_successful_delivery_terminal_after_execution():
    repository = StartedDeliveryRepository(None)
    executed = []

    async def execute_job(job_id, delivery_token):
        executed.append((job_id, delivery_token))

    service = IncomingIngestWorkerService(
        session_factory=FakeSession,
        execute_job=execute_job,
        repository_factory=lambda _session: repository,
        worker_id="worker-1",
    )

    assert await service.process(job_id="ij_1", delivery_token="token-1") is True
    assert executed == [("ij_1", "token-1")]
    assert repository.finished == [
        {
            "job_id": "ij_1",
            "delivery_token": "token-1",
            "worker_id": "worker-1",
            "status": "succeeded",
        }
    ]


@pytest.mark.asyncio
async def test_worker_renews_lease_while_long_running_execution_is_active():
    repository = HeartbeatRepository(None)
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute_job(*_args):
        started.set()
        await release.wait()

    service = IncomingIngestWorkerService(
        session_factory=FakeSession,
        execute_job=execute_job,
        repository_factory=lambda _session: repository,
        worker_id="worker-1",
        lease_renew_interval_seconds=0.01,
    )

    task = asyncio.create_task(service.process(job_id="ij_1", delivery_token="token-1"))
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0.03)
    release.set()

    assert await task is True
    assert repository.renewals
    assert repository.renewals[0]["job_id"] == "ij_1"
