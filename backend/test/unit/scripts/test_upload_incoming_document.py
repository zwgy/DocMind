import json
import os
import runpy
from pathlib import Path

import requests


# 与仓库级 Compose/脚本断言保持一致：Docker API 运行时只挂载 backend，测试时才从只读卷注入仓库根目录。
ROOT = Path(os.environ.get("YUXI_TEST_REPOSITORY_ROOT", Path(__file__).resolve().parents[4]))
SCRIPT = ROOT / "scripts" / "upload_incoming_document.py.py"


def _load_script():
    return runpy.run_path(str(SCRIPT))


def test_upload_script_uses_document_metadata_and_explicit_main_file():
    script = _load_script()

    assert json.loads(script["INGEST_METADATA"]["document_metadata"])["document_number"]
    metas = json.loads(script["build_file_metas"](script["UPLOAD_ITEMS"]))
    assert [item["is_main_file"] for item in metas] == [True, False, False]


def test_upload_script_rewinds_files_before_retry(tmp_path, monkeypatch):
    script = _load_script()
    upload_item = script["UploadItem"](source_file_id="main", filename="main.pdf", is_main_file=True)
    (tmp_path / "main.pdf").write_bytes(b"complete-content")
    bodies = []

    class SuccessResponse:
        ok = True
        status_code = 200
        text = '{"status":"accepted"}'

        @staticmethod
        def json():
            return {"status": "accepted"}

    def fake_post(*_args, files, **_kwargs):
        bodies.append(files[0][1][1].read())
        if len(bodies) == 1:
            raise requests.ConnectionError("retry")
        return SuccessResponse()

    monkeypatch.setattr(script["requests"], "post", fake_post)
    monkeypatch.setattr(script["time"], "sleep", lambda _seconds: None)

    result = script["upload"](
        api_base="http://example.test",
        token="token",
        base_dir=tmp_path,
        items=[upload_item],
        metadata={
            "source_system": "oa",
            "source_function_id": "incoming",
            "source_doc_id": "DOC-1",
            "document_metadata": "{}",
        },
        max_retries=1,
    )

    assert result == {"status": "accepted"}
    assert bodies == [b"complete-content", b"complete-content"]
