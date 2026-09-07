import runpy
from pathlib import Path

import pytest
import requests


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "upload_incoming_batch.py"


def _load_script():
    return runpy.run_path(str(SCRIPT))


def test_upload_batch_reuses_batch_key_after_second_chunk_network_failure(monkeypatch):
    script = _load_script()
    calls = []
    failed_once = True

    class Response:
        ok = True
        status_code = 200

        def json(self):
            if self.path.endswith("/ingest-batches"):
                return {"batchId": "ib_1"}
            return {"items": []}

    def fake_post(url, **kwargs):
        nonlocal failed_once
        calls.append((url, kwargs.get("json")))
        if url.endswith("/items") and len([call for call in calls if call[0].endswith("/items")]) == 2 and failed_once:
            failed_once = False
            raise requests.ConnectionError("temporary network error")
        response = Response()
        response.path = url
        return response

    monkeypatch.setattr(script["requests"], "post", fake_post)
    items = [
        {
            "source_document_id": f"DOC-{index}",
            "document_metadata": {"source_doc_id": f"DOC-{index}"},
            "file_manifest": [{"source_file_id": "main", "filename": "main.pdf", "source_url": "https://oa.test/main"}],
        }
        for index in range(201)
    ]

    with pytest.raises(requests.ConnectionError):
        script["upload_batch"](
            api_base="http://api.test",
            token="token",
            source_system="oa",
            batch_key="history-2026",
            name="历史来文",
            items=items,
        )

    result = script["upload_batch"](
        api_base="http://api.test",
        token="token",
        source_system="oa",
        batch_key="history-2026",
        name="历史来文",
        items=items,
    )

    assert result == {"batchId": "ib_1", "registered": 201}
    create_payloads = [payload for url, payload in calls if url.endswith("/ingest-batches")]
    assert create_payloads == [
        {"source_system": "oa", "batch_key": "history-2026", "name": "历史来文"},
        {"source_system": "oa", "batch_key": "history-2026", "name": "历史来文"},
    ]
    assert calls[-1][0].endswith("/ingest-batches/ib_1/submit")
