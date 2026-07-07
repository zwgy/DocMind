from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from yuxi import config
from yuxi.knowledge.parser import is_supported_file_extension
from yuxi.knowledge.utils import calculate_content_hash
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.business_extraction_task_service import submit_business_extraction_task
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.minio import MinIOClient, aupload_file_to_minio
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES

UploadFileFn = Callable[..., Awaitable[dict[str, Any]]]
INCOMING_DOCUMENT_INGEST_TASK_TYPE = "incoming_document_ingest"
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
        file_repo: KnowledgeFileRepository | None = None,
        knowledge=None,
        tasker=tasker,
        default_kb_id: str | None = None,
        upload_file: UploadFileFn | None = None,
    ):
        self.file_repo = file_repo or KnowledgeFileRepository()
        if knowledge is None:
            from yuxi import knowledge_base

            knowledge = knowledge_base
        self.knowledge = knowledge
        self.tasker = tasker
        self.default_kb_id = default_kb_id or config.incoming_default_kb_id
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
        kb_id = self._require_default_kb_id()
        await self._validate_target(kb_id)
        existing = await self.file_repo.list_by_source_key(source_key, source_system)
        if existing:
            record = existing[0]
            return {"fileId": record.file_id, "kbId": record.kb_id, "taskId": None, "status": "exists"}

        upload = await self.upload_file(kb_id=kb_id, filename=filename, content=content)
        params = self._processing_params(
            source_key=source_key,
            source_url=source_url,
            source_doc_id=source_doc_id,
            source_system=source_system,
            source_filename=filename,
            source_size_text=source_size_text,
            content_hash=content_hash or upload["content_hash"],
            metadata=metadata,
        )
        file_meta = await self.knowledge.add_file_record(
            kb_id,
            upload["minio_url"],
            params={
                **params,
                "content_hashes": {upload["minio_url"]: upload["content_hash"]},
                "file_sizes": {upload["minio_url"]: int(file_size or upload["size"])},
                "source_path": filename,
            },
            operator_id=operator_id,
        )
        task = await self._submit_parse_task(kb_id=kb_id, file_id=file_meta["file_id"], operator_id=operator_id)
        return {"fileId": file_meta["file_id"], "kbId": kb_id, "taskId": task.id, "status": "accepted"}

    async def ingest_source_url(
        self,
        *,
        source_url: str,
        filename: str,
        source_key: str,
        source_system: str = "production",
        operator_id: str | None = None,
        **metadata,
    ) -> dict[str, Any]:
        kb_id = self._require_default_kb_id()
        await self._validate_target(kb_id)
        existing = await self.file_repo.list_by_source_key(source_key, source_system)
        if existing:
            record = existing[0]
            return {"fileId": record.file_id, "kbId": record.kb_id, "taskId": None, "status": "exists"}

        async def run_ingest(context: TaskContext):
            from yuxi.knowledge.utils.url_fetcher import fetch_url_content

            await context.set_progress(5.0, "准备下载来文")
            # 来文下载与网页解析共用 URL 拉取器，这里显式放开文档 MIME，避免影响网页解析默认白名单。
            content, _ = await fetch_url_content(
                source_url,
                max_size=MAX_UPLOAD_SIZE_BYTES,
                allowed_content_types=INCOMING_ALLOWED_CONTENT_TYPES,
            )
            return await self.ingest_file(
                content=content,
                filename=filename,
                source_key=source_key,
                source_url=source_url,
                source_system=source_system,
                operator_id=operator_id,
                **metadata,
            )

        task, _ = await self.tasker.enqueue_unique_by_payload(
            name=f"来文下载入库 ({source_key})",
            task_type=INCOMING_DOCUMENT_INGEST_TASK_TYPE,
            payload={
                "kb_id": kb_id,
                "source_system": source_system,
                "source_key": source_key,
                "source_url": source_url,
            },
            payload_match={"source_system": source_system, "source_key": source_key},
            statuses={"pending", "running"},
            coroutine=run_ingest,
        )
        return {"fileId": None, "kbId": kb_id, "taskId": task.id, "status": "accepted"}

    async def _submit_parse_task(self, *, kb_id: str, file_id: str, operator_id: str | None):
        async def run_parse(context: TaskContext):
            await context.set_progress(5.0, "准备解析来文")
            result = await self.knowledge.parse_file(kb_id, file_id, operator_id=operator_id)
            markdown_file = result.get("markdown_file")
            if result.get("status") == "parsed" and markdown_file:
                await submit_business_extraction_task(
                    kb_id=kb_id,
                    file_id=file_id,
                    markdown_file=markdown_file,
                    operator_id=operator_id,
                    queue=self.tasker,
                )
            return result

        task, _ = await self.tasker.enqueue_unique_by_payload(
            name=f"来文解析 ({file_id})",
            task_type="knowledge_parse",
            payload={"kb_id": kb_id, "file_id": file_id, "source": "incoming_document"},
            payload_match={"kb_id": kb_id, "file_id": file_id, "source": "incoming_document"},
            statuses={"pending", "running"},
            coroutine=run_parse,
        )
        return task

    def _require_default_kb_id(self) -> str:
        if not self.default_kb_id:
            raise ValueError("INCOMING_DEFAULT_KB_ID is required")
        return self.default_kb_id

    async def _validate_target(self, kb_id: str) -> None:
        if not await self.knowledge.get_database_info(kb_id):
            raise ValueError(f"Knowledge base {kb_id} not found")

    @staticmethod
    def _processing_params(**values) -> dict[str, Any]:
        return {key: value for key, value in values.items() if value not in (None, "", {})}


async def _upload_incoming_file(*, kb_id: str, filename: str, content: bytes) -> dict[str, Any]:
    if not filename or not is_supported_file_extension(filename):
        raise ValueError("Unsupported file type")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError("文件过大，当前仅支持 100 MB 以内的文件")
    content_hash = await calculate_content_hash(content)
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem or "incoming"
    object_name = f"{kb_id}/upload/{stem}_{int(time.time() * 1000)}{suffix}"
    minio_url = await aupload_file_to_minio(MinIOClient.KB_BUCKETS["documents"], object_name, content)
    return {"minio_url": minio_url, "content_hash": content_hash, "size": len(content)}
