from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from server.utils.auth_middleware import get_required_user
from yuxi.services.incoming_document_ingest_service import IncomingDocumentIngestService
from yuxi.services.incoming_document_service import IncomingDocumentService, IncomingPageFile
from yuxi.storage.postgres.models_business import User
from yuxi.utils.upload_utils import read_upload_with_limit

incoming_documents = APIRouter(prefix="/incoming-documents", tags=["incoming-documents"])


class IncomingExtractionQueryRequest(BaseModel):
    files: list[IncomingPageFile]


class IncomingIngestJsonRequest(BaseModel):
    source_url: str = Field(alias="sourceUrl")
    source_key: str = Field(alias="sourceKey")
    filename: str
    source_doc_id: str | None = Field(default=None, alias="sourceDocId")
    source_system: str = Field(default="production", alias="sourceSystem")
    metadata: dict | None = None

    model_config = {"populate_by_name": True}


@incoming_documents.post("/extractions/query")
async def query_incoming_document_extractions(
    payload: IncomingExtractionQueryRequest,
    current_user: User = Depends(get_required_user),
):
    del current_user
    return await IncomingDocumentService().query_extractions([item.model_dump(by_alias=True) for item in payload.files])


@incoming_documents.post("/ingest")
async def ingest_incoming_document(request: Request, current_user: User = Depends(get_required_user)):
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = IncomingIngestJsonRequest.model_validate(await request.json())
            return await IncomingDocumentIngestService().ingest_source_url(
                source_url=body.source_url,
                filename=body.filename,
                source_key=body.source_key,
                source_system=body.source_system,
                source_doc_id=body.source_doc_id,
                operator_id=current_user.uid,
                **(body.metadata or {}),
            )

        form = await request.form()
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise ValueError("file is required")
        source_key = str(form.get("sourceKey") or "").strip()
        if not source_key:
            raise ValueError("sourceKey is required")
        safe_filename = str(form.get("filename") or file.filename or "")
        if not safe_filename:
            raise ValueError("filename is required")
        metadata_obj = json.loads(str(form.get("metadata"))) if form.get("metadata") else None
        if metadata_obj is not None and not isinstance(metadata_obj, dict):
            raise ValueError("metadata must be a JSON object")
        content = await read_upload_with_limit(file, too_large_message="文件过大，当前仅支持 100 MB 以内的文件")
        return await IncomingDocumentIngestService().ingest_file(
            content=content,
            filename=safe_filename,
            source_key=source_key,
            source_url=str(form.get("sourceUrl") or "") or None,
            source_doc_id=str(form.get("sourceDocId") or "") or None,
            source_system=str(form.get("sourceSystem") or "production"),
            source_size_text=str(form.get("sourceSizeText") or "") or None,
            file_size=int(form["fileSize"]) if form.get("fileSize") else None,
            content_hash=str(form.get("contentHash") or "") or None,
            metadata=metadata_obj,
            operator_id=current_user.uid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
