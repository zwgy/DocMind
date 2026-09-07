import asyncio
from types import SimpleNamespace

import pytest

import yuxi.services.incoming_ingest_worker as worker_module
from yuxi.services.incoming_ingest_dispatcher_service import INCOMING_QUEUE_NAME
from yuxi.services.incoming_ingest_worker import IncomingIngestWorkerService, IncomingWorkerSettings


def test_worker_settings_listens_on_dispatcher_dedicated_queue():
    """若 Worker 回退到 ARQ 默认队列，已投递来文会永久停在 queued。"""
    assert IncomingWorkerSettings.queue_name == INCOMING_QUEUE_NAME


@pytest.mark.asyncio
async def test_worker_startup_starts_runtime_configuration_sync(monkeypatch: pytest.MonkeyPatch):
    """来文 Worker 必须获得管理端更新后的模型和解析配置。"""
    calls = []

    async def create_business_tables():
        calls.append("tables")

    async def ensure_business_schema():
        calls.append("schema")

    class RuntimeConfig:
        def start_runtime_sync(self):
            calls.append("runtime-config")

    monkeypatch.setattr(worker_module.pg_manager, "initialize", lambda: calls.append("initialize"))
    monkeypatch.setattr(worker_module.pg_manager, "create_business_tables", create_business_tables)
    monkeypatch.setattr(worker_module.pg_manager, "ensure_business_schema", ensure_business_schema)
    monkeypatch.setattr(worker_module, "sys_config", RuntimeConfig(), raising=False)

    await worker_module._worker_startup(None)

    assert calls == ["initialize", "tables", "schema", "runtime-config"]


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


class AutoRetryRepository(StartedDeliveryRepository):
    def __init__(self, session):
        super().__init__(session)
        self.auto_retries = []

    async def start_delivery(self, **_kwargs):
        return SimpleNamespace(attempt_count=1)

    async def retry_running_delivery(self, **kwargs):
        self.auto_retries.append(kwargs)
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


@pytest.mark.asyncio
async def test_worker_requeues_retryable_download_error_within_configured_limit(monkeypatch: pytest.MonkeyPatch):
    """短暂下载超时应释放租约，交给 Dispatcher 在后续轮询中重新投递。"""
    repository = AutoRetryRepository(None)
    runtime_config = SimpleNamespace(
        incoming_history_window_start="18:00",
        incoming_history_window_end="07:30",
        incoming_auto_retry_count=1,
    )
    monkeypatch.setattr(worker_module, "sys_config", runtime_config)

    async def execute_job(*_args):
        raise ValueError("附件下载超时")

    service = IncomingIngestWorkerService(
        session_factory=FakeSession,
        execute_job=execute_job,
        repository_factory=lambda _session: repository,
        worker_id="worker-1",
    )

    assert await service.process(job_id="ij_1", delivery_token="token-1") is False
    assert repository.auto_retries == [
        {
            "job_id": "ij_1",
            "delivery_token": "token-1",
            "worker_id": "worker-1",
            "error_message": "附件下载超时",
        }
    ]
    assert repository.finished == []
