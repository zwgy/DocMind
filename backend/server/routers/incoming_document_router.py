from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from server.utils.auth_middleware import get_admin_user, get_required_user
from yuxi.knowledge.utils import parse_minio_url
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.services.incoming_document_ingest_service import IncomingDocumentIngestService, IncomingKnowledgeImportConflict
from yuxi.services.incoming_document_service import IncomingDocumentService, IncomingPageFile
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES, read_upload_with_limit

incoming_documents = APIRouter(prefix="/incoming-documents", tags=["incoming-documents"])
INCOMING_MARKDOWN_PREVIEW_CHARS = 40_000


class IncomingExtractionQueryRequest(BaseModel):
    files: list[IncomingPageFile]


class IncomingKnowledgeImportRequest(BaseModel):
    kb_id: str = Field(alias="kbId")
    parent_id: str | None = Field(default=None, alias="parentId")
    params: dict | None = None

    model_config = {"populate_by_name": True}


@incoming_documents.post("/extractions/query")
async def query_incoming_document_extractions(
    payload: IncomingExtractionQueryRequest,
    current_user: User = Depends(get_required_user),
):
    del current_user
    # iframe 只能按当前页面附件线索查询摘要，不提供全局来文列表。
    return await IncomingDocumentService().query_extractions([item.model_dump(by_alias=True) for item in payload.files])


@incoming_documents.get("")
async def list_incoming_documents(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    knowledge_import_status: str | None = None,
    keyword: str | None = None,
    source_system: str | None = None,
    classification: str | None = None,
    current_user: User = Depends(get_admin_user),
):
    del current_user
    # 管理页列表只暴露来文处理状态和入库状态，知识库内容仍走知识库文件接口。
    items, total = await IncomingDocumentRepository().list_for_management(
        page=page,
        page_size=page_size,
        status=status,
        knowledge_import_status=knowledge_import_status,
        keyword=keyword,
        source_system=source_system,
        classification=classification,
    )
    return {"items": [_incoming_document_payload(item, detail=False) for item in items], "total": total}


@incoming_documents.get("/{incoming_id}")
async def get_incoming_document_detail(incoming_id: str, current_user: User = Depends(get_admin_user)):
    del current_user
    record = await IncomingDocumentRepository().get_by_incoming_id(incoming_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Incoming document not found: {incoming_id}")
    payload = _incoming_document_payload(record, detail=True)
    # 未入库来文没有知识库 file_id，只能在详情里提供解析 Markdown 的轻量预览。
    payload["markdownPreview"] = await _read_incoming_markdown_preview(record)
    return payload


@incoming_documents.post("/ingest")
async def ingest_incoming_document(request: Request, current_user: User = Depends(get_required_user)):
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            raise ValueError("multipart files is required")

        form = await request.form()
        # 外部系统直接传文件内容；原文长期存 MinIO，数据库只保存地址和来文元数据。
        uploads = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
        if not uploads:
            raise ValueError("files is required")
        file_metas = _parse_file_metas(form.get("file_metas"), len(uploads))
        files = []
        for upload, meta in zip(uploads, file_metas, strict=True):
            filename = str(meta.get("filename") or upload.filename or "").strip()
            source_file_id = str(meta.get("source_file_id") or "").strip()
            if not source_file_id:
                raise ValueError("source_file_id is required")
            if not filename:
                raise ValueError("filename is required")
            files.append(
                {
                    "source_file_id": source_file_id,
                    "filename": filename,
                    "content": await read_upload_with_limit(
                        upload,
                        max_size_bytes=MAX_UPLOAD_SIZE_BYTES,
                        too_large_message="文件过大，当前仅支持 100 MB 以内的文件",
                    ),
                }
            )
        return await IncomingDocumentIngestService().ingest_files(
            source_doc_id=str(form.get("source_doc_id") or "").strip(),
            document_number=str(form.get("document_number") or "").strip() or None,
            title=str(form.get("title") or "").strip() or None,
            incoming_type=str(form.get("incoming_type") or "").strip() or None,
            source_unit=str(form.get("source_unit") or "").strip() or None,
            incoming_date=str(form.get("incoming_date") or "").strip() or None,
            source_system=str(form.get("source_system") or "production").strip() or "production",
            files=files,
            operator_id=current_user.uid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@incoming_documents.post("/{incoming_id}/knowledge-import")
async def import_incoming_document_to_knowledge(
    incoming_id: str,
    payload: IncomingKnowledgeImportRequest,
    current_user: User = Depends(get_admin_user),
):
    try:
        # 人工确认后才把来文导入知识库，并复用知识库文件解析/索引链路。
        return await IncomingDocumentIngestService().import_to_knowledge(
            incoming_id,
            kb_id=payload.kb_id,
            parent_id=payload.parent_id,
            params=payload.params,
            operator_id=current_user.uid,
        )
    except IncomingKnowledgeImportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@incoming_documents.post("/{incoming_id}/retry")
async def retry_incoming_document_processing(incoming_id: str, current_user: User = Depends(get_admin_user)):
    try:
        # 重试只重跑来文解析摘要，不改变已经存在的知识库入库记录。
        return await IncomingDocumentIngestService().retry_processing(incoming_id, operator_id=current_user.uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _iso(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _parse_file_metas(raw_value, file_count: int) -> list[dict]:
    if not raw_value:
        raise ValueError("file_metas is required")
    try:
        metas = json.loads(str(raw_value))
    except json.JSONDecodeError as exc:
        raise ValueError("file_metas must be a JSON array with same length as files") from exc
    if not isinstance(metas, list) or len(metas) != file_count:
        raise ValueError("file_metas must be a JSON array with same length as files")
    if not all(isinstance(item, dict) for item in metas):
        raise ValueError("file_metas items must be JSON objects")
    return metas


def _incoming_document_payload(record, *, detail: bool) -> dict:
    payload = {
        "incomingId": record.incoming_id,
        "sourceSystem": record.source_system,
        "sourceDocumentId": record.source_document_id,
        "sourceFileId": getattr(record, "source_file_id", None),
        "sourceKey": getattr(record, "source_key", None),
        "filename": record.filename,
        "documentNumber": getattr(record, "document_number", None),
        "title": getattr(record, "title", None),
        "incomingType": getattr(record, "incoming_type", None),
        "sourceUnit": getattr(record, "source_unit", None),
        "incomingDate": getattr(record, "incoming_date", None),
        "isMainFile": bool(getattr(record, "is_main_file", False)),
        "fileSize": getattr(record, "file_size", None),
        "status": record.status,
        "classification": getattr(record, "classification", None),
        "classificationConfidence": getattr(record, "classification_confidence", None),
        "processingError": getattr(record, "processing_error", None),
        "linkedKbId": getattr(record, "linked_kb_id", None),
        "linkedFileId": getattr(record, "linked_file_id", None),
        "knowledgeImportStatus": getattr(record, "knowledge_import_status", None) or "none",
        "knowledgeImportTaskId": getattr(record, "knowledge_import_task_id", None),
        "knowledgeImportError": getattr(record, "knowledge_import_error", None),
        "createdAt": _iso(getattr(record, "created_at", None)),
        "updatedAt": _iso(getattr(record, "updated_at", None)),
    }
    if detail:
        payload.update(
            {
                "sourceUrl": getattr(record, "source_url", None),
                "contentHash": getattr(record, "content_hash", None),
                "originalFileUrl": getattr(record, "original_file_url", None),
                "markdownFileUrl": getattr(record, "markdown_file_url", None),
                "summary": getattr(record, "summary", None),
                "structuredResult": getattr(record, "structured_result", None) or {},
                "metadata": getattr(record, "metadata_json", None) or {},
            }
        )
    return payload


async def _read_incoming_markdown_preview(record) -> str:
    markdown_url = getattr(record, "markdown_file_url", None)
    if not markdown_url:
        return ""
    bucket_name, object_name = parse_minio_url(markdown_url)
    content = (await get_minio_client().adownload_file(bucket_name, object_name)).decode("utf-8", errors="replace")
    return content[:INCOMING_MARKDOWN_PREVIEW_CHARS]
