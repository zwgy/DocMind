from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from yuxi.document_extraction import BusinessExtractionService, classify_incoming_document
from yuxi.document_extraction.schemas import IncomingDocumentClassificationResult
from yuxi.knowledge.parser import Parser
from yuxi.knowledge.parser import is_supported_file_extension
from yuxi.knowledge.utils import calculate_content_hash
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.services.knowledge_document_ingest_service import KnowledgeDocumentIngestService
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.minio import MinIOClient, aupload_file_to_minio
from yuxi.utils import hashstr, logger
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES

UploadFileFn = Callable[..., Awaitable[dict[str, Any]]]
ParseDocumentFn = Callable[[str, dict[str, Any]], Awaitable[str]]
UploadMarkdownFn = Callable[..., Awaitable[str]]
ClassifyDocumentFn = Callable[..., Awaitable[dict[str, Any]]]
INCOMING_DOCUMENT_INGEST_TASK_TYPE = "incoming_document_ingest"
INCOMING_DOCUMENT_PROCESS_TASK_TYPE = "incoming_document_process"
INCOMING_ALLOWED_CONTENT_TYPES = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "text/html",
    "application/xhtml+xml",
    "application/octet-stream",
)


class IncomingKnowledgeImportConflict(ValueError):
    """来文入库任务已经在执行时抛出，路由层会映射为 409。"""

    pass


class IncomingDocumentIngestService:
    """来文接入编排：先独立保存和摘要，人工确认后再导入知识库。"""

    def __init__(
        self,
        *,
        incoming_repo: IncomingDocumentRepository | None = None,
        file_repo=None,
        knowledge=None,
        tasker=tasker,
        default_kb_id: str | None = None,
        upload_file: UploadFileFn | None = None,
        parse_document: ParseDocumentFn | None = None,
        upload_markdown: UploadMarkdownFn | None = None,
        classify_document: ClassifyDocumentFn | None = None,
        business_extraction_service: BusinessExtractionService | None = None,
    ):
        # 保留历史签名占位：file_repo / knowledge / default_kb_id 已迁移到 KnowledgeFile 体系
        del file_repo, knowledge, default_kb_id
        self.incoming_repo = incoming_repo or IncomingDocumentRepository()
        self.tasker = tasker
        self.upload_file = upload_file or _upload_incoming_file
        self.parse_document = parse_document or _parse_incoming_document
        self.upload_markdown = upload_markdown or _upload_incoming_markdown
        self.classify_document = classify_document or classify_incoming_document
        self.business_extraction_service = business_extraction_service or BusinessExtractionService()

    async def ingest_file(
        self,
        *,
        content: bytes,
        filename: str,
        source_function_id: str,
        source_file_id: str | None = None,
        source_url: str | None = None,
        source_doc_id: str | None = None,
        source_system: str = "production",
        document_number: str | None = None,
        title: str | None = None,
        incoming_type: str | None = None,
        source_unit: str | None = None,
        incoming_date: str | None = None,
        is_main_file: bool = False,
        source_size_text: str | None = None,
        file_size: int | None = None,
        content_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        del source_size_text
        # 校验文件名和扩展名：必须为解析器支持的标准类型
        if not filename or not is_supported_file_extension(filename):
            raise ValueError("Unsupported file type")
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise ValueError("文件大小不能超过 100 MB 上限")

        normalized_source_system = (source_system or "production").strip() or "production"
        normalized_source_function_id = (source_function_id or "").strip()
        normalized_source_file_id = (source_file_id or "").strip()
        source_document_id = (source_doc_id or "").strip()
        if not normalized_source_function_id:
            raise ValueError("source_function_id is required")
        if not source_document_id:
            raise ValueError("source_doc_id is required")
        if not normalized_source_file_id:
            raise ValueError("source_file_id is required")

        incoming_id = self._incoming_id(
            normalized_source_system,
            normalized_source_function_id,
            source_document_id,
            normalized_source_file_id,
        )
        existing = await self._get_existing_file(
            normalized_source_system,
            normalized_source_function_id,
            source_document_id,
            normalized_source_file_id,
        )
        if existing is not None and content_hash and getattr(existing, "content_hash", None) == content_hash:
            # 若已有记录且 hash 一致，直接复用已有 payload
            return self._existing_payload(existing)

        upload = await self.upload_file(
            source_system=normalized_source_system,
            incoming_id=incoming_id,
            filename=filename,
            content=content,
        )
        resolved_hash = content_hash or upload["content_hash"]
        if existing is not None and getattr(existing, "content_hash", None) == resolved_hash:
            # 若上传后 hash 与已有 hash 一致，直接复用已有记录
            return self._existing_payload(existing)

        # 写入数据库；新文档返回 accepted 给 chat-iframe
        record = await self.incoming_repo.upsert(
            incoming_id,
            {
                "source_system": normalized_source_system,
                "source_function_id": normalized_source_function_id,
                "source_document_id": source_document_id,
                "source_file_id": normalized_source_file_id,
                "source_url": source_url,
                "filename": Path(filename).name,
                "document_number": (document_number or "").strip() or None,
                "title": (title or "").strip() or None,
                "incoming_type": (incoming_type or "").strip() or None,
                "source_unit": (source_unit or "").strip() or None,
                "incoming_date": (incoming_date or "").strip() or None,
                "is_main_file": bool(is_main_file),
                "content_hash": resolved_hash,
                "file_size": int(file_size or upload["size"]),
                "original_file_url": upload["minio_url"],
                "markdown_file_url": None,
                "status": "uploaded",
                "classification": None,
                "classification_confidence": None,
                "summary": None,
                "structured_result": None,
                "processing_error": None,
                "linked_kb_id": None,
                "linked_file_id": None,
                "knowledge_import_status": "none",
                "knowledge_import_task_id": None,
                "knowledge_import_error": None,
                "metadata_json": self._metadata_with_document_fields(
                    metadata,
                    document_number=document_number,
                    title=title,
                    incoming_type=incoming_type,
                    source_unit=source_unit,
                    incoming_date=incoming_date,
                    source_function_id=normalized_source_function_id,
                    source_file_id=normalized_source_file_id,
                    is_main_file=is_main_file,
                ),
                "created_by": operator_id,
                "updated_by": operator_id,
            },
        )
        task = await self._submit_process_task(incoming_id=record.incoming_id, operator_id=operator_id)
        return {
            "incomingId": record.incoming_id,
            "taskId": task.id,
            "status": "accepted",
            "knowledgeImportStatus": "none",
        }

    async def ingest_files(
        self,
        *,
        source_doc_id: str,
        source_function_id: str,
        document_number: str | None = None,
        title: str | None = None,
        incoming_type: str | None = None,
        source_unit: str | None = None,
        incoming_date: str | None = None,
        source_system: str = "production",
        files: list[dict[str, Any]],
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        source_document_id = (source_doc_id or "").strip()
        if not source_document_id:
            raise ValueError("source_doc_id is required")
        normalized_source_function_id = (source_function_id or "").strip()
        if not normalized_source_function_id:
            raise ValueError("source_function_id is required")
        if not files:
            raise ValueError("files is required")

        items = []
        for index, item in enumerate(files):
            filename = str(item.get("filename") or "").strip()
            source_file_id = str(item.get("source_file_id") or "").strip()
            content = item.get("content")
            if not source_file_id:
                raise ValueError("source_file_id is required")
            if not filename:
                raise ValueError("filename is required")
            if not isinstance(content, bytes):
                raise ValueError("file content is required")
            is_main_file = _is_main_incoming_file(filename, document_number, is_first=index == 0)
            result = await self.ingest_file(
                content=content,
                filename=filename,
                source_function_id=normalized_source_function_id,
                source_file_id=source_file_id,
                source_doc_id=source_document_id,
                source_system=source_system,
                document_number=document_number,
                title=title,
                incoming_type=incoming_type,
                source_unit=source_unit,
                incoming_date=incoming_date,
                is_main_file=is_main_file,
                operator_id=operator_id,
            )
            items.append(
                {
                    "incomingId": result["incomingId"],
                    "taskId": result["taskId"],
                    "status": result["status"],
                    "source_file_id": source_file_id,
                    "filename": filename,
                    "is_main_file": is_main_file,
                    "knowledgeImportStatus": result["knowledgeImportStatus"],
                }
            )
        return {
            "status": "accepted",
            "source_function_id": normalized_source_function_id,
            "source_doc_id": source_document_id,
            "items": items,
        }

    async def ingest_source_url(
        self,
        *,
        source_url: str,
        filename: str,
        source_function_id: str,
        source_file_id: str,
        source_doc_id: str | None = None,
        source_system: str = "production",
        operator_id: str | None = None,
        **metadata,
    ) -> dict[str, Any]:
        normalized_source_system = (source_system or "production").strip() or "production"
        normalized_source_function_id = (source_function_id or "").strip()
        normalized_source_file_id = (source_file_id or "").strip()
        source_document_id = (source_doc_id or "").strip()
        if not normalized_source_function_id:
            raise ValueError("source_function_id is required")
        if not source_document_id:
            raise ValueError("source_doc_id is required")
        if not normalized_source_file_id:
            raise ValueError("source_file_id is required")
        incoming_id = self._incoming_id(
            normalized_source_system,
            normalized_source_function_id,
            source_document_id,
            normalized_source_file_id,
        )

        async def run_ingest(context: TaskContext):
            from yuxi.knowledge.utils.url_fetcher import fetch_url_content

            # 下载阶段已做 MIME 校验，此处无需重复
            content, _ = await fetch_url_content(
                source_url,
                max_size=MAX_UPLOAD_SIZE_BYTES,
                allowed_content_types=INCOMING_ALLOWED_CONTENT_TYPES,
            )
            return await self.ingest_file(
                content=content,
                filename=filename,
                source_function_id=normalized_source_function_id,
                source_file_id=normalized_source_file_id,
                source_url=source_url,
                source_doc_id=source_document_id,
                source_system=normalized_source_system,
                operator_id=operator_id,
                metadata=metadata or None,
            )

        task, _ = await self.tasker.enqueue_unique_by_payload(
            name=f"来文下载处理 ({normalized_source_file_id})",
            task_type=INCOMING_DOCUMENT_INGEST_TASK_TYPE,
            payload={
                "incoming_id": incoming_id,
                "source_system": normalized_source_system,
                "source_function_id": normalized_source_function_id,
                "source_file_id": normalized_source_file_id,
                "source_document_id": source_document_id,
                "source_url": source_url,
            },
            payload_match={"incoming_id": incoming_id},
            statuses={"pending", "running"},
            coroutine=run_ingest,
        )
        return {
            "incomingId": incoming_id,
            "taskId": task.id,
            "status": "accepted",
            "knowledgeImportStatus": "none",
        }

    async def import_to_knowledge(
        self,
        incoming_id: str,
        *,
        kb_id: str,
        parent_id: str | None = None,
        params: dict[str, Any] | None = None,
        operator_id: str | None = None,
        document_ingest_service: KnowledgeDocumentIngestService | None = None,
    ) -> dict[str, Any]:
        record = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if record is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")
        source = getattr(record, "original_file_url", None)
        if not source:
            raise ValueError("Incoming document original file is missing")

        # 若已存在导入任务则抛出冲突，路由层会映射为 409
        current_status = getattr(record, "knowledge_import_status", None) or "none"
        if current_status == "importing":
            raise IncomingKnowledgeImportConflict("Incoming document is already importing to knowledge base")
        if current_status == "indexed" and getattr(record, "linked_kb_id", None) and getattr(record, "linked_file_id", None):
            return {
                "incomingId": incoming_id,
                "status": "exists",
                "taskId": getattr(record, "knowledge_import_task_id", None),
                "knowledgeImportStatus": "indexed",
                "linkedKbId": getattr(record, "linked_kb_id", None),
                "linkedFileId": getattr(record, "linked_file_id", None),
            }

        ingest_params = dict(params or {})
        ingest_params["content_type"] = "file"
        ingest_params["auto_index"] = True
        if parent_id:
            ingest_params["parent_id"] = parent_id

        # 把源文件 hash 透传给下游，避免重复解析
        content_hashes = dict(ingest_params.get("content_hashes") or {})
        if getattr(record, "content_hash", None):
            content_hashes[source] = record.content_hash
        ingest_params["content_hashes"] = content_hashes

        file_sizes = dict(ingest_params.get("file_sizes") or {})
        if getattr(record, "file_size", None) is not None:
            file_sizes[source] = int(record.file_size)
        ingest_params["file_sizes"] = file_sizes

        source_paths = dict(ingest_params.get("source_paths") or {})
        source_paths.setdefault(source, _incoming_source_path(record))
        ingest_params["source_paths"] = source_paths

        document_ingest = document_ingest_service or KnowledgeDocumentIngestService()
        if hasattr(document_ingest, "ensure_database_supports_documents"):
            await document_ingest.ensure_database_supports_documents(kb_id, "来文存入知识库")

        async def on_success(result: dict[str, Any]) -> None:
            linked_file_id = _first_result_file_id(result)
            await self.incoming_repo.update_fields(
                incoming_id,
                {
                    "knowledge_import_status": "indexed",
                    "knowledge_import_error": None,
                    "linked_kb_id": kb_id,
                    "linked_file_id": linked_file_id,
                    "updated_by": operator_id,
                },
            )
            if linked_file_id:
                await self.business_extraction_service.link_knowledge_file(
                    incoming_id=incoming_id,
                    kb_id=kb_id,
                    file_id=linked_file_id,
                )

        async def on_failure(exc: Exception) -> None:
            await self.incoming_repo.update_fields(
                incoming_id,
                {
                    "knowledge_import_status": "failed",
                    "knowledge_import_error": str(exc),
                    "updated_by": operator_id,
                },
            )

        queued = await document_ingest.enqueue_ingest(
            kb_id=kb_id,
            items=[source],
            params=ingest_params,
            operator_id=operator_id,
            task_name=f"来文存入知识库({record.filename})",
            on_success=on_success,
            on_failure=on_failure,
        )
        await self.incoming_repo.update_fields(
            incoming_id,
            {
                "knowledge_import_status": "importing",
                "knowledge_import_task_id": queued["task_id"],
                "knowledge_import_error": None,
                "linked_kb_id": kb_id,
                "updated_by": operator_id,
            },
        )
        return {
            "incomingId": incoming_id,
            "status": "queued",
            "taskId": queued["task_id"],
            "knowledgeImportStatus": "importing",
            "linkedKbId": kb_id,
        }

    async def retry_processing(
        self,
        incoming_id: str,
        *,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        record = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if record is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")
        if not getattr(record, "original_file_url", None):
            raise ValueError("Incoming document original file is missing")

        # 重置处理状态，允许重新跑整个流程
        await self.incoming_repo.update_fields(
            incoming_id,
            {
                "status": "uploaded",
                "markdown_file_url": None,
                "classification": None,
                "classification_confidence": None,
                "summary": None,
                "structured_result": None,
                "processing_error": None,
                "updated_by": operator_id,
            },
        )
        task = await self._submit_process_task(incoming_id=incoming_id, operator_id=operator_id)
        return {"incomingId": incoming_id, "taskId": task.id, "status": "accepted"}

    async def _submit_process_task(self, *, incoming_id: str, operator_id: str | None):
        async def run_process(context: TaskContext):
            return await self.process_incoming_document(incoming_id, operator_id=operator_id, context=context)

        task, _ = await self.tasker.enqueue_unique_by_payload(
            name=f"来文处理 ({incoming_id})",
            task_type=INCOMING_DOCUMENT_PROCESS_TASK_TYPE,
            payload={"incoming_id": incoming_id},
            payload_match={"incoming_id": incoming_id},
            statuses={"pending", "running"},
            coroutine=run_process,
        )
        return task

    async def process_incoming_document(
        self,
        incoming_id: str,
        *,
        operator_id: str | None = None,
        context: TaskContext | None = None,
    ) -> dict[str, Any]:
        record = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if record is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")

        try:
            # 推进处理状态，便于 chat-iframe 实时展示进度

            await self.incoming_repo.update_fields(
                incoming_id,
                {"status": "parsing", "processing_error": None, "updated_by": operator_id},
            )
            markdown = await self.parse_document(
                record.original_file_url,
                {
                    # 图片统一存到 public 桶的 incoming 目录
                    "image_bucket": "public",
                    "image_prefix": f"incoming/{incoming_id}/images",
                },
            )
            markdown_url = await self.upload_markdown(incoming_id=incoming_id, markdown=markdown)

            await _set_progress(context, 70.0, "开始生成来文摘要")
            await self.incoming_repo.update_fields(
                incoming_id,
                {"status": "summarizing", "markdown_file_url": markdown_url, "updated_by": operator_id},
            )
            metadata = getattr(record, "metadata_json", None) or {}
            from yuxi.config.app import config

            raw_classification = await self.classify_document(
                filename=record.filename,
                markdown=markdown,
                metadata=metadata,
                model_spec=config.business_extraction_model or config.default_model,
            )
            classification_result = IncomingDocumentClassificationResult.model_validate(raw_classification)
            await self._run_business_extraction(
                incoming_id=incoming_id,
                filename=record.filename,
                markdown=markdown,
                markdown_url=markdown_url,
                classification=classification_result.classification,
                operator_id=operator_id,
            )
            await self.incoming_repo.update_fields(
                incoming_id,
                {
                    "status": "ready",
                    "classification": classification_result.classification,
                    "classification_confidence": classification_result.classification_confidence,
                    "summary": classification_result.summary,
                    "structured_result": classification_result.structured_result,
                    "processing_error": None,
                    "updated_by": operator_id,
                },
            )
            return {"incoming_id": incoming_id, "status": "ready"}
        except Exception as exc:
            await self.incoming_repo.update_fields(
                incoming_id,
                {"status": "failed", "processing_error": str(exc), "updated_by": operator_id},
            )
            raise

    async def _run_business_extraction(
        self,
        *,
        incoming_id: str,
        filename: str,
        markdown: str,
        markdown_url: str,
        operator_id: str | None,
        classification: str | None = None,
    ) -> None:
        try:
            from yuxi.config.app import config

            # 业务抽取是来文之外的独立增强能力，失败不能阻断摘要给 chat-iframe 使用。
            await self.business_extraction_service.run_markdown_extraction(
                document_scope="incoming",
                incoming_id=incoming_id,
                markdown_file=markdown_url,
                filename=filename,
                processing_params={"classification": classification},
                model_spec=config.business_extraction_model or config.default_model,
                operator_id=operator_id,
                markdown_reader=lambda _: markdown,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Incoming business extraction failed: incoming_id={incoming_id}: {exc}")

    @staticmethod
    def _incoming_id(
        source_system: str,
        source_function_id: str,
        source_document_id: str,
        source_file_id: str,
    ) -> str:
        identity = f"{source_system}:{source_function_id}:{source_document_id}:{source_file_id}"
        return f"inc_{hashstr(identity, 16)}"

    @staticmethod
    def _existing_payload(record) -> dict[str, Any]:
        return {
            "incomingId": record.incoming_id,
            "taskId": None,
            "status": "exists",
            "knowledgeImportStatus": getattr(record, "knowledge_import_status", None) or "none",
        }

    async def _get_existing_file(
        self,
        source_system: str,
        source_function_id: str,
        source_document_id: str,
        source_file_id: str,
    ):
        if hasattr(self.incoming_repo, "get_by_file_identity"):
            return await self.incoming_repo.get_by_file_identity(
                source_system,
                source_function_id,
                source_document_id,
                source_file_id,
            )
        return None

    @staticmethod
    def _metadata_with_document_fields(
        metadata: dict[str, Any] | None,
        *,
        document_number: str | None,
        title: str | None,
        incoming_type: str | None,
        source_unit: str | None,
        incoming_date: str | None,
        source_function_id: str,
        source_file_id: str,
        is_main_file: bool,
    ) -> dict[str, Any]:
        merged = dict(metadata or {})
        for key, value in {
            "document_number": document_number,
            "title": title,
            "incoming_type": incoming_type,
            "source_unit": source_unit,
            "incoming_date": incoming_date,
            "source_function_id": source_function_id,
            "source_file_id": source_file_id,
            "is_main_file": is_main_file,
        }.items():
            if value not in (None, ""):
                merged[key] = value
        return merged


async def _upload_incoming_file(*, source_system: str, incoming_id: str, filename: str, content: bytes) -> dict[str, Any]:
    safe_name = Path(filename).name
    if not safe_name or not is_supported_file_extension(safe_name):
        raise ValueError("Unsupported file type")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError("文件大小不能超过 100 MB 上限")
    content_hash = await calculate_content_hash(content)
    suffix = Path(safe_name).suffix.lower()
    stem = Path(safe_name).stem or "incoming"
    safe_source_system = _safe_object_segment(source_system)
    object_name = f"incoming/{safe_source_system}/{incoming_id}/{stem}_{int(time.time() * 1000)}{suffix}"
    minio_url = await aupload_file_to_minio(MinIOClient.KB_BUCKETS["documents"], object_name, content)
    return {"minio_url": minio_url, "content_hash": content_hash, "size": len(content)}


async def _parse_incoming_document(source: str, params: dict[str, Any]) -> str:
    return await Parser.aparse(source=source, params=params)


async def _upload_incoming_markdown(*, incoming_id: str, markdown: str) -> str:
    object_name = f"incoming/{incoming_id}/parsed.md"
    return await aupload_file_to_minio(MinIOClient.KB_BUCKETS["parsed"], object_name, markdown.encode("utf-8"))


async def _set_progress(context: TaskContext | None, percent: float, message: str) -> None:
    if context is not None:
        await context.set_progress(percent, message)


def _safe_object_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (value or "").strip())
    return cleaned or "production"


def _is_main_incoming_file(filename: str, document_number: str | None, *, is_first: bool = False) -> bool:
    number = (document_number or "").strip()
    if not number:
        return is_first
    safe_name = Path(filename).name.strip()
    return safe_name == number or Path(safe_name).stem == number


def _incoming_source_path(record) -> str:
    classification = (getattr(record, "classification", None) or "uncategorized").strip() or "uncategorized"
    filename = Path(getattr(record, "filename", None) or "incoming").name
    return f"incoming/{_safe_object_segment(classification)}/{filename}"


def _first_result_file_id(result: dict[str, Any]) -> str | None:
    for item in result.get("items") or []:
        if item.get("status") == "failed" or item.get("error"):
            continue
        file_id = item.get("file_id")
        if file_id:
            return file_id
        file_meta = item.get("file_meta")
        if isinstance(file_meta, dict) and file_meta.get("file_id"):
            return file_meta["file_id"]
    return None
