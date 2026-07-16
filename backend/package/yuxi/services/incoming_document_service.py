from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from yuxi.document_extraction.schemas import extraction_schema_display_metadata
from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository


class IncomingPageFile(BaseModel):
    """chat-iframe 从宿主页面收集到的附件线索。"""

    id: str | None = None
    name: str
    size_text: str | None = None
    size_bytes: int | None = None
    source_url: str | None = None
    source_file_id: str | None = None
    source_function_id: str | None = None
    source_doc_id: str | None = None
    source_system: str | None = None
    onclick: str | None = None


class IncomingDocumentService:
    """面向 chat-iframe 的来文摘要查询服务，不负责上传、解析或知识库入库。"""

    def __init__(
        self,
        *,
        incoming_repo: IncomingDocumentRepository | None = None,
        extraction_repo: DocumentBusinessExtractionRepository | None = None,
        file_repo=None,
        tasker=None,
        model_spec: str | None = None,
    ):
        # 旧依赖已不再参与匹配；保留构造参数，避免调用方一次性大改。
        del file_repo, tasker, model_spec
        self.incoming_repo = incoming_repo or IncomingDocumentRepository()
        self.extraction_repo = extraction_repo or DocumentBusinessExtractionRepository()

    async def query_extractions(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        return {"items": [await self._query_one(IncomingPageFile.model_validate(item)) for item in files]}

    async def _query_one(self, incoming: IncomingPageFile) -> dict[str, Any]:
        candidates, reason = await self._match(incoming)
        # 这里只做摘要查询，不触发解析；未命中但有来源线索时交给 iframe 自动同步。
        base = {
            "incomingFileId": incoming.id or incoming.source_file_id or incoming.source_url or incoming.name,
            "name": incoming.name,
            "source_url": incoming.source_url,
            "source_file_id": incoming.source_file_id,
            "source_function_id": incoming.source_function_id,
            "source_doc_id": incoming.source_doc_id,
            "matchStatus": "not_found",
            "processingStatus": "not_found",
            "extractionStatus": "not_found",
            "reason": reason,
        }
        if not candidates:
            has_source_hint = bool(
                incoming.source_function_id
                and incoming.source_doc_id
                and (incoming.source_file_id or incoming.source_url)
            )
            # 有来源线索时让 iframe 去触发同步；没有任何线索则明确 not_found，避免无效上传。
            return base | {"matchStatus": "pending_sync" if has_source_hint else "not_found"}
        if len(candidates) > 1:
            return base | {"matchStatus": "multiple", "reason": reason}

        record = candidates[0]
        processing_status = getattr(record, "status", None) or "uploaded"
        has_markdown = bool(getattr(record, "markdown_file_url", None))
        summary = getattr(record, "summary", None)
        extraction_status = "ready" if processing_status == "ready" and summary else processing_status
        business_extraction = await self.extraction_repo.get_latest_by_incoming_id(record.incoming_id)
        schema_ids = (business_extraction or {}).get("schema_ids") or []
        display = extraction_schema_display_metadata(schema_ids)
        classification = getattr(record, "classification", None)
        display["classificationLabel"] = classification or _first_matched_category_label(
            (business_extraction or {}).get("categories") or {},
            display.get("categoryLabels") or {},
        )
        # 摘要卡片可能只收到文件定位线索，需以已匹配记录为准补全来文元数据。
        return base | {
            "incomingId": record.incoming_id,
            "source_system": getattr(record, "source_system", None),
            "document_number": getattr(record, "document_number", None),
            "title": getattr(record, "title", None),
            "incoming_type": getattr(record, "incoming_type", None),
            "source_unit": getattr(record, "source_unit", None),
            "incoming_date": getattr(record, "incoming_date", None),
            "matchStatus": "matched",
            "processingStatus": processing_status,
            "extractionStatus": extraction_status,
            "classification": classification,
            "summary": summary,
            "runId": (business_extraction or {}).get("run_id"),
            "categories": (business_extraction or {}).get("categories") or {},
            "schemaIds": schema_ids,
            "items": (business_extraction or {}).get("items") or [],
            "display": display,
            "hasMarkdown": has_markdown,
            "knowledgeImportStatus": getattr(record, "knowledge_import_status", None) or "none",
            "linkedKbId": getattr(record, "linked_kb_id", None),
            "linkedFileId": getattr(record, "linked_file_id", None),
            "reason": reason,
        }

    async def _match(self, incoming: IncomingPageFile):
        source_url = incoming.source_url
        source_system = incoming.source_system or "production"
        source_function_id = incoming.source_function_id
        source_document_id = incoming.source_doc_id
        if not source_function_id or not source_document_id:
            return [], "source_function_id/source_doc_id is required"
        # 匹配顺序从强到弱：同一来文内文件 ID 优先，最后只在同一来文内按文件名兜底。
        source_file_id = incoming.source_file_id
        if source_file_id:
            candidates = await self.incoming_repo.list_by_source_file_id(
                source_file_id,
                source_system=source_system,
                source_function_id=source_function_id,
                source_document_id=source_document_id,
            )
            if candidates:
                return candidates, "source_file_id matched"
        if source_url:
            candidates = await self.incoming_repo.list_by_source_url(
                source_url,
                source_system=source_system,
                source_function_id=source_function_id,
                source_document_id=source_document_id,
            )
            if candidates:
                return candidates, "source_url matched"
        if incoming.name:
            candidates = await self.incoming_repo.list_by_source_doc_id_and_filename(
                source_document_id,
                incoming.name,
                source_system=source_system,
                source_function_id=source_function_id,
            )
            if candidates:
                return candidates, "source_document_id + filename matched"
        return [], "not found"


def _first_matched_category_label(categories: dict[str, Any], category_labels: dict[str, str]) -> str | None:
    for name, value in categories.items():
        if isinstance(value, dict) and value.get("matched"):
            return category_labels.get(name) or name
    return None
