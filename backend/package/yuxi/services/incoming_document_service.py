from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from yuxi.document_extraction.schemas import extraction_schema_display_metadata
from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository


class IncomingPageFile(BaseModel):
    """chat-iframe 从宿主页面收集到的附件线索。"""

    name: str
    size_text: str | None = None
    size_bytes: int | None = None
    source_url: str | None = None
    source_file_id: str
    source_function_id: str | None = None
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
    ):
        self.incoming_repo = incoming_repo or IncomingDocumentRepository()
        self.extraction_repo = extraction_repo or DocumentBusinessExtractionRepository()

    async def query_extractions(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        results = []
        seen = set()
        for raw in files:
            item = await self._query_one(IncomingPageFile.model_validate(raw))
            incoming_id = item.get("incomingId")
            if incoming_id and incoming_id in seen:
                continue
            if incoming_id:
                seen.add(incoming_id)
            results.append(item)
        return {"items": results}

    async def _query_one(self, incoming: IncomingPageFile) -> dict[str, Any]:
        base = {
            "incomingFileId": incoming.source_file_id,
            "name": incoming.name,
            "source_url": incoming.source_url,
            "source_file_id": incoming.source_file_id,
            "source_function_id": incoming.source_function_id,
            "source_doc_id": incoming.source_doc_id,
            "matchStatus": "not_found",
            "processingStatus": "not_found",
            "extractionStatus": "not_found",
        }
        if not incoming.source_function_id or not incoming.source_doc_id:
            return base | {"reason": "source_function_id/source_doc_id is required"}
        match = await self.incoming_repo.get_file_for_source(
            source_system=incoming.source_system or "production",
            source_function_id=incoming.source_function_id,
            source_document_id=incoming.source_doc_id,
            source_file_id=incoming.source_file_id,
        )
        if match is None:
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
        display["classificationLabel"] = classification
        metadata = document.document_metadata or {}
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
            "extractionStatus": "ready" if document.status == "ready" and document.summary else document.status,
            "classification": classification,
            "aiClassificationEvidence": getattr(document, "classification_evidence", None),
            "additionalClassifications": getattr(document, "additional_classifications", None) or [],
            "summary": document.summary,
            "hasParsedMarkdown": bool(document_files) and all(file.markdown_file_url for file in document_files),
            "runId": (extraction or {}).get("run_id"),
            "kbId": document.linked_kb_id,
            "fileId": getattr(matched_file, "linked_file_id", None),
            "fileStatus": getattr(matched_file, "knowledge_import_status", None) or "none",
            "categories": (extraction or {}).get("categories") or {},
            "schemaIds": schema_ids,
            "items": (extraction or {}).get("items") or [],
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
