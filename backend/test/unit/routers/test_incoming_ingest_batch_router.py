from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from server.routers import incoming_document_router


def _document(source_document_id: str = "DOC-1") -> dict:
    return {
        "source_document_id": source_document_id,
        "metadata": {"title": "历史来文", "incoming_date": "2026-09-08"},
        "files": [
            {
                "source_file_id": "main",
                "filename": "main.pdf",
                "source_url": f"https://attachments.test/{source_document_id}.pdf",
                "is_main_file": True,
            }
        ],
    }


@pytest.mark.parametrize("documents", [[], [_document(str(index)) for index in range(201)]])
def test_historical_ingest_requires_one_to_two_hundred_documents(documents):
    with pytest.raises(ValidationError):
        incoming_document_router.HistoricalIncomingIngestRequest(source_system="oa", documents=documents)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({**_document(), "files": []}, "files"),
        (
            {
                **_document(),
                "files": [
                    *_document()["files"],
                    {**_document()["files"][0], "filename": "copy.pdf"},
                ],
            },
            "source_file_id",
        ),
        (
            {
                **_document(),
                "files": [
                    *_document()["files"],
                    {
                        **_document()["files"][0],
                        "source_file_id": "other",
                        "is_main_file": True,
                    },
                ],
            },
            "main file",
        ),
        ({**_document(), "files": [{**_document()["files"][0], "filename": "malware.exe"}]}, "file type"),
        ({**_document(), "metadata": {"incoming_date": "2026-02-30"}}, "YYYY-MM-DD"),
        (
            {
                **_document(),
                "files": [{**_document()["files"][0], "source_url": "file:///tmp/main.pdf"}],
            },
            "absolute HTTP or HTTPS",
        ),
        (
            {
                **_document(),
                "files": [
                    {
                        **_document()["files"][0],
                        "source_url": "https://user:secret@attachments.test/main.pdf",
                    }
                ],
            },
            "credentials",
        ),
    ],
)
def test_historical_ingest_rejects_invalid_document_before_registration(document, message):
    with pytest.raises(ValidationError, match=message):
        incoming_document_router.HistoricalIncomingIngestRequest(source_system="oa", documents=[document])


def test_historical_ingest_rejects_duplicate_document_identity():
    with pytest.raises(ValidationError, match="source_document_id"):
        incoming_document_router.HistoricalIncomingIngestRequest(
            source_system="oa",
            documents=[_document(), _document()],
        )


async def test_historical_ingest_registers_validated_documents_directly(monkeypatch):
    captured = {}

    class FakeIncomingDocumentService:
        async def register_historical_ingest(self, **kwargs):
            captured.update(kwargs)
            return {
                "items": [{"sourceDocumentId": "DOC-1", "jobId": "ij_1", "status": "accepted", "message": None}],
                "summary": {"accepted": 1, "exists": 0, "requeued": 0, "conflict": 0},
            }

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentService", FakeIncomingDocumentService)
    payload = incoming_document_router.HistoricalIncomingIngestRequest(source_system="oa", documents=[_document()])

    result = await incoming_document_router.register_historical_incoming_documents(
        payload,
        current_user=SimpleNamespace(uid="admin-1"),
    )

    assert result["summary"] == {"accepted": 1, "exists": 0, "requeued": 0, "conflict": 0}
    assert captured == {
        "source_system": "oa",
        "documents": [
            {
                "source_document_id": "DOC-1",
                "document_metadata": {
                    "source_doc_id": "DOC-1",
                    "title": "历史来文",
                    "incoming_date": "2026-09-08",
                },
                "file_manifest": _document()["files"],
            }
        ],
        "actor_uid": "admin-1",
    }


async def test_historical_ingest_rejects_request_larger_than_five_mib_before_registration(monkeypatch):
    class FakeIncomingDocumentService:
        def __init__(self):
            raise AssertionError("超限请求不应进入服务层")

    monkeypatch.setattr(incoming_document_router, "IncomingDocumentService", FakeIncomingDocumentService)
    document = _document()
    document["metadata"]["payload"] = "x" * incoming_document_router.INCOMING_INGEST_BATCH_BYTES_LIMIT
    payload = incoming_document_router.HistoricalIncomingIngestRequest(source_system="oa", documents=[document])

    with pytest.raises(HTTPException, match="5 MiB"):
        await incoming_document_router.register_historical_incoming_documents(
            payload,
            current_user=SimpleNamespace(uid="admin-1"),
        )


def test_only_single_stage_historical_ingest_route_remains():
    routes = {route.path for route in incoming_document_router.incoming_documents.routes}

    assert "/incoming-documents/ingest-batches" in routes
    assert "/incoming-documents/ingest-batches/{batch_id}/items" not in routes
    assert "/incoming-documents/ingest-batches/{batch_id}/submit" not in routes
    assert "/incoming-documents/ingest-batches/{batch_id}/pause" not in routes
