from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel
from yuxi.document_extraction.schemas import document_category_label, extraction_schema_display_metadata
from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.repositories.incoming_ingest_repository import IncomingIngestRepository
from yuxi.storage.postgres.manager import pg_manager

IngestJobLookup = Callable[[str, str], Awaitable[Any | None]]

INGEST_STAGE_LABELS = {
    "registered": "待接收",
    "downloading": "下载中",
    "parsing": "解析中",
    "extracting": "分类与抽取中",
    "succeeded": "已完成",
    "failed": "处理失败",
    "cancelled": "已取消",
}


class IncomingPageFile(BaseModel):
    """chat-iframe 从宿主页面收集到的附件线索。"""

    name: str
    size_text: str | None = None
    size_bytes: int | None = None
    source_url: str | None = None
    source_file_id: str
    source_doc_id: str | None = None
    source_system: str | None = None
    onclick: str | None = None


class IncomingDocumentService:
    """向 iframe 提供来文级摘要，不将附件全文带入对话上下文。"""

    def __init__(
        self,
        *,
        incoming_repo: IncomingDocumentRepository | None = None,
        extraction_repo: DocumentBusinessExtractionRepository | None = None,
        ingest_job_lookup: IngestJobLookup | None = None,
    ):
        self.incoming_repo = incoming_repo or IncomingDocumentRepository()
        self.extraction_repo = extraction_repo or DocumentBusinessExtractionRepository()
        self.ingest_job_lookup = ingest_job_lookup or self._find_ingest_job

    async def query_extractions(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        results = []
        seen = set()
        for raw in files:
            item = await self._query_one(IncomingPageFile.model_validate(raw))
            key = (str(item.get("incomingId") or ""), str(item.get("source_file_id") or ""))
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
        return {"items": results}

    async def register_historical_ingest(
        self,
        *,
        source_system: str,
        documents: list[dict[str, Any]],
        actor_uid: str,
    ) -> dict[str, Any]:
        async with pg_manager.get_async_session_context() as session:
            results = await IncomingIngestRepository(session).register_historical(
                source_system=source_system,
                documents=documents,
                actor_uid=actor_uid,
            )
        summary = {status: 0 for status in ("accepted", "exists", "requeued", "conflict")}
        for result in results:
            summary[result.status] += 1
        return {
            "items": [
                {
                    "sourceDocumentId": result.source_document_id,
                    "jobId": result.job_id,
                    "status": result.status,
                    "message": result.error_message,
                }
                for result in results
            ],
            "summary": summary,
        }

    async def list_ingest_jobs(
        self, *, page: int, page_size: int, status: str | None, priority: str | None
    ) -> dict[str, Any]:
        async with pg_manager.get_async_session_context() as session:
            jobs, total = await IncomingIngestRepository(session).list_jobs(
                page=page, page_size=page_size, status=status, priority=priority
            )
        return {"items": [self._job_payload(job) for job in jobs], "total": total}

    async def register_immediate_ingest(
        self,
        *,
        source_system: str,
        source_document_id: str,
        document_metadata: dict[str, Any],
        file_manifest: list[dict[str, Any]],
        actor_uid: str,
    ) -> dict[str, Any]:
        async with pg_manager.get_async_session_context() as session:
            result = await IncomingIngestRepository(session).register_immediate(
                source_system=source_system,
                source_document_id=source_document_id,
                document_metadata=document_metadata,
                file_manifest=file_manifest,
                actor_uid=actor_uid,
            )
        return {"jobId": result.job_id, "status": result.status, "priority": "immediate"}

    async def expedite_ingest_job(
        self, *, job_id: str, source_system: str, source_document_id: str, actor_uid: str
    ) -> bool:
        async with pg_manager.get_async_session_context() as session:
            return await IncomingIngestRepository(session).expedite(
                job_id,
                source_system=source_system,
                source_document_id=source_document_id,
                actor_uid=actor_uid,
            )

    async def retry_ingest_job(self, *, job_id: str, actor_uid: str) -> dict[str, Any]:
        async with pg_manager.get_async_session_context() as session:
            job = await IncomingIngestRepository(session).retry_terminal_job(job_id=job_id, actor_uid=actor_uid)
        if job is None:
            raise ValueError("仅处理失败或已取消的任务可以重试")
        return self._job_payload(job)

    async def refresh_ingest_job_source(
        self,
        *,
        job_id: str,
        document_metadata: dict[str, Any],
        file_manifest: list[dict[str, Any]],
        actor_uid: str,
    ) -> dict[str, Any]:
        async with pg_manager.get_async_session_context() as session:
            job = await IncomingIngestRepository(session).refresh_terminal_job_source(
                job_id=job_id,
                document_metadata=document_metadata,
                file_manifest=file_manifest,
                actor_uid=actor_uid,
            )
        if job is None:
            raise ValueError("仅处理失败或已取消的任务可以刷新来源")
        return self._job_payload(job)

    async def _query_one(self, incoming: IncomingPageFile) -> dict[str, Any]:
        base = {
            "incomingFileId": incoming.source_file_id,
            "name": incoming.name,
            "source_url": incoming.source_url,
            "source_file_id": incoming.source_file_id,
            "source_doc_id": incoming.source_doc_id,
            "matchStatus": "not_found",
            "processingStatus": "not_found",
            "extractionStatus": "not_found",
        }
        if not incoming.source_doc_id:
            return base | {"reason": "source_doc_id is required"}
        match = await self.incoming_repo.get_file_for_source(
            source_system=incoming.source_system or "production",
            source_document_id=incoming.source_doc_id,
            source_file_id=incoming.source_file_id,
        )
        if match is None:
            job = await self.ingest_job_lookup(incoming.source_system or "production", incoming.source_doc_id)
            if job is not None and any(
                str(item.get("source_file_id") or "") == incoming.source_file_id
                for item in (job.file_manifest or [])
                if isinstance(item, dict)
            ):
                return base | {
                    "matchStatus": "matched",
                    "processingStatus": job.status,
                    "extractionStatus": "pending",
                    "ingestJobId": job.job_id,
                    "ingestPriority": job.priority,
                    "reason": "incoming ingest job registered",
                }
            return base | {"matchStatus": "pending_sync", "reason": "source_file_id not found"}

        document, matched_file = match
        document_files = await self.incoming_repo.list_files(document.incoming_id)
        extraction = (
            await self.extraction_repo.get_latest_by_incoming_id(document.incoming_id)
            if document.status == "ready"
            else None
        )
        schema_ids = (extraction or {}).get("schema_ids") or []
        display = extraction_schema_display_metadata(schema_ids)
        classification = document.confirmed_classification or document.ai_classification
        display["classificationLabel"] = document_category_label(classification)
        metadata = document.document_metadata or {}
        is_main_file = bool(matched_file.is_main_file)
        attachment_summaries = ((extraction or {}).get("run_metadata") or {}).get("attachment_summaries") or {}
        summary = document.summary if is_main_file else attachment_summaries.get(matched_file.source_file_id)
        return base | {
            "incomingId": document.incoming_id,
            "source_system": document.source_system,
            "document_number": metadata.get("document_number"),
            "title": metadata.get("title"),
            "incoming_type": metadata.get("incoming_type"),
            "source_unit": metadata.get("source_unit"),
            "incoming_date": metadata.get("incoming_date"),
            "matchStatus": "matched",
            "processingStatus": document.status,
            "extractionStatus": "ready" if document.status == "ready" and summary else document.status,
            "classification": classification if is_main_file else None,
            "classificationLabel": document_category_label(classification) if is_main_file else None,
            "aiClassificationEvidence": getattr(document, "classification_evidence", None),
            "additionalClassifications": [
                item | {"classificationLabel": document_category_label(item.get("classification"))}
                for item in getattr(document, "additional_classifications", None) or []
                if isinstance(item, dict)
            ]
            if is_main_file
            else [],
            "summary": summary,
            "hasParsedMarkdown": bool(matched_file.markdown_file_url),
            "runId": (extraction or {}).get("run_id") if is_main_file else None,
            "kbId": document.linked_kb_id,
            "fileId": getattr(matched_file, "linked_file_id", None),
            "fileStatus": getattr(matched_file, "knowledge_import_status", None) or "none",
            "categories": ((extraction or {}).get("categories") or {}) if is_main_file else {},
            "schemaIds": schema_ids if is_main_file else [],
            "items": (extraction or {}).get("items") or [] if is_main_file else [],
            "files": [
                {
                    "sourceFileId": file.source_file_id,
                    "filename": file.filename,
                    "isMainFile": file.is_main_file,
                    "status": file.status,
                    "hasParsedMarkdown": bool(file.markdown_file_url),
                }
                for file in document_files
            ],
            "display": display,
            "knowledgeImportStatus": document.knowledge_import_status or "none",
            "linkedKbId": document.linked_kb_id,
            "reason": "source_file_id matched",
        }

    async def _find_ingest_job(self, source_system: str, source_document_id: str):
        async with pg_manager.get_async_session_context() as session:
            return await IncomingIngestRepository(session).get_by_source_identity(
                source_system=source_system,
                source_document_id=source_document_id,
            )

    @staticmethod
    def _job_payload(job) -> dict[str, Any]:
        metadata = job.document_metadata or {}
        processing_error = job.processing_error
        return {
            "jobId": job.job_id,
            "sourceSystem": job.source_system,
            "sourceDocumentId": job.source_document_id,
            "title": metadata.get("title") or metadata.get("document_number") or job.source_document_id,
            "priority": job.priority,
            "status": job.status,
            "stage": job.stage,
            "stageLabel": INGEST_STAGE_LABELS.get(job.stage, "处理中"),
            "attemptCount": job.attempt_count,
            "failureReason": _ingest_failure_reason(processing_error),
            "processingError": processing_error,
            "updatedAt": job.updated_at,
        }


def _ingest_failure_reason(error: str | None) -> str | None:
    """列表只展示可操作的失败摘要，完整诊断仍留给详情弹窗。"""
    if not error:
        return None
    if "下载超时" in error:
        return "下载超时"
    if "HTTP 401" in error or "HTTP 403" in error or "HTML 页面" in error:
        return "下载地址或访问权限异常"
    if "HTTP 404" in error or "HTTP 410" in error:
        return "下载地址不可用或已失效"
    if "附件下载失败" in error:
        return "附件下载失败"
    return "处理失败，请查看详情"
