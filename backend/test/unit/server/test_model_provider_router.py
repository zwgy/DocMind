from types import SimpleNamespace

import pytest

from server.routers import model_provider_router
from server.routers.model_provider_router import ModelProviderPayload


def test_provider_response_exposes_effective_profile_context_without_persisting_it(monkeypatch):
    class Provider:
        provider_id = "openai-compatible"
        is_enabled = True

        def to_dict(self):
            return {
                "provider_id": self.provider_id,
                "enabled_models": [{"id": "profiled-model", "type": "chat"}],
            }

    monkeypatch.setattr(
        "yuxi.agents.models.load_chat_model",
        lambda _spec: SimpleNamespace(
            profile={"max_input_tokens": 65536, "context_length_source": "langchain_profile"}
        ),
    )

    data = model_provider_router._provider_response(Provider())

    model = data["enabled_models"][0]
    assert model["effective_context_length"] == 65536
    assert model["effective_context_length_source"] == "langchain_profile"
    assert "context_length" not in model


def test_model_provider_payload_accepts_embedding_and_rerank_urls():
    payload = ModelProviderPayload(
        provider_id="mixed-provider",
        display_name="Mixed Provider",
        base_url="https://api.example.com/v1",
        embedding_base_url="https://api.example.com/v1/embeddings",
        rerank_base_url="https://api.example.com/v1/rerank",
        capabilities=["chat", "embedding", "rerank"],
    )

    data = payload.model_dump(exclude_none=True)

    assert data["embedding_base_url"] == "https://api.example.com/v1/embeddings"
    assert data["rerank_base_url"] == "https://api.example.com/v1/rerank"


@pytest.mark.asyncio
async def test_update_provider_commits_before_refreshing_cache(monkeypatch):
    calls = []

    class Db:
        async def commit(self):
            calls.append("commit")

    class User:
        username = "admin"

    class Provider:
        def to_dict(self):
            return {"provider_id": "alibaba"}

    async def fake_update_provider_config(db, provider_id, data, username):
        calls.append("update")
        return Provider()

    async def fake_refresh_model_cache():
        calls.append("refresh")

    monkeypatch.setattr(model_provider_router, "update_provider_config", fake_update_provider_config)
    monkeypatch.setattr(model_provider_router, "_refresh_model_cache", fake_refresh_model_cache)

    result = await model_provider_router.update_provider(
        "alibaba",
        ModelProviderPayload(enabled_models=[]),
        current_user=User(),
        db=Db(),
    )

    assert result == {"success": True, "data": {"provider_id": "alibaba"}}
    assert calls == ["update", "commit", "refresh"]
