from types import SimpleNamespace

import pytest

from server.routers import chat_router


@pytest.mark.asyncio
async def test_thread_artifact_download_encodes_chinese_filename(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    artifact = tmp_path / "车辆使用流程.md"
    artifact.write_text("# 流程\n", encoding="utf-8")

    async def resolve_artifact(**_kwargs):
        return artifact

    monkeypatch.setattr(chat_router, "resolve_thread_artifact_view", resolve_artifact)

    response = await chat_router.get_thread_artifact(
        thread_id="thread-1",
        path="home/gem/user-data/outputs/车辆使用流程.md",
        download=True,
        db=object(),
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''%E8%BD%A6%E8%BE%86%E4%BD%BF%E7%94%A8%E6%B5%81%E7%A8%8B.md"
    )
