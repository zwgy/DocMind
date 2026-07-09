from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository


class IncomingPageFile(BaseModel):
    """chat-iframe 从宿主页面收集到的附件线索。"""

    id: str | None = None
    name: str
    size_text: str | None = Field(default=None, alias="sizeText")
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    url: str | None = None
    source_url: str | None = Field(default=None, alias="sourceUrl")
    source_key: str | None = Field(default=None, alias="sourceKey")
    source_file_id: str | None = Field(default=None, alias="sourceFileId")
    source_doc_id: str | None = Field(default=None, alias="sourceDocId")
    source_system: str | None = Field(default=None, alias="sourceSystem")
    onclick: str | None = None

    model_config = {"populate_by_name": True}


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
        # 返回字段兼容 iframe 旧语义，但数据源已经切到 incoming_documents。
        base = {
            "incomingFileId": incoming.id or incoming.source_file_id or incoming.source_key or incoming.source_url or incoming.name,
            "name": incoming.name,
            "sourceUrl": incoming.source_url or incoming.url,
            "sourceFileId": incoming.source_file_id or incoming.source_key,
            "sourceKey": incoming.source_key,
            "matchStatus": "not_found",
            "processingStatus": "not_found",
            "extractionStatus": "not_found",
            "reason": reason,
        }
        if not candidates:
            has_source_hint = bool(incoming.source_file_id or incoming.source_key or incoming.source_url or incoming.source_doc_id or incoming.url)
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
        return base | {
            "incomingId": record.incoming_id,
            "matchStatus": "matched",
            "processingStatus": processing_status,
            "extractionStatus": extraction_status,
            "classification": getattr(record, "classification", None),
            "summary": summary,
            "runId": (business_extraction or {}).get("run_id"),
            "categories": (business_extraction or {}).get("categories") or {},
            "schemaIds": (business_extraction or {}).get("schema_ids") or [],
            "items": (business_extraction or {}).get("items") or [],
            "hasMarkdown": has_markdown,
            "knowledgeImportStatus": getattr(record, "knowledge_import_status", None) or "none",
            "linkedKbId": getattr(record, "linked_kb_id", None),
            "linkedFileId": getattr(record, "linked_file_id", None),
            "reason": reason,
        }

    async def _match(self, incoming: IncomingPageFile):
        source_url = incoming.source_url or incoming.url
        source_system = incoming.source_system or "production"
        # 匹配顺序从强到弱：外部系统主键优先，最后才退到文件名，降低误匹配概率。
        source_file_id = incoming.source_file_id or incoming.source_key
        if source_file_id:
            candidates = await self.incoming_repo.list_by_source_key(source_file_id, source_system)
            if candidates:
                return candidates, "source_file_id matched"
        if source_url:
            candidates = await self.incoming_repo.list_by_source_url(source_url, source_system)
            if candidates:
                return candidates, "source_url matched"
        if incoming.source_doc_id and incoming.name:
            candidates = await self.incoming_repo.list_by_source_doc_id_and_filename(
                incoming.source_doc_id,
                incoming.name,
                source_system,
            )
            if candidates:
                return candidates, "source_document_id + filename matched"
        if incoming.name and incoming.size_bytes:
            candidates = await self.incoming_repo.list_by_filename_and_size(incoming.name, incoming.size_bytes)
            if candidates:
                return candidates, "filename + file_size matched"
        if incoming.name:
            candidates = await self.incoming_repo.list_by_filename(incoming.name)
            if candidates:
                return candidates, "filename matched"
        return [], "not found"
