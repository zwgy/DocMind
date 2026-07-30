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
        preview=False,
        db=object(),
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''%E8%BD%A6%E8%BE%86%E4%BD%BF%E7%94%A8%E6%B5%81%E7%A8%8B.md"
    )


@pytest.mark.asyncio
async def test_thread_artifact_preview_converts_office_to_pdf(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    artifact = tmp_path / "检查报告.docx"
    artifact.write_bytes(b"PK\x03\x04document")

    async def resolve_artifact(**_kwargs):
        return artifact

    async def convert(filename: str, content: bytes) -> bytes:
        assert filename == "检查报告.docx"
        assert content == b"PK\x03\x04document"
        return b"%PDF-1.7\npreview"

    monkeypatch.setattr(chat_router, "resolve_thread_artifact_view", resolve_artifact)
    monkeypatch.setattr(chat_router, "convert_office_to_pdf", convert)

    response = await chat_router.get_thread_artifact(
        thread_id="thread-1",
        path="home/gem/user-data/outputs/检查报告.docx",
        download=False,
        preview=True,
        db=object(),
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-1.7\npreview"
    assert response.headers["content-disposition"].endswith("%E6%A3%80%E6%9F%A5%E6%8A%A5%E5%91%8A.pdf")


@pytest.mark.asyncio
async def test_thread_artifact_preview_rejects_oversized_office_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    artifact = tmp_path / "检查报告.xlsx"
    artifact.write_bytes(b"1234")

    async def resolve_artifact(**_kwargs):
        return artifact

    monkeypatch.setattr(chat_router, "resolve_thread_artifact_view", resolve_artifact)
    monkeypatch.setattr(chat_router, "MAX_BINARY_PREVIEW_SIZE_BYTES", 3)

    with pytest.raises(chat_router.HTTPException) as exc_info:
        await chat_router.get_thread_artifact(
            thread_id="thread-1",
            path="home/gem/user-data/outputs/检查报告.xlsx",
            download=False,
            preview=True,
            db=object(),
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.status_code == 413
