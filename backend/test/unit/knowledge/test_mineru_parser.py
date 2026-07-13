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
