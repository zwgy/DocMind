from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from yuxi.document_extraction.llm import JsonLLM, ModelJsonLLM
from yuxi.document_extraction.prompts import (
    build_extraction_prompt,
    build_category_prompt,
)
from yuxi.document_extraction.schemas import (
    DocumentCategoryResult,
    IncomingDocumentClassificationResult,
    category_result_for_classification_label,
    category_result_to_mapping,
    extraction_schema_ids_for_categories,
    get_extraction_schema,
)
from yuxi.knowledge.chunking.ragflow_like.dispatcher import chunk_markdown
from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.utils import is_minio_url, parse_minio_url
from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository
from yuxi.storage.minio import get_minio_client
from yuxi.utils import hashstr, logger
from yuxi.utils.datetime_utils import utc_isoformat

MarkdownReader = Callable[[str], str | Awaitable[str]]
SHORT_MARKDOWN_EXTRACTION_TOKEN_LIMIT = 12_000
INCOMING_CLASSIFICATION_MARKDOWN_LIMIT = 20_000


@dataclass(slots=True)
class ChunkInput:
    chunk_id: str | None
    content: str
    chunk_index: int = 0


@dataclass(slots=True)
class ExtractedBusinessItem:
    document_scope: str
    incoming_id: str | None
    kb_id: str | None
    file_id: str | None
    chunk_id: str | None
    item_type: str
    data: dict[str, Any]
    source_quote: str
    status: str = "draft"


@dataclass(slots=True)
class BusinessExtractionDraft:
    document_scope: str
    incoming_id: str | None
    kb_id: str | None
    file_id: str | None
    categories: DocumentCategoryResult
    schema_ids: list[str]
    items: list[ExtractedBusinessItem] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


class BusinessExtractionService:
    """独立文档业务结构化抽取服务；调用方决定是否把结果挂到来文或知识库文件。"""

    def __init__(
        self,
        *,
        llm: JsonLLM | None = None,
        extraction_repo: DocumentBusinessExtractionRepository | None = None,
    ):
        self.llm = llm
        self.extraction_repo = extraction_repo or DocumentBusinessExtractionRepository()

    async def extract_chunks(
        self,
        *,
        document_scope: str,
        incoming_id: str | None = None,
        kb_id: str | None = None,
        file_id: str | None = None,
        chunks: list[dict[str, Any]],
        model_spec: str | None = None,
        category_result: DocumentCategoryResult,
    ) -> BusinessExtractionDraft:
        llm = self._resolve_llm(model_spec)
        normalized_chunks = [
            ChunkInput(
                chunk_id=str(chunk["chunk_id"]) if chunk.get("chunk_id") is not None else None,
                content=str(chunk.get("content") or ""),
                chunk_index=int(chunk.get("chunk_index") or 0),
            )
            for chunk in chunks
            if chunk.get("content")
        ]

        category_mapping = category_result_to_mapping(category_result)
        # 通用类只做兜底；即使模型或调用方同时判真，也不能和专业 schema 重复抽取。
        if any(matched for name, matched in category_mapping.items() if name != "general"):
            category_mapping["general"] = False
        schema_ids = extraction_schema_ids_for_categories(category_mapping)
        if not schema_ids:
            return BusinessExtractionDraft(
                document_scope=document_scope,
                incoming_id=incoming_id,
                kb_id=kb_id,
                file_id=file_id,
                categories=category_result,
                schema_ids=[],
            )

        items: list[ExtractedBusinessItem] = []
        errors: list[dict[str, Any]] = []
        for chunk in normalized_chunks:
            for schema_id in schema_ids:
                schema = get_extraction_schema(schema_id)
                try:
                    items.extend(
                        await self._extract_schema_items(
                            llm,
                            schema_id,
                            schema,
                            document_scope,
                            incoming_id,
                            kb_id,
                            file_id,
                            chunk,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Business extraction failed: "
                        f"scope={document_scope}, incoming_id={incoming_id}, file_id={file_id}, "
                        f"chunk_id={chunk.chunk_id}, schema={schema_id}: {exc}"
                    )
                    errors.append({"chunk_id": chunk.chunk_id, "schema_id": schema_id, "error": str(exc)})

        return BusinessExtractionDraft(
            document_scope=document_scope,
            incoming_id=incoming_id,
            kb_id=kb_id,
            file_id=file_id,
            categories=category_result,
            schema_ids=schema_ids,
            items=_merge_obvious_duplicate_items(items),
            errors=errors,
        )

    async def run_markdown_extraction(
        self,
        *,
        document_scope: str,
        markdown_file: str,
        model_spec: str,
        incoming_id: str | None = None,
        kb_id: str | None = None,
        file_id: str | None = None,
        filename: str = "",
        processing_params: dict[str, Any] | None = None,
        operator_id: str | None = None,
        markdown_reader: MarkdownReader | None = None,
    ) -> dict[str, Any]:
        processing_params = processing_params or {}
        classification = str(processing_params.get("classification") or "").strip()
        category_result = category_result_for_classification_label(classification)
        if not any(category_result_to_mapping(category_result).values()):
            raise ValueError("A valid classification is required for business extraction")
        expected_schema_ids = extraction_schema_ids_for_categories(category_result_to_mapping(category_result))
        reusable = await self.extraction_repo.get_success_by_document_markdown_model(
            document_scope=document_scope,
            incoming_id=incoming_id,
            file_id=file_id,
            markdown_file=markdown_file,
            model_spec=model_spec,
        )
        if reusable and not (expected_schema_ids and not reusable.get("schema_ids")):
            return {**reusable, "reused": True}

        markdown = await self._read_markdown(markdown_file, markdown_reader)
        document_key = incoming_id or file_id or hashstr(markdown_file, 16)
        segments = self._markdown_segments(
            markdown=markdown,
            document_key=document_key,
            filename=filename or document_key,
            processing_params=processing_params,
        )
        run_id = f"ber_{hashstr(f'{document_scope}:{document_key}:{markdown_file}:{utc_isoformat()}', 16)}"
        run_metadata = {
            "markdown_file": markdown_file,
            "model_spec": model_spec,
            "segment_count": len(segments),
        }
        await self.extraction_repo.create_run(
            {
                "run_id": run_id,
                "document_scope": document_scope,
                "incoming_id": incoming_id,
                "kb_id": kb_id,
                "file_id": file_id,
                "status": "running",
                "model_spec": model_spec,
                "created_by": operator_id,
                "run_metadata": run_metadata,
            }
        )
        try:
            draft = await self.extract_chunks(
                document_scope=document_scope,
                incoming_id=incoming_id,
                kb_id=kb_id,
                file_id=file_id,
                model_spec=model_spec,
                chunks=segments,
                category_result=category_result,
            )
            await self.extraction_repo.replace_result(
                run_id=run_id,
                result_data={
                    "document_scope": document_scope,
                    "incoming_id": incoming_id,
                    "kb_id": kb_id,
                    "file_id": file_id,
                    "categories": draft.categories.model_dump(),
                    "schema_ids": draft.schema_ids,
                    "status": "draft",
                    "created_by": operator_id,
                },
                items=[
                    {
                        "item_id": f"bei_{hashstr(f'{run_id}:{idx}:{item.item_type}', 16)}",
                        "document_scope": item.document_scope,
                        "incoming_id": item.incoming_id,
                        "kb_id": item.kb_id,
                        "file_id": item.file_id,
                        # Markdown 阶段不绑定 KnowledgeChunk，避免业务抽取反向依赖向量入库。
                        "chunk_id": None,
                        "item_type": item.item_type,
                        "data": item.data,
                        "source_quote": item.source_quote,
                        "status": item.status,
                    }
                    for idx, item in enumerate(draft.items)
                ],
            )
            await self.extraction_repo.update_run(
                run_id,
                {"status": "success", "run_metadata": run_metadata | {"errors": draft.errors}},
            )
            return {
                "run_id": run_id,
                "categories": draft.categories.model_dump(),
                "item_count": len(draft.items),
                "errors": draft.errors,
                "reused": False,
            }
        except Exception as exc:
            await self.extraction_repo.update_run(
                run_id,
                {"status": "failed", "error": str(exc), "run_metadata": run_metadata | {"errors": [str(exc)]}},
            )
            raise

    async def link_knowledge_file(self, *, incoming_id: str, kb_id: str, file_id: str) -> None:
        await self.extraction_repo.link_knowledge_file(incoming_id=incoming_id, kb_id=kb_id, file_id=file_id)

    async def _read_markdown(self, markdown_file: str, markdown_reader: MarkdownReader | None) -> str:
        if markdown_reader is not None:
            content = markdown_reader(markdown_file)
            if inspect.isawaitable(content):
                content = await content
            return str(content)
        if not is_minio_url(markdown_file):
            raise ValueError(f"Invalid MinIO markdown path: {markdown_file}")
        bucket_name, object_name = parse_minio_url(markdown_file)
        return (await get_minio_client().adownload_file(bucket_name, object_name)).decode("utf-8")

    @staticmethod
    def _markdown_segments(
        *,
        markdown: str,
        document_key: str,
        filename: str,
        processing_params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        text = markdown.strip()
        if not text:
            return []
        if count_tokens(text) <= SHORT_MARKDOWN_EXTRACTION_TOKEN_LIMIT:
            return [{"chunk_id": None, "content": text, "chunk_index": 0}]
        chunks = chunk_markdown(text, document_key, filename, processing_params)
        return [
            {"chunk_id": None, "content": chunk["content"], "chunk_index": chunk["chunk_index"]}
            for chunk in chunks
        ]

    async def _extract_schema_items(
        self,
        llm: JsonLLM,
        schema_id: str,
        schema: type[BaseModel],
        document_scope: str,
        incoming_id: str | None,
        kb_id: str | None,
        file_id: str | None,
        chunk: ChunkInput,
    ) -> list[ExtractedBusinessItem]:
        data = await llm.complete_json(build_extraction_prompt(schema, chunk.content), schema)
        raw_items = data.get("items") if isinstance(data, dict) else None
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise ValueError("Extraction output field 'items' must be a list")

        items: list[ExtractedBusinessItem] = []
        for raw_item in raw_items:
            try:
                parsed = schema.model_validate(raw_item)
            except ValidationError as exc:
                raise ValueError(f"Invalid {schema_id} item: {exc}") from exc
            payload = parsed.model_dump()
            source_quote = str(payload.get("source_quote") or "")
            if not source_quote:
                continue
            items.append(
                ExtractedBusinessItem(
                    document_scope=document_scope,
                    incoming_id=incoming_id,
                    kb_id=kb_id,
                    file_id=file_id,
                    chunk_id=chunk.chunk_id,
                    item_type=schema_id,
                    data=payload,
                    source_quote=source_quote,
                )
            )
        return items

    def _resolve_llm(self, model_spec: str | None) -> JsonLLM:
        if self.llm is not None:
            return self.llm
        if not model_spec:
            raise ValueError("model_spec is required when no JsonLLM is injected")
        return ModelJsonLLM(model_spec)


async def classify_incoming_document(
    *,
    filename: str,
    markdown: str,
    metadata: dict[str, Any] | None = None,
    model_spec: str,
) -> dict[str, Any]:
    prompt = build_category_prompt(
        filename=filename,
        markdown=markdown,
        metadata=metadata,
        markdown_limit=INCOMING_CLASSIFICATION_MARKDOWN_LIMIT,
    )
    data = await ModelJsonLLM(model_spec).complete_json(prompt, IncomingDocumentClassificationResult)
    return IncomingDocumentClassificationResult.model_validate(data).model_dump()


def _merge_obvious_duplicate_items(items: list[ExtractedBusinessItem]) -> list[ExtractedBusinessItem]:
    merged: list[ExtractedBusinessItem] = []
    index_by_key: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
    for item in items:
        key = _item_merge_key(item)
        if key is None:
            merged.append(item)
            continue
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(merged)
            merged.append(item)
            continue
        target = merged[existing_index]
        # 业务字段完全一致时才合并；原文依据允许来自不同分块并应全部保留。
        quotes = [quote for quote in (target.source_quote, item.source_quote) if quote]
        target.source_quote = "\n".join(dict.fromkeys(quotes))
        target.data["source_quote"] = target.source_quote
    return merged


def _item_merge_key(item: ExtractedBusinessItem) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    # source_quote 是证据而非业务身份；其余字段任一不同都表示不能确认是同一事项。
    values = tuple(
        sorted(
            (name, _normalize_merge_value(value))
            for name, value in item.data.items()
            if name != "source_quote"
        )
    )
    if not values:
        return None
    return item.item_type, values


def _normalize_merge_value(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())
