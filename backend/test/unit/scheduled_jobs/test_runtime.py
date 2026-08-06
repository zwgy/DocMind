import pytest

from yuxi.scheduled_jobs.runtime import load_runtime_config


def test_load_runtime_config_uses_documented_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCHEDULE_INSTANCE_ID", raising=False)

    config = load_runtime_config()

    assert config.schedule_poll_seconds == 5
    assert config.dispatch_concurrency == 10
    assert config.default_timezone == "Asia/Shanghai"
    assert config.instance_id


def test_load_runtime_config_rejects_lease_not_longer_than_action_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISPATCH_LEASE_SECONDS", "30")
    monkeypatch.setenv("DISPATCH_ACTION_TIMEOUT_SECONDS", "30")

    with pytest.raises(ValueError, match="必须大于"):
        load_runtime_config()


def test_load_runtime_config_rejects_non_positive_value(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCHEDULE_BATCH_SIZE", "0")

    with pytest.raises(ValueError, match="正整数"):
        load_runtime_config()
