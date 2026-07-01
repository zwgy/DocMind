from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from yuxi.knowledge.extraction.llm import JsonLLM, ModelJsonLLM
from yuxi.knowledge.extraction.prompts import build_category_prompt, build_extraction_prompt
from yuxi.knowledge.extraction.schemas import (
    DocumentCategoryResult,
    category_result_to_mapping,
    extraction_schema_ids_for_categories,
    get_extraction_schema,
)
from yuxi.repositories.knowledge_business_extraction_repository import KnowledgeBusinessExtractionRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.utils import hashstr, logger
from yuxi.utils.datetime_utils import utc_isoformat


@dataclass(slots=True)
class ChunkInput:
    chunk_id: str
    content: str
    chunk_index: int = 0


@dataclass(slots=True)
class ExtractedBusinessItem:
    kb_id: str
    file_id: str
    chunk_id: str
    item_type: str
    data: dict[str, Any]
    source_quote: str
    status: str = "draft"


@dataclass(slots=True)
class BusinessExtractionDraft:
    kb_id: str
    file_id: str
    categories: DocumentCategoryResult
    schema_ids: list[str]
    items: list[ExtractedBusinessItem] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


class BusinessExtractionService:
    def __init__(
        self,
        *,
        llm: JsonLLM | None = None,
        chunk_repo: KnowledgeChunkRepository | None = None,
        extraction_repo: KnowledgeBusinessExtractionRepository | None = None,
    ):
        self.llm = llm
        self.chunk_repo = chunk_repo or KnowledgeChunkRepository()
        self.extraction_repo = extraction_repo or KnowledgeBusinessExtractionRepository()

    async def extract_chunks(
        self,
        *,
        kb_id: str,
        file_id: str,
        chunks: list[dict[str, Any]],
        model_spec: str | None = None,
    ) -> BusinessExtractionDraft:
        llm = self._resolve_llm(model_spec)
        normalized_chunks = [
            ChunkInput(
                chunk_id=str(chunk["chunk_id"]),
                content=str(chunk.get("content") or ""),
                chunk_index=int(chunk.get("chunk_index") or 0),
            )
            for chunk in chunks
            if chunk.get("chunk_id") and chunk.get("content")
        ]

        category_result = await self._classify_chunks(llm, normalized_chunks)
        category_mapping = category_result_to_mapping(category_result)
        schema_ids = extraction_schema_ids_for_categories(category_mapping)
        if not schema_ids:
            return BusinessExtractionDraft(kb_id=kb_id, file_id=file_id, categories=category_result, schema_ids=[])

        items: list[ExtractedBusinessItem] = []
        errors: list[dict[str, Any]] = []
        for chunk in normalized_chunks:
            for schema_id in schema_ids:
                schema = get_extraction_schema(schema_id)
                try:
                    items.extend(await self._extract_schema_items(llm, schema_id, schema, kb_id, file_id, chunk))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Business extraction failed: "
                        f"file_id={file_id}, chunk_id={chunk.chunk_id}, schema={schema_id}: {exc}"
                    )
                    errors.append({"chunk_id": chunk.chunk_id, "schema_id": schema_id, "error": str(exc)})

        return BusinessExtractionDraft(
            kb_id=kb_id,
            file_id=file_id,
            categories=category_result,
            schema_ids=schema_ids,
            items=items,
            errors=errors,
        )

    async def run_file_extraction(
        self,
        *,
        kb_id: str,
        file_id: str,
        model_spec: str,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        chunks = await self.chunk_repo.list_by_file_id(file_id)
        run_id = f"ber_{hashstr(f'{file_id}:{utc_isoformat()}', 16)}"
        await self.extraction_repo.create_run(
            {
                "run_id": run_id,
                "kb_id": kb_id,
                "file_id": file_id,
                "status": "running",
                "model_spec": model_spec,
                "created_by": operator_id,
                "run_metadata": {"chunk_count": len(chunks)},
            }
        )
        try:
            draft = await self.extract_chunks(
                kb_id=kb_id,
                file_id=file_id,
                model_spec=model_spec,
                chunks=[
                    {"chunk_id": chunk.chunk_id, "content": chunk.content, "chunk_index": chunk.chunk_index}
                    for chunk in chunks
                ],
            )
            await self.extraction_repo.replace_result(
                run_id=run_id,
                result_data={
                    "kb_id": kb_id,
                    "file_id": file_id,
                    "categories": draft.categories.model_dump(),
                    "schema_ids": draft.schema_ids,
                    "status": "draft",
                    "created_by": operator_id,
                },
                items=[
                    {
                        "item_id": f"bei_{hashstr(f'{run_id}:{item.chunk_id}:{idx}:{item.item_type}', 16)}",
                        "kb_id": item.kb_id,
                        "file_id": item.file_id,
                        "chunk_id": item.chunk_id,
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
                {"status": "success", "run_metadata": {"errors": draft.errors}},
            )
            return {
                "run_id": run_id,
                "categories": draft.categories.model_dump(),
                "item_count": len(draft.items),
                "errors": draft.errors,
            }
        except Exception as exc:
            await self.extraction_repo.update_run(run_id, {"status": "failed", "error": str(exc)})
            raise

    async def _classify_chunks(self, llm: JsonLLM, chunks: list[ChunkInput]) -> DocumentCategoryResult:
        merged = DocumentCategoryResult()
        # 文件名/标题前置暂由前几个 chunk 承载；MVP 限制分类上下文，照顾本地小模型。
        for chunk in chunks[: min(len(chunks), 8)]:
            data = await llm.complete_json(build_category_prompt(chunk.content), DocumentCategoryResult)
            result = DocumentCategoryResult.model_validate(data)
            for name in DocumentCategoryResult.model_fields:
                current = getattr(merged, name)
                candidate = getattr(result, name)
                if candidate.matched and not current.matched:
                    setattr(merged, name, candidate)
        return merged

    async def _extract_schema_items(
        self,
        llm: JsonLLM,
        schema_id: str,
        schema: type[BaseModel],
        kb_id: str,
        file_id: str,
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
