from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from yuxi.knowledge.parser import is_supported_file_extension
from yuxi.knowledge.utils import calculate_content_hash
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.minio import MinIOClient, aupload_file_to_minio
from yuxi.utils import hashstr
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES

UploadFileFn = Callable[..., Awaitable[dict[str, Any]]]
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


class IncomingDocumentIngestService:
    def __init__(
        self,
        *,
        incoming_repo: IncomingDocumentRepository | None = None,
        file_repo=None,
        knowledge=None,
        tasker=tasker,
        default_kb_id: str | None = None,
        upload_file: UploadFileFn | None = None,
    ):
        # file_repo/knowledge/default_kb_id 仅保留构造兼容，阶段一开始不再写 KnowledgeFile。
        del file_repo, knowledge, default_kb_id
        self.incoming_repo = incoming_repo or IncomingDocumentRepository()
        self.tasker = tasker
        self.upload_file = upload_file or _upload_incoming_file

    async def ingest_file(
        self,
        *,
        content: bytes,
        filename: str,
        source_key: str,
        source_url: str | None = None,
        source_doc_id: str | None = None,
        source_system: str = "production",
        source_size_text: str | None = None,
        file_size: int | None = None,
        content_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        del source_size_text
        if not filename or not is_supported_file_extension(filename):
            raise ValueError("Unsupported file type")
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise ValueError("文件过大，当前仅支持 100 MB 以内的文件")

        normalized_source_system = (source_system or "production").strip() or "production"
        normalized_source_key = (source_key or "").strip()
        source_document_id = (source_doc_id or normalized_source_key).strip()
        if not source_document_id:
            raise ValueError("sourceKey is required")

        incoming_id = self._incoming_id(normalized_source_system, source_document_id)
        existing = await self.incoming_repo.get_by_source_identity(normalized_source_system, source_document_id)
        if existing is not None and content_hash and getattr(existing, "content_hash", None) == content_hash:
            return self._existing_payload(existing)

        upload = await self.upload_file(
            source_system=normalized_source_system,
            incoming_id=incoming_id,
            filename=filename,
            content=content,
        )
        resolved_hash = content_hash or upload["content_hash"]
        if existing is not None and getattr(existing, "content_hash", None) == resolved_hash:
            return self._existing_payload(existing)

        # 重新上传同一外部单号时清空派生结果，避免旧摘要误导 chat-iframe。
        record = await self.incoming_repo.upsert(
            incoming_id,
            {
                "source_system": normalized_source_system,
                "source_document_id": source_document_id,
                "source_key": normalized_source_key,
                "source_url": source_url,
                "filename": Path(filename).name,
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
                "metadata_json": metadata or {},
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

    async def ingest_source_url(
        self,
        *,
        source_url: str,
        filename: str,
        source_key: str,
        source_doc_id: str | None = None,
        source_system: str = "production",
        operator_id: str | None = None,
        **metadata,
    ) -> dict[str, Any]:
        normalized_source_system = (source_system or "production").strip() or "production"
        normalized_source_key = (source_key or "").strip()
        source_document_id = (source_doc_id or normalized_source_key).strip()
        if not source_document_id:
            raise ValueError("sourceKey is required")
        incoming_id = self._incoming_id(normalized_source_system, source_document_id)

        async def run_ingest(context: TaskContext):
            from yuxi.knowledge.utils.url_fetcher import fetch_url_content

            await context.set_progress(5.0, "准备下载来文")
            # 只在来文链路放开文档 MIME，避免影响网页抓取默认白名单。
            content, _ = await fetch_url_content(
                source_url,
                max_size=MAX_UPLOAD_SIZE_BYTES,
                allowed_content_types=INCOMING_ALLOWED_CONTENT_TYPES,
            )
            return await self.ingest_file(
                content=content,
                filename=filename,
                source_key=normalized_source_key,
                source_url=source_url,
                source_doc_id=source_document_id,
                source_system=normalized_source_system,
                operator_id=operator_id,
                metadata=metadata or None,
            )

        task, _ = await self.tasker.enqueue_unique_by_payload(
            name=f"来文下载处理 ({normalized_source_key})",
            task_type=INCOMING_DOCUMENT_INGEST_TASK_TYPE,
            payload={
                "incoming_id": incoming_id,
                "source_system": normalized_source_system,
                "source_key": normalized_source_key,
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

    async def _submit_process_task(self, *, incoming_id: str, operator_id: str | None):
        async def run_process(context: TaskContext):
            del operator_id
            # 阶段一只负责解耦和落库；解析、摘要、分类在阶段二接入。
            await context.set_progress(100.0, "来文已保存，等待解析摘要")
            return {"incoming_id": incoming_id, "status": "uploaded"}

        task, _ = await self.tasker.enqueue_unique_by_payload(
            name=f"来文处理 ({incoming_id})",
            task_type=INCOMING_DOCUMENT_PROCESS_TASK_TYPE,
            payload={"incoming_id": incoming_id},
            payload_match={"incoming_id": incoming_id},
            statuses={"pending", "running"},
            coroutine=run_process,
        )
        return task

    @staticmethod
    def _incoming_id(source_system: str, source_document_id: str) -> str:
        return f"inc_{hashstr(f'{source_system}:{source_document_id}', 16)}"

    @staticmethod
    def _existing_payload(record) -> dict[str, Any]:
        return {
            "incomingId": record.incoming_id,
            "taskId": None,
            "status": "exists",
            "knowledgeImportStatus": getattr(record, "knowledge_import_status", None) or "none",
        }


async def _upload_incoming_file(*, source_system: str, incoming_id: str, filename: str, content: bytes) -> dict[str, Any]:
    safe_name = Path(filename).name
    if not safe_name or not is_supported_file_extension(safe_name):
        raise ValueError("Unsupported file type")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError("文件过大，当前仅支持 100 MB 以内的文件")
    content_hash = await calculate_content_hash(content)
    suffix = Path(safe_name).suffix.lower()
    stem = Path(safe_name).stem or "incoming"
    safe_source_system = _safe_object_segment(source_system)
    object_name = f"incoming/{safe_source_system}/{incoming_id}/{stem}_{int(time.time() * 1000)}{suffix}"
    minio_url = await aupload_file_to_minio(MinIOClient.KB_BUCKETS["documents"], object_name, content)
    return {"minio_url": minio_url, "content_hash": content_hash, "size": len(content)}


def _safe_object_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (value or "").strip())
    return cleaned or "production"
