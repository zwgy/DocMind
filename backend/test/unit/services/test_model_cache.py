from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
import yuxi.models.providers.cache as cache_module
from yuxi.models.providers.cache import REDIS_CACHE_KEY, ModelCache, ModelInfo

pytestmark = pytest.mark.unit


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.get_calls = 0

    def get(self, key: str) -> str | None:
        self.get_calls += 1
        return self.data.get(key)

    def set(self, key: str, value: str) -> bool:
        self.data[key] = value
        return True


def _patch_redis(monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis) -> None:
    @contextmanager
    def fake_sync_redis_client(*args, **kwargs):
        del args, kwargs
        yield redis

    monkeypatch.setattr(cache_module, "sync_redis_client", fake_sync_redis_client)


def test_model_cache_prefers_model_base_url_override(monkeypatch):
    saved_cache = {}

    class Provider:
        is_enabled = True
        provider_id = "alibaba"
        api_key = "sk-test"
        api_key_env = None
        provider_type = "openai"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        embedding_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
        rerank_base_url = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
        headers_json = {}
        extra_json = {}
        enabled_models = [
            {
                "id": "qwen3-rerank",
                "type": "rerank",
                "display_name": "Qwen3 Rerank",
                "base_url_override": "https://invalid.example/rerank",
            }
        ]

    cache = ModelCache()
    monkeypatch.setattr(cache, "_save_cache", lambda data: saved_cache.update(data))

    cache.rebuild([Provider()])

    assert saved_cache["alibaba:qwen3-rerank"].base_url == "https://invalid.example/rerank"
    assert saved_cache["alibaba:qwen3-rerank"].context_length is None
    assert saved_cache["alibaba:qwen3-rerank"].min_output_reserve_tokens is None
    assert saved_cache["alibaba:qwen3-rerank"].context_safety_tokens is None


def test_model_cache_merges_provider_and_model_runtime_parameters(monkeypatch):
    saved_cache = {}

    class Provider:
        is_enabled = True
        provider_id = "ollama-local"
        api_key = "ollama"
        api_key_env = None
        provider_type = "openai"
        base_url = "http://localhost:11434/v1"
        embedding_base_url = None
        rerank_base_url = None
        headers_json = {}
        extra_json = {"timeout": 60, "parameters": {"temperature": 0.2}}
        enabled_models = [
            {
                "id": "qwen3.6:35b",
                "type": "chat",
                "context_length": 32768,
                "context_length_source": "models_api",
                "extra": {"parameters": {"reasoning_effort": "none"}},
            }
        ]

    cache = ModelCache()
    monkeypatch.setattr(cache, "_save_cache", lambda data: saved_cache.update(data))

    cache.rebuild([Provider()])

    assert saved_cache["ollama-local:qwen3.6:35b"].extra == {
        "timeout": 60,
        "parameters": {"temperature": 0.2, "reasoning_effort": "none"},
    }
    assert saved_cache["ollama-local:qwen3.6:35b"].context_length_source == "models_api"


def test_model_cache_loads_from_redis_and_uses_local_ttl(monkeypatch: pytest.MonkeyPatch):
    redis = _FakeRedis()
    _patch_redis(monkeypatch, redis)
    redis.data[REDIS_CACHE_KEY] = json.dumps(
        {
            "provider:chat": {
                "provider_id": "provider",
                "model_id": "chat",
                "model_type": "chat",
                "display_name": "Chat",
                "api_key": "sk-test",
                "base_url": "https://example.com/v1",
                "provider_type": "openai",
                "max_completion_tokens": 4096,
            }
        }
    )
    cache = ModelCache()

    info = cache.get_model_info("provider:chat")
    cached_info = cache.get_model_info("provider:chat")

    assert info is not None
    assert cached_info is info
    assert info.base_url == "https://example.com/v1"
    assert info.min_output_reserve_tokens == 4096
    assert redis.get_calls == 1


def test_model_cache_save_writes_redis_json(monkeypatch: pytest.MonkeyPatch):
    redis = _FakeRedis()
    _patch_redis(monkeypatch, redis)
    cache = ModelCache()
    info = ModelInfo(
        provider_id="provider",
        model_id="chat",
        model_type="chat",
        display_name="Chat",
        api_key="sk-test",
        base_url="https://example.com/v1",
        provider_type="openai",
    )

    cache._save_cache({info.spec: info})

    payload = json.loads(redis.data[REDIS_CACHE_KEY])
    assert payload[info.spec]["base_url"] == "https://example.com/v1"
