import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(os.environ.get("YUXI_TEST_REPOSITORY_ROOT", Path(__file__).resolve().parents[4]))
SCRIPT = ROOT / "scripts" / "upload_incoming_batch.py"
STATUSES = ("accepted", "exists", "requeued", "conflict")


def _load_script():
    return runpy.run_path(str(SCRIPT))


def _document(source_document_id: str, *, metadata_size: int = 0) -> dict:
    return {
        "source_document_id": source_document_id,
        "metadata": {"title": "x" * metadata_size},
        "files": [
            {
                "source_file_id": "main",
                "filename": "main.pdf",
                "source_url": f"https://attachments.test/{source_document_id}.pdf",
                "is_main_file": True,
            }
        ],
    }


def _response(items: list[dict]):
    class Response:
        ok = True

        def raise_for_status(self):
            raise AssertionError("成功响应不应调用 raise_for_status")

        def json(self):
            return {
                "items": items,
                "summary": {status: sum(item["status"] == status for item in items) for status in STATUSES},
            }

    return Response()


def test_help_explains_arguments_input_fields_and_result_statuses():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    for text in (
        "documents_file",
        "--api-base",
        "INGEST_API_BASE",
        "--token",
        "INGEST_TOKEN",
        "--source-system",
        "source_document_id",
        "source_file_id",
        "source_url",
        "source_system + source_document_id",
        "accepted / exists / requeued / conflict",
        "python -m pip install requests",
    ):
        assert text in result.stdout


def test_upload_batch_posts_directly_in_two_hundred_document_chunks(monkeypatch):
    script = _load_script()
    payloads = []

    def fake_post(url, **kwargs):
        assert url == "http://api.test/api/incoming-documents/ingest-batches"
        payload = json.loads(kwargs["data"])
        payloads.append(payload)
        return _response(
            [
                {
                    "sourceDocumentId": item["source_document_id"],
                    "jobId": f"ij_{item['source_document_id']}",
                    "status": "accepted",
                    "message": None,
                }
                for item in payload["documents"]
            ]
        )

    monkeypatch.setattr(script["requests"], "post", fake_post)
    result = script["upload_batch"](
        api_base="http://api.test",
        token="token",
        source_system="oa",
        documents=[_document(f"DOC-{index}") for index in range(201)],
    )

    assert [len(payload["documents"]) for payload in payloads] == [200, 1]
    assert result["summary"] == {"accepted": 201, "exists": 0, "requeued": 0, "conflict": 0}
    assert len(result["items"]) == 201


def test_upload_batch_splits_on_exact_encoded_request_size(monkeypatch):
    script = _load_script()
    payloads = []
    first = _document("DOC-1", metadata_size=80)
    second = _document("DOC-2", metadata_size=80)
    one_size = len(script["_encode_payload"]({"source_system": "oa", "documents": [first]}))
    two_size = len(script["_encode_payload"]({"source_system": "oa", "documents": [first, second]}))
    script["upload_batch"].__globals__["BATCH_BYTES_LIMIT"] = two_size - 1
    assert one_size < script["upload_batch"].__globals__["BATCH_BYTES_LIMIT"]

    def fake_post(_url, **kwargs):
        payload = json.loads(kwargs["data"])
        payloads.append(payload)
        document = payload["documents"][0]
        return _response(
            [
                {
                    "sourceDocumentId": document["source_document_id"],
                    "jobId": "ij_1",
                    "status": "accepted",
                    "message": None,
                }
            ]
        )

    monkeypatch.setattr(script["requests"], "post", fake_post)
    script["upload_batch"](
        api_base="http://api.test",
        token="token",
        source_system="oa",
        documents=[first, second],
    )

    assert [len(payload["documents"]) for payload in payloads] == [1, 1]
    assert all(
        len(script["_encode_payload"](payload)) <= script["upload_batch"].__globals__["BATCH_BYTES_LIMIT"]
        for payload in payloads
    )


def test_upload_batch_rejects_one_document_larger_than_request_limit():
    script = _load_script()
    script["upload_batch"].__globals__["BATCH_BYTES_LIMIT"] = 200

    with pytest.raises(ValueError, match="DOC-1.*5 MiB"):
        script["upload_batch"](
            api_base="http://api.test",
            token="token",
            source_system="oa",
            documents=[_document("DOC-1", metadata_size=500)],
        )


def test_upload_batch_counts_server_item_results_and_keeps_conflict_ids(monkeypatch):
    script = _load_script()

    def fake_post(_url, **_kwargs):
        return _response(
            [
                {"sourceDocumentId": "A", "jobId": "ij_a", "status": "accepted", "message": None},
                {"sourceDocumentId": "B", "jobId": "ij_b", "status": "exists", "message": None},
                {"sourceDocumentId": "C", "jobId": "ij_c", "status": "requeued", "message": None},
                {"sourceDocumentId": "D", "jobId": "ij_d", "status": "conflict", "message": "不同"},
            ]
        )

    monkeypatch.setattr(script["requests"], "post", fake_post)
    result = script["upload_batch"](
        api_base="http://api.test",
        token="token",
        source_system="oa",
        documents=[_document(item) for item in "ABCD"],
    )

    assert result["summary"] == {"accepted": 1, "exists": 1, "requeued": 1, "conflict": 1}
    assert result["conflictSourceDocumentIds"] == ["D"]


def test_upload_batch_can_be_rerun_after_committed_response_is_lost(monkeypatch):
    script = _load_script()
    stored = set()
    lose_first_response = True

    def fake_post(_url, **kwargs):
        nonlocal lose_first_response
        payload = json.loads(kwargs["data"])
        items = []
        for document in payload["documents"]:
            source_document_id = document["source_document_id"]
            status = "exists" if source_document_id in stored else "accepted"
            stored.add(source_document_id)
            items.append(
                {
                    "sourceDocumentId": source_document_id,
                    "jobId": f"ij_{source_document_id}",
                    "status": status,
                    "message": None,
                }
            )
        if lose_first_response:
            lose_first_response = False
            raise requests.ConnectionError("response lost after commit")
        return _response(items)

    monkeypatch.setattr(script["requests"], "post", fake_post)
    documents = [_document("DOC-1")]

    with pytest.raises(requests.ConnectionError):
        script["upload_batch"](api_base="http://api.test", token="token", source_system="oa", documents=documents)
    result = script["upload_batch"](api_base="http://api.test", token="token", source_system="oa", documents=documents)

    assert stored == {"DOC-1"}
    assert result["summary"] == {"accepted": 0, "exists": 1, "requeued": 0, "conflict": 0}


def test_main_exits_nonzero_and_prints_conflict_source_ids(monkeypatch, capsys):
    script = _load_script()
    documents_file = Path("documents.json")
    monkeypatch.setattr(Path, "read_text", lambda _self, **_kwargs: json.dumps([_document("DOC-1")]))
    script["main"].__globals__["upload_batch"] = lambda **_kwargs: {
        "items": [
            {
                "sourceDocumentId": "DOC-1",
                "jobId": "ij_1",
                "status": "conflict",
                "message": "来文输入与已有任务不一致",
            }
        ],
        "summary": {"accepted": 0, "exists": 0, "requeued": 0, "conflict": 1},
        "conflictSourceDocumentIds": ["DOC-1"],
    }
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            str(documents_file),
            "--token",
            "token",
            "--source-system",
            "oa",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        script["main"]()

    assert exc_info.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["conflictSourceDocumentIds"] == ["DOC-1"]
