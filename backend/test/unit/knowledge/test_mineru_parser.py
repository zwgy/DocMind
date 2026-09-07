from pathlib import Path
from types import SimpleNamespace

import yuxi.knowledge.parser.mineru as mineru_module

from yuxi.knowledge.parser.mineru import MinerUParser


def test_mineru_parser_uses_backend_environment(tmp_path: Path, monkeypatch) -> None:
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"pdf")
    request_data = {}

    def _fake_post(url, *, files, data, timeout):
        request_data.update(data)
        return SimpleNamespace(status_code=200, headers={"content-type": "application/zip"}, content=b"zip")

    monkeypatch.setenv("MINERU_BACKEND", "hybrid-engine")
    monkeypatch.setattr(mineru_module.requests, "post", _fake_post)
    monkeypatch.setattr(
        mineru_module,
        "process_zip_file_sync",
        lambda *args, **kwargs: {"markdown_content": "parsed"},
    )

    result = MinerUParser("http://mineru.internal").process_file(str(file_path))

    assert result == "parsed"
    assert request_data["backend"] == "hybrid-engine"
    assert "server_url" not in request_data


def test_mineru_parser_overrides_defaults_and_forwards_future_request_params(tmp_path: Path, monkeypatch) -> None:
    """来文 JSON 配置应能扩展 MinerU API，且不能改坏当前 ZIP 响应协议。"""
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"pdf")
    request_data = {}

    def _fake_post(url, *, files, data, timeout):
        request_data.update(data)
        return SimpleNamespace(status_code=200, headers={"content-type": "application/zip"}, content=b"zip")

    monkeypatch.setattr(mineru_module.requests, "post", _fake_post)
    monkeypatch.setattr(
        mineru_module,
        "process_zip_file_sync",
        lambda *args, **kwargs: {"markdown_content": "parsed"},
    )

    MinerUParser("http://mineru.internal").process_file(
        str(file_path),
        {
            "backend": "vlm-engine",
            "effort": "high",
            "table_enable": False,
            "return_md": False,
            "response_format_zip": False,
            "return_image": False,
            "image_prefix": "incoming/private",
        },
    )

    assert request_data["backend"] == "vlm-engine"
    assert request_data["effort"] == "high"
    assert request_data["table_enable"] is False
    assert request_data["return_md"] is True
    assert request_data["response_format_zip"] is True
    assert request_data["return_images"] is True
    assert "return_image" not in request_data
    assert "image_prefix" not in request_data
