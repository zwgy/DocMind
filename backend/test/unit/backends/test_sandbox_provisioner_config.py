from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient


MODULE_NAME = "sandbox_provisioner_app_for_test"


def _find_module_path() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "docker" / "sandbox_provisioner" / "app.py"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("docker/sandbox_provisioner/app.py not found from test path")


MODULE_PATH = _find_module_path()


def _load_module():
    existing = sys.modules.get(MODULE_NAME)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _docker_backend(module, tmp_path, run_container):
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._lock = threading.Lock()
    backend._container_port = 8080
    backend._network_prefix = "yuxi-know-sandbox"
    backend._sandbox_image = "sandbox-image"
    backend._container_prefix = "yuxi-sandbox"
    backend._sandbox_env = {}
    backend._health_timeout_seconds = 1
    backend._threads_host_path = str(tmp_path)
    backend._client = SimpleNamespace(containers=SimpleNamespace(run=run_container))
    return backend


def test_canonical_backend_name(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.canonical_backend_name("docker") == "docker"
    assert module.canonical_backend_name("kubernetes") == "kubernetes"


def test_merged_sandbox_env_user_values_override_global(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.merged_sandbox_env(
        {"SHARED": "global", "GLOBAL_ONLY": "value"},
        {"SHARED": "user", "USER_ONLY": "value"},
    ) == {
        "SHARED": "user",
        "GLOBAL_ONLY": "value",
        "USER_ONLY": "value",
    }


def test_normalize_env_converts_values_to_strings(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.normalize_env({"A": 1, "B": None, "": "ignored"}) == {"A": "1", "B": ""}


def test_local_container_identity_validation_rejects_unsafe_path_segments(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend_cls = module.LocalContainerProvisionerBackend

    assert backend_cls._validate_thread_id("thread-1_2") == "thread-1_2"
    assert backend_cls._validate_uid("user-1_2") == "user-1_2"

    for value in ["../escape", "thread/name", "thread name", "thread;rm", "thread.name"]:
        with pytest.raises(ValueError):
            backend_cls._validate_thread_id(value)

    for value in ["../user", "user/name", "user name", "user;rm", "user.name"]:
        with pytest.raises(ValueError):
            backend_cls._validate_uid(value)


def test_memory_backend_accepts_split_thread_ids(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = module.MemoryProvisionerBackend()

    record = backend.create(
        "sandbox-1",
        "child-thread",
        "user-1",
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
    )

    assert record.sandbox_id == "sandbox-1"
    assert backend.discover("sandbox-1") is record


def test_docker_mount_checks_use_file_and_skills_thread_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._threads_host_path = str(tmp_path)

    workspace = tmp_path / "shared" / "user-1" / "workspace"
    uploads = tmp_path / "parent-thread" / "user-data" / "uploads"
    outputs = tmp_path / "parent-thread" / "user-data" / "outputs"
    skills = tmp_path / "child-skills-thread" / "skills"
    container = SimpleNamespace(
        attrs={
            "Mounts": [
                {"Destination": "/home/gem/user-data/workspace", "Source": str(workspace)},
                {"Destination": "/home/gem/user-data/uploads", "Source": str(uploads)},
                {"Destination": "/home/gem/user-data/outputs", "Source": str(outputs)},
                {"Destination": "/home/gem/skills", "Source": str(skills)},
            ]
        }
    )

    assert backend._has_expected_user_data_mounts(container, "parent-thread", "user-1") is True
    assert backend._is_expected_skills_mount(container, "child-skills-thread") is True
    assert backend._has_expected_user_data_mounts(container, "child-thread", "user-1") is False
    assert backend._is_expected_skills_mount(container, "parent-thread") is False


def test_kubernetes_mount_check_uses_file_and_skills_thread_ids(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            containers=[
                SimpleNamespace(
                    name="sandbox",
                    volume_mounts=[
                        SimpleNamespace(
                            mount_path="/home/gem/user-data/workspace",
                            sub_path="threads/shared/user-1/workspace",
                        ),
                        SimpleNamespace(
                            mount_path="/home/gem/user-data/uploads",
                            sub_path="threads/parent-thread/user-data/uploads",
                        ),
                        SimpleNamespace(
                            mount_path="/home/gem/user-data/outputs",
                            sub_path="threads/parent-thread/user-data/outputs",
                        ),
                        SimpleNamespace(mount_path="/home/gem/skills", sub_path="threads/child-skills-thread/skills"),
                    ],
                )
            ]
        )
    )

    assert module.KubernetesProvisionerBackend._pod_has_expected_mounts(
        pod,
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
        uid="user-1",
    )
    assert not module.KubernetesProvisionerBackend._pod_has_expected_mounts(
        pod,
        file_thread_id="child-thread",
        skills_thread_id="child-skills-thread",
        uid="user-1",
    )


def test_management_api_requires_bearer_token(monkeypatch):
    token = "test-provisioner-token-that-is-long-enough"
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", token)
    module = _load_module()

    with TestClient(module.app) as client:
        assert client.get("/api/sandboxes").status_code == 401
        assert client.get("/api/sandboxes", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get("/api/sandboxes", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


def test_management_api_returns_authenticated_proxy_url(monkeypatch):
    token = "test-provisioner-token-that-is-long-enough"
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", token)
    monkeypatch.setenv("PROVISIONER_PUBLIC_URL", "http://sandbox-provisioner:8002")
    module = _load_module()
    sandbox_id = "sandbox-auth-test"
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(module.app) as client:
        response = client.post(
            "/api/sandboxes",
            headers=headers,
            json={"sandbox_id": sandbox_id, "thread_id": "thread-1", "uid": "user-1"},
        )

    assert response.status_code == 200
    assert response.json()["sandbox_url"] == f"http://sandbox-provisioner:8002/api/sandboxes/{sandbox_id}/proxy"


def test_authenticated_proxy_strips_management_token(monkeypatch):
    token = "test-provisioner-token-that-is-long-enough"
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", token)
    module = _load_module()
    captured = []
    real_async_client = httpx.AsyncClient

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    def create_client(**kwargs):
        return real_async_client(transport=httpx.MockTransport(upstream), **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", create_client)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(module.app) as client:
        client.post(
            "/api/sandboxes",
            headers=headers,
            json={"sandbox_id": "sandbox-proxy-test", "thread_id": "thread-1", "uid": "user-1"},
        )
        response = client.get(
            "/api/sandboxes/sandbox-proxy-test/proxy/v1/sandbox",
            headers=headers,
            params={"detail": "full"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert str(captured[0].url) == "http://agent-sandbox:8000/v1/sandbox?detail=full"
    assert "authorization" not in captured[0].headers


def test_docker_backend_uses_private_network_without_published_port(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    captured = []

    class FakeContainer:
        name = "yuxi-sandbox-sandbox-1"
        status = "running"
        attrs = {"State": {"Status": "running"}}

        def reload(self):
            return None

    backend = _docker_backend(
        module,
        tmp_path,
        lambda image, **kwargs: captured.append((image, kwargs)) or FakeContainer(),
    )
    monkeypatch.setattr(backend, "_get_container", lambda _sandbox_id: None)
    monkeypatch.setattr(backend, "_ensure_network", backend._network_name)
    monkeypatch.setattr(backend, "_ensure_user_data_writable", lambda _container: None)
    monkeypatch.setattr(module, "wait_for_sandbox_ready", lambda _url, timeout_seconds: True)

    record = backend.create("sandbox-1", "thread-1", "user-1")

    assert record.sandbox_url == "http://yuxi-sandbox-sandbox-1:8080"
    assert captured[0][1]["network"] == "yuxi-know-sandbox-sandbox-1"
    assert "ports" not in captured[0][1]
