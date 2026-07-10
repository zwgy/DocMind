from __future__ import annotations

import io
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile
from yuxi.document_extraction.schemas import extraction_schema_display_metadata
from yuxi.knowledge.utils import parse_minio_url
from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.services.file_preview import (
    MAX_BINARY_PREVIEW_SIZE_BYTES,
    convert_office_to_pdf,
    detect_media_type,
    detect_preview_type,
    is_binary_preview_type,
    is_office_pdf_preview_file,
    render_preview_payload,
    render_preview_too_large_payload,
)
from yuxi.services.incoming_document_ingest_service import (
    IncomingDocumentIngestService,
    IncomingKnowledgeImportConflict,
)
from yuxi.services.incoming_document_service import IncomingDocumentService, IncomingPageFile
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES, read_upload_with_limit

from server.utils.auth_middleware import get_admin_user, get_required_user

incoming_documents = APIRouter(prefix="/incoming-documents", tags=["incoming-documents"])
INCOMING_MARKDOWN_PREVIEW_CHARS = 40_000


class IncomingExtractionQueryRequest(BaseModel):
    files: list[IncomingPageFile]


class IncomingIngestFileMeta(BaseModel):
    """multipart 字段 file_metas 的单个文件元数据。"""

    source_file_id: str
    filename: str


class IncomingIngestMultipartFields(BaseModel):
    """multipart 非文件字段；二进制文件通过重复的 files 字段传入。"""

    source_doc_id: str
    source_function_id: str
    document_number: str | None = None
    title: str | None = None
    incoming_type: str | None = None
    source_unit: str | None = None
    incoming_date: str | None = None
    source_system: str = "production"


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
    business_extraction = await DocumentBusinessExtractionRepository().get_latest_by_incoming_id(incoming_id)
    payload["businessExtraction"] = _business_extraction_payload(business_extraction)
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
        fields = IncomingIngestMultipartFields(
            source_doc_id=str(form.get("source_doc_id") or "").strip(),
            source_function_id=str(form.get("source_function_id") or "").strip(),
            document_number=_optional_form_text(form.get("document_number")),
            title=_optional_form_text(form.get("title")),
            incoming_type=_optional_form_text(form.get("incoming_type")),
            source_unit=_optional_form_text(form.get("source_unit")),
            incoming_date=_optional_form_text(form.get("incoming_date")),
            source_system=str(form.get("source_system") or "production").strip() or "production",
        )
        file_metas = _parse_file_metas(form.get("file_metas"), len(uploads))
        files = []
        for upload, meta in zip(uploads, file_metas, strict=True):
            filename = meta.filename.strip() or str(upload.filename or "").strip()
            source_file_id = meta.source_file_id.strip()
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
            source_doc_id=fields.source_doc_id,
            source_function_id=fields.source_function_id,
            document_number=fields.document_number,
            title=fields.title,
            incoming_type=fields.incoming_type,
            source_unit=fields.source_unit,
            incoming_date=fields.incoming_date,
            source_system=fields.source_system,
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


@incoming_documents.get("/{incoming_id}/file/original")
async def get_incoming_document_original_file(incoming_id: str, current_user: User = Depends(get_admin_user)):
    """预览来文原文（MinIO 上的原始文件），不依赖知识库入库状态。"""
    del current_user
    record = await IncomingDocumentRepository().get_by_incoming_id(incoming_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Incoming document not found: {incoming_id}")
    original_url = getattr(record, "original_file_url", None)
    if not original_url:
        raise HTTPException(status_code=404, detail="原文文件尚未上传")

    filename = record.filename or "incoming"
    try:
        bucket_name, object_name = parse_minio_url(original_url)
        content = await get_minio_client().adownload_file(bucket_name, object_name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=f"原文文件不存在: {exc}") from exc

    if len(content) > MAX_BINARY_PREVIEW_SIZE_BYTES:
        # 超出二进制预览上限时复用 file_preview 提供的标准化超大响应。
        return render_preview_too_large_payload()

    # .docx/.pptx 与知识库一致先转换为 PDF，便于浏览器内嵌预览。
    if is_office_pdf_preview_file(filename):
        pdf_bytes = await convert_office_to_pdf(filename, content)
        return _stream_incoming_binary(
            filename=f"{(filename.rsplit('.', 1)[0] or 'preview')}.pdf",
            content=pdf_bytes,
            media_type="application/pdf",
            preview_type="pdf",
        )

    preview_type, supported, message = detect_preview_type(filename, content)
    if not supported:
        # 二进制格式无法预览（zip/exe 等）— 返回受支持的预览元信息，前端据此渲染"暂不支持"提示。
        return {
            "content": None,
            "preview_type": preview_type,
            "supported": False,
            "message": message,
            "truncated": False,
            "limit": None,
        }

    if is_binary_preview_type(preview_type):
        # 图片 / PDF 等可由浏览器直接渲染，按二进制流返回。
        return _stream_incoming_binary(
            filename=filename,
            content=content,
            media_type=detect_media_type(filename, content),
            preview_type=preview_type,
        )

    # 文本类文件（txt、md、代码等）— 返回 JSON，前端 AgentFilePreview 走 content 分支展示。
    return render_preview_payload(filename, content)


def _iso(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _optional_form_text(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_file_metas(raw_value, file_count: int) -> list[IncomingIngestFileMeta]:
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
    return [IncomingIngestFileMeta.model_validate(item) for item in metas]


def _incoming_document_payload(record, *, detail: bool) -> dict:
    payload = {
        "incomingId": record.incoming_id,
        "sourceSystem": record.source_system,
        "sourceFunctionId": getattr(record, "source_function_id", None),
        "sourceDocumentId": record.source_document_id,
        "sourceFileId": getattr(record, "source_file_id", None),
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


def _business_extraction_payload(extraction: dict | None) -> dict | None:
    if not extraction:
        return None
    schema_ids = extraction.get("schema_ids") or []
    return {
        "runId": extraction.get("run_id"),
        "categories": extraction.get("categories") or {},
        "schemaIds": schema_ids,
        "items": extraction.get("items") or [],
        # 后端统一透出展示标签，避免前端重复维护抽取 schema 的中文名。
        "display": extraction_schema_display_metadata(schema_ids),
    }


async def _read_incoming_markdown_preview(record) -> str:
    markdown_url = getattr(record, "markdown_file_url", None)
    if not markdown_url:
        return ""
    bucket_name, object_name = parse_minio_url(markdown_url)
    content = (await get_minio_client().adownload_file(bucket_name, object_name)).decode("utf-8", errors="replace")
    return content[:INCOMING_MARKDOWN_PREVIEW_CHARS]


def _stream_incoming_binary(*, filename: str, content: bytes, media_type: str, preview_type: str) -> StreamingResponse:
    """统一构造来文文件二进制响应，沿用知识库预览的头部约定以便前端 AgentFilePreview 复用。"""
    quoted = quote(filename or "preview", safe="")
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quoted}",
            "X-Yuxi-Preview-Type": preview_type,
            "X-Yuxi-Preview-Filename": quoted,
        },
    )
