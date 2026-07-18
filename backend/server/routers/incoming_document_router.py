from __future__ import annotations

import io
import json
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile
from yuxi.document_extraction.schemas import (
    document_category_label_mapping,
    extraction_schema_display_metadata,
)
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
from yuxi.services.incoming_document_markdown_service import IncomingDocumentMarkdownService
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
    is_main_file: bool | None = None


class IncomingIngestMultipartFields(BaseModel):
    """multipart 非文件字段；二进制文件通过重复的 files 字段传入。"""

    source_doc_id: str
    source_function_id: str
    source_system: str = "production"


class IncomingClassificationRequest(BaseModel):
    classification: str


class IncomingKnowledgeImportRequest(BaseModel):
    kb_id: str = Field(alias="kbId")
    parent_id: str | None = Field(default=None, alias="parentId")
    source_file_ids: list[str] | None = Field(default=None, alias="sourceFileIds")
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


@incoming_documents.get("/options")
async def get_incoming_document_options(current_user: User = Depends(get_admin_user)):
    del current_user
    return {
        "classifications": document_category_label_mapping(),
        "display": extraction_schema_display_metadata(),
    }


@incoming_documents.get("/{incoming_id}")
async def get_incoming_document_detail(incoming_id: str, current_user: User = Depends(get_admin_user)):
    del current_user
    record = await IncomingDocumentRepository().get_by_incoming_id(incoming_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Incoming document not found: {incoming_id}")
    payload = _incoming_document_payload(record, detail=True)
    files = await IncomingDocumentRepository().list_files(incoming_id)
    payload["files"] = [_incoming_file_payload(file) for file in files]
    business_extraction = (
        await DocumentBusinessExtractionRepository().get_latest_by_incoming_id(incoming_id)
        if record.status == "ready"
        else None
    )
    payload["businessExtraction"] = _business_extraction_payload(business_extraction)
    return payload


@incoming_documents.put("/{incoming_id}/classification")
async def correct_incoming_document_classification(
    incoming_id: str,
    payload: IncomingClassificationRequest,
    current_user: User = Depends(get_admin_user),
):
    try:
        return await IncomingDocumentIngestService().correct_classification(
            incoming_id, classification=payload.classification, operator_id=current_user.uid
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@incoming_documents.post("/{incoming_id}/confirm")
async def confirm_incoming_document(incoming_id: str, current_user: User = Depends(get_admin_user)):
    try:
        return await IncomingDocumentIngestService().confirm_document(incoming_id, operator_id=current_user.uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
            source_system=str(form.get("source_system") or "production").strip() or "production",
        )
        document_metadata = _parse_document_metadata(form.get("document_metadata"))
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
                    "is_main_file": meta.is_main_file,
                    "content": await read_upload_with_limit(
                        upload,
                        max_size_bytes=MAX_UPLOAD_SIZE_BYTES,
                        too_large_message="文件过大，当前仅支持 100 MB 以内的文件",
                    ),
                }
            )
        result = await IncomingDocumentIngestService().ingest_files(
            source_doc_id=fields.source_doc_id,
            source_function_id=fields.source_function_id,
            document_metadata=document_metadata,
            source_system=fields.source_system,
            files=files,
            operator_id=current_user.uid,
        )
        return result | {"fileCount": len(files)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@incoming_documents.post("/{incoming_id}/knowledge-import")
async def import_incoming_document_to_knowledge(
    incoming_id: str,
    payload: IncomingKnowledgeImportRequest,
    current_user: User = Depends(get_admin_user),
):
    try:
        # 不传附件 ID 时默认导入整份来文；显式传入时仅导入选中的附件。
        return await IncomingDocumentIngestService().import_to_knowledge(
            incoming_id,
            kb_id=payload.kb_id,
            parent_id=payload.parent_id,
            source_file_ids=payload.source_file_ids,
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
async def get_incoming_document_original_file(
    incoming_id: str,
    source_file_id: str | None = None,
    current_user: User = Depends(get_admin_user),
):
    """预览来文原文（MinIO 上的原始文件），不依赖知识库入库状态。"""
    del current_user
    record = await IncomingDocumentRepository().get_by_incoming_id(incoming_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Incoming document not found: {incoming_id}")
    files = await IncomingDocumentRepository().list_files(incoming_id)
    if source_file_id:
        file = next((item for item in files if item.source_file_id == source_file_id), None)
        if file is None:
            raise HTTPException(status_code=404, detail="来文附件不存在")
    else:
        file = next((item for item in files if item.is_main_file), None)
    original_url = getattr(file, "original_file_url", None)
    if not original_url:
        raise HTTPException(status_code=404, detail="原文文件尚未上传")

    filename = getattr(file, "filename", None) or "incoming"
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


@incoming_documents.get("/{incoming_id}/file/markdown")
async def get_incoming_document_markdown(
    incoming_id: str,
    source_file_id: str,
    current_user: User = Depends(get_admin_user),
):
    del current_user
    files = await IncomingDocumentRepository().list_files(incoming_id)
    file = next((item for item in files if item.source_file_id == source_file_id), None)
    if file is None or not file.markdown_file_url:
        raise HTTPException(status_code=404, detail="附件 Markdown 尚未生成")
    content = await IncomingDocumentMarkdownService.download_text(file.markdown_file_url)
    return {
        "content": content[:INCOMING_MARKDOWN_PREVIEW_CHARS],
        "truncated": len(content) > INCOMING_MARKDOWN_PREVIEW_CHARS,
        "limit": INCOMING_MARKDOWN_PREVIEW_CHARS,
    }


def _iso(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


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


def _parse_document_metadata(raw_value) -> dict:
    if raw_value is None:
        raise ValueError("document_metadata is required")
    try:
        metadata = json.loads(str(raw_value))
    except json.JSONDecodeError as exc:
        raise ValueError("document_metadata must be a JSON object") from exc
    if not isinstance(metadata, dict):
        raise ValueError("document_metadata must be a JSON object")
    incoming_date = metadata.get("incoming_date")
    if incoming_date is not None:
        try:
            if not isinstance(incoming_date, str) or date.fromisoformat(incoming_date).isoformat() != incoming_date:
                raise ValueError
        except ValueError as exc:
            raise ValueError("document_metadata.incoming_date must be YYYY-MM-DD") from exc
    return metadata


def _incoming_document_payload(record, *, detail: bool) -> dict:
    metadata = getattr(record, "document_metadata", None) or {}
    effective_classification = getattr(record, "confirmed_classification", None) or getattr(
        record, "ai_classification", None
    )
    payload = {
        "incomingId": record.incoming_id,
        "sourceSystem": record.source_system,
        "sourceFunctionId": getattr(record, "source_function_id", None),
        "sourceDocumentId": record.source_document_id,
        "documentMetadata": metadata,
        "documentNumber": metadata.get("document_number"),
        "title": metadata.get("title"),
        "incomingType": metadata.get("incoming_type"),
        "sourceUnit": metadata.get("source_unit"),
        "incomingDate": metadata.get("incoming_date"),
        "status": record.status,
        "aiClassification": getattr(record, "ai_classification", None),
        "confirmedClassification": getattr(record, "confirmed_classification", None),
        "effectiveClassification": effective_classification,
        "classificationConfidence": getattr(record, "classification_confidence", None),
        "aiClassificationEvidence": getattr(record, "classification_evidence", None),
        "additionalClassifications": getattr(record, "additional_classifications", None) or [],
        "reviewStatus": getattr(record, "review_status", None) or "draft",
        "confirmedBy": getattr(record, "confirmed_by", None),
        "confirmedAt": _iso(getattr(record, "confirmed_at", None)),
        "processingError": getattr(record, "processing_error", None),
        "linkedKbId": getattr(record, "linked_kb_id", None),
        "knowledgeImportStatus": getattr(record, "knowledge_import_status", None) or "none",
        "knowledgeImportTaskId": getattr(record, "knowledge_import_task_id", None),
        "knowledgeImportError": getattr(record, "knowledge_import_error", None),
        "createdAt": _iso(getattr(record, "created_at", None)),
        "updatedAt": _iso(getattr(record, "updated_at", None)),
    }
    if detail:
        payload.update(
            {
                "summary": getattr(record, "summary", None),
            }
        )
    return payload


def _incoming_file_payload(file) -> dict:
    return {
        "incomingFileId": file.incoming_file_id,
        "sourceFileId": file.source_file_id,
        "filename": file.filename,
        "isMainFile": file.is_main_file,
        "fileSize": file.file_size,
        "status": file.status,
        "processingError": file.processing_error,
        "hasOriginalFile": bool(file.original_file_url),
        "hasMarkdownFile": bool(file.markdown_file_url),
        "linkedFileId": file.linked_file_id,
        "knowledgeImportStatus": file.knowledge_import_status or "none",
        "knowledgeImportError": file.knowledge_import_error,
    }


def _business_extraction_payload(extraction: dict | None) -> dict | None:
    if not extraction:
        return None
    schema_ids = extraction.get("schema_ids") or []
    items = extraction.get("items") or []
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item.get("item_type") or "unknown", []).append(item)
    return {
        "runId": extraction.get("run_id"),
        "categories": extraction.get("categories") or {},
        "schemaIds": schema_ids,
        "items": items,
        "groups": [
            {
                "itemType": item_type,
                "summary": _extraction_group_summary(group_items),
                "details": group_items,
            }
            for item_type, group_items in groups.items()
        ],
        # 后端统一透出展示标签，避免前端重复维护抽取 schema 的中文名。
        "display": extraction_schema_display_metadata(schema_ids),
    }


def _extraction_group_summary(items: list[dict]) -> str:
    if not items:
        return "暂无抽取结果"
    data = items[0].get("data") or {}
    first_value = next(
        (str(value) for key, value in data.items() if key != "source_quote" and value not in (None, "", [], {})),
        "已提取业务事项",
    )
    return first_value if len(items) == 1 else f"{first_value}等 {len(items)} 条事项"


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
