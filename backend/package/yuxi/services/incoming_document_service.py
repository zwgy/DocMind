from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from yuxi import config
from yuxi.repositories.knowledge_business_extraction_repository import KnowledgeBusinessExtractionRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.business_extraction_task_service import (
    ACTIVE_BUSINESS_EXTRACTION_STATUSES,
    BUSINESS_EXTRACTION_TASK_TYPE,
)
from yuxi.services.task_service import tasker


class IncomingPageFile(BaseModel):
    id: str | None = None
    name: str
    size_text: str | None = Field(default=None, alias="sizeText")
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    url: str | None = None
    source_url: str | None = Field(default=None, alias="sourceUrl")
    source_key: str | None = Field(default=None, alias="sourceKey")
    source_doc_id: str | None = Field(default=None, alias="sourceDocId")
    source_system: str | None = Field(default=None, alias="sourceSystem")
    onclick: str | None = None

    model_config = {"populate_by_name": True}


class IncomingDocumentService:
    def __init__(
        self,
        *,
        file_repo: KnowledgeFileRepository | None = None,
        extraction_repo: KnowledgeBusinessExtractionRepository | None = None,
        tasker=tasker,
        model_spec: str | None = None,
    ):
        self.file_repo = file_repo or KnowledgeFileRepository()
        self.extraction_repo = extraction_repo or KnowledgeBusinessExtractionRepository()
        self.tasker = tasker
        self.model_spec = model_spec or config.business_extraction_model or config.default_model

    async def query_extractions(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        return {"items": [await self._query_one(IncomingPageFile.model_validate(item)) for item in files]}

    async def _query_one(self, incoming: IncomingPageFile) -> dict[str, Any]:
        candidates, reason = await self._match(incoming)
        base = {
            "incomingFileId": incoming.id or incoming.source_key or incoming.source_url or incoming.name,
            "name": incoming.name,
            "matchStatus": "not_found",
            "extractionStatus": "not_found",
            "reason": reason,
        }
        if not candidates:
            has_source_hint = bool(incoming.source_key or incoming.source_url or incoming.source_doc_id or incoming.url)
            return base | {"matchStatus": "pending_sync" if has_source_hint else "not_found"}
        if len(candidates) > 1:
            return base | {"matchStatus": "multiple", "reason": reason}

        record = candidates[0]
        extraction = await self._extraction_payload(record)
        return base | {
            "matchStatus": "matched",
            "kbId": record.kb_id,
            "fileId": record.file_id,
            "reason": reason,
            **extraction,
        }

    async def _match(self, incoming: IncomingPageFile):
        source_url = incoming.source_url or incoming.url
        source_system = incoming.source_system or "production"
        if incoming.source_key:
            candidates = await self.file_repo.list_by_source_key(incoming.source_key, source_system)
            if candidates:
                return candidates, "source_key matched"
        if source_url:
            candidates = await self.file_repo.list_by_source_url(source_url, source_system)
            if candidates:
                return candidates, "source_url matched"
        if incoming.source_doc_id and incoming.name:
            candidates = await self.file_repo.list_by_source_doc_id_and_filename(
                incoming.source_doc_id,
                incoming.name,
                source_system,
            )
            if candidates:
                return candidates, "source_doc_id + filename matched"
        if incoming.name and incoming.size_bytes:
            candidates = await self.file_repo.list_by_filename_and_size(incoming.name, incoming.size_bytes)
            if candidates:
                return candidates, "filename + file_size matched"
        if incoming.name:
            candidates = await self.file_repo.list_by_filename(incoming.name)
            if candidates:
                return candidates, "filename matched"
        return [], "not found"

    async def _extraction_payload(self, record) -> dict[str, Any]:
        markdown_file = record.markdown_file
        latest = await self.extraction_repo.get_latest_success_view_by_file_id(
            record.file_id,
            markdown_file=markdown_file,
        )
        if latest:
            return {
                "extractionStatus": "ready",
                "runId": latest["run_id"],
                "categories": latest.get("categories") or {},
                "items": latest.get("items") or [],
            }

        task = None
        if markdown_file:
            task = await self.tasker.find_task_by_payload(
                task_type=BUSINESS_EXTRACTION_TASK_TYPE,
                payload_match={
                    "kb_id": record.kb_id,
                    "file_id": record.file_id,
                    "markdown_file": markdown_file,
                    "model_spec": self.model_spec,
                },
                statuses=ACTIVE_BUSINESS_EXTRACTION_STATUSES,
            )
        if task:
            return {"extractionStatus": "running", "runId": None, "categories": {}, "items": []}

        run = await self.extraction_repo.get_latest_run_by_file_id(record.file_id, markdown_file=markdown_file)
        if run and run.get("status") == "failed":
            return {
                "extractionStatus": "failed",
                "runId": run.get("run_id"),
                "categories": {},
                "items": [],
                "reason": run.get("error") or "extraction failed",
            }
        return {"extractionStatus": "not_found", "runId": None, "categories": {}, "items": []}
