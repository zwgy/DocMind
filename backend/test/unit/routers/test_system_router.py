from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.routers import system_router
from server.routers.system_router import system
from server.utils.auth_middleware import get_admin_user
from yuxi.config.app import Config

pytestmark = pytest.mark.unit


def test_discovery_endpoint_is_public(monkeypatch):
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.1.dev0")

    app = FastAPI()
    app.include_router(system, prefix="/api")
    response = TestClient(app).get("/api/system/discovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Yuxi"
    assert payload["version"] == "0.7.1.dev0"
    assert payload["api_prefix"] == "/api"
    assert payload["capabilities"]["cli"]["browser_login"] is True
    assert payload["capabilities"]["cli"]["api_key_auth"] is True
    assert payload["capabilities"]["cli"]["kb_upload"] is True
    assert payload["endpoints"]["cli_auth_sessions"] == "/api/auth/cli/sessions"


async def test_batch_config_update_maps_validation_error_to_400(monkeypatch):
    class FakeConfig:
        def update(self, _items):
            raise ValueError("来文来源文件下载超时秒数必须大于等于 10")

    monkeypatch.setattr(system_router, "config", FakeConfig())

    with pytest.raises(HTTPException) as exc_info:
        await system_router.update_config_batch({"incoming_download_timeout_seconds": 5}, current_user=object())

    assert exc_info.value.status_code == 400
    assert "下载超时" in exc_info.value.detail


def test_batch_config_update_returns_serializable_config_metadata(monkeypatch):
    test_config = Config.model_construct()
    monkeypatch.setattr(system_router, "config", test_config)
    app = FastAPI()
    app.include_router(system, prefix="/api")
    app.dependency_overrides[get_admin_user] = lambda: object()

    response = TestClient(app).post(
        "/api/system/config/update",
        json={
            "incoming_history_window_start": "00:00",
            "incoming_history_window_end": "23:59",
        },
    )

    assert response.status_code == 200
    assert response.json()["_config_items"]["document_parser_ocr_engine_config"]["default"] == {}
