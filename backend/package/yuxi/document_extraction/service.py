from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from yuxi.document_extraction.llm import JsonLLM, ModelJsonLLM
from yuxi.document_extraction.prompts import (
    build_attachment_summary_prompt,
    build_extraction_prompt,
    build_category_prompt,
)
from yuxi.document_extraction.schemas import (
    DocumentCategoryResult,
    IncomingDocumentClassificationResult,
    IncomingAttachmentSummary,
    category_result_for_classification_label,
    category_result_for_classification_labels,
    category_result_to_mapping,
    extraction_schema_ids_for_categories,
    get_extraction_schema,
)
from yuxi.knowledge.chunking.ragflow_like.dispatcher import chunk_markdown
from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.utils import is_minio_url, parse_minio_url
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository
from yuxi.storage.minio import get_minio_client
from yuxi.utils import hashstr, logger
from yuxi.utils.datetime_utils import utc_isoformat

MarkdownReader = Callable[[str], str | Awaitable[str]]
SHORT_MARKDOWN_EXTRACTION_TOKEN_LIMIT = 12_000
DEFAULT_MODEL_CONTEXT_TOKEN_LIMIT = 32_768
MODEL_INPUT_TOKEN_RATIO = 0.7
DOCUMENT_CHUNK_OVERLAP_PERCENT = 10


def document_input_token_limit(model_spec: str) -> int:
    """给正文保留 70% 上下文，剩余空间用于提示词和结构化输出。"""
    info = model_cache.get_model_info(model_spec)
    try:
        context_limit = int(info.context_length) if info and info.context_length else DEFAULT_MODEL_CONTEXT_TOKEN_LIMIT
    except (TypeError, ValueError):
        context_limit = DEFAULT_MODEL_CONTEXT_TOKEN_LIMIT
    if context_limit <= 0:
        context_limit = DEFAULT_MODEL_CONTEXT_TOKEN_LIMIT
    return max(1_024, int(context_limit * MODEL_INPUT_TOKEN_RATIO))


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
    evidence: list[dict[str, str]] = field(default_factory=list)


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
        merge_items: bool = True,
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
            items=_merge_obvious_duplicate_items(items) if merge_items else items,
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
            token_limit=document_input_token_limit(model_spec),
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
                        "evidence": item.evidence,
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

    async def run_incoming_document_extraction(
        self,
        *,
        incoming_id: str,
        files: list[dict[str, Any]],
        classifications: list[str],
        model_spec: str,
        operator_id: str | None = None,
        attachment_summaries: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """以一份来文为单位抽取，并按附件保留可靠的原文定位。"""
        category_result = category_result_for_classification_labels(classifications)
        category_mapping = category_result_to_mapping(category_result)
        if not any(category_mapping.values()):
            raise ValueError("A valid classification is required for business extraction")
        run_id = f"ber_{hashstr(f'incoming:{incoming_id}:{utc_isoformat()}', 16)}"
        source_files = {
            str(file["incoming_file_id"]): {
                "source_file_id": str(file.get("source_file_id") or file["incoming_file_id"]),
                "file_name": str(file["filename"]),
            }
            for file in files
        }
        attachment_summaries = attachment_summaries or {}
        await self.extraction_repo.create_run(
            {
                "run_id": run_id,
                "document_scope": "incoming",
                "incoming_id": incoming_id,
                "status": "running",
                "model_spec": model_spec,
                "created_by": operator_id,
                "run_metadata": {
                    "source_files": source_files,
                    "attachment_summaries": attachment_summaries,
                    "model_spec": model_spec,
                },
            }
        )
        try:
            all_items: list[ExtractedBusinessItem] = []
            errors: list[dict[str, Any]] = []
            input_limit = document_input_token_limit(model_spec)
            main_files = [file for file in files if file.get("is_main_file")]
            # 历史调用没有传主附件标记时，沿用首文件作为主附件；正式来文入口始终传入 is_main_file。
            for file in main_files or files[:1]:
                incoming_file_id = str(file["incoming_file_id"])
                document_key = f"{incoming_id}:{incoming_file_id}"
                segments = self._markdown_segments(
                    markdown=str(file["markdown"]),
                    document_key=document_key,
                    filename=str(file["filename"]),
                    processing_params={},
                    token_limit=input_limit,
                )
                draft = await self.extract_chunks(
                    document_scope="incoming",
                    incoming_id=incoming_id,
                    file_id=incoming_file_id,
                    model_spec=model_spec,
                    chunks=segments,
                    category_result=category_result,
                    merge_items=False,
                )
                source = source_files[incoming_file_id]
                segment_location = {
                    str(segment.get("chunk_id")) if segment.get("chunk_id") is not None else None: (
                        "全文" if len(segments) == 1 else f"正文第 {int(segment.get('chunk_index') or 0) + 1} 部分"
                    )
                    for segment in segments
                }
                for item in draft.items:
                    # 抽取结论可以概括原文，不能因模型参考片段不是逐字引文而丢弃；
                    # 按附件分别抽取，才能把后续原文读取准确收敛到对应文件和分段。
                    item.evidence = [
                        {
                            "source_file_id": source["source_file_id"],
                            "incoming_file_id": incoming_file_id,
                            "file_name": source["file_name"],
                            "quote": item.source_quote,
                            "source_location": segment_location.get(item.chunk_id) or "全文",
                        }
                    ]
                all_items.extend(draft.items)
                errors.extend(draft.errors)

            if errors:
                # 模型调用或 schema 解析失败意味着该分块未完成，不能发布为完整来文结果。
                failed = ", ".join(
                    f"{item.get('schema_id')}@{item.get('chunk_id') or 'document'}" for item in errors[:5]
                )
                raise RuntimeError(f"Business extraction incomplete: {failed}")

            merged = _merge_obvious_duplicate_items(all_items)
            await self.extraction_repo.replace_result(
                run_id=run_id,
                result_data={
                    "document_scope": "incoming",
                    "incoming_id": incoming_id,
                    "categories": category_result.model_dump(),
                    "schema_ids": extraction_schema_ids_for_categories(category_result_to_mapping(category_result)),
                    "status": "draft",
                    "created_by": operator_id,
                },
                items=[
                    {
                        "item_id": f"bei_{hashstr(f'{run_id}:{index}:{item.item_type}', 16)}",
                        "document_scope": item.document_scope,
                        "incoming_id": item.incoming_id,
                        "kb_id": item.kb_id,
                        "file_id": item.file_id,
                        "chunk_id": None,
                        "item_type": item.item_type,
                        "data": item.data,
                        "evidence": item.evidence,
                    }
                    for index, item in enumerate(merged)
                ],
            )
            await self.extraction_repo.update_run(
                run_id,
                {
                    "status": "success",
                    "run_metadata": {
                        "source_files": source_files,
                        "attachment_summaries": attachment_summaries,
                        "model_spec": model_spec,
                        "errors": errors,
                        "warnings": [],
                        "dropped_item_count": 0,
                    },
                },
            )
            return {
                "run_id": run_id,
                "item_count": len(merged),
                "errors": errors,
                "warnings": [],
                "dropped_item_count": 0,
            }
        except Exception as exc:
            await self.extraction_repo.update_run(run_id, {"status": "failed", "error": str(exc)})
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
        token_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        text = markdown.strip()
        if not text:
            return []
        token_limit = token_limit or SHORT_MARKDOWN_EXTRACTION_TOKEN_LIMIT
        if count_tokens(text) <= token_limit:
            return [{"chunk_id": None, "content": text, "chunk_index": 0}]
        params = dict(processing_params)
        parser_config = dict(params.get("chunk_parser_config") or {})
        # 通用解析器允许块略超目标值，因此目标取输入预算的 2/3，确保最终块仍能放入模型。
        parser_config.setdefault("chunk_token_num", max(512, int(token_limit / 1.5)))
        parser_config.setdefault("overlapped_percent", DOCUMENT_CHUNK_OVERLAP_PERCENT)
        params["chunk_parser_config"] = parser_config
        chunks = chunk_markdown(text, document_key, filename, params)
        return [
            {"chunk_id": chunk["chunk_id"], "content": chunk["content"], "chunk_index": chunk["chunk_index"]}
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
    )
    data = await ModelJsonLLM(model_spec).complete_json(prompt, IncomingDocumentClassificationResult)
    return IncomingDocumentClassificationResult.model_validate(data).model_dump()


async def summarize_incoming_attachment(*, filename: str, markdown: str, model_spec: str) -> str:
    """只概括副附件内容，不混入来文分类与业务结构化语义。"""
    data = await ModelJsonLLM(model_spec).complete_json(
        build_attachment_summary_prompt(filename=filename, markdown=markdown),
        IncomingAttachmentSummary,
    )
    return IncomingAttachmentSummary.model_validate(data).summary


ITEM_IDENTITY_FIELDS = {
    "risk_item": ("risk_name",),
    "task_item": ("task_name",),
    "assessment_item": ("target",),
    "reward_punishment_item": ("target", "action_type"),
    "management_requirement_item": ("requirement",),
    "general_item": ("content",),
}


def _merge_obvious_duplicate_items(items: list[ExtractedBusinessItem]) -> list[ExtractedBusinessItem]:
    merged: list[ExtractedBusinessItem] = []
    for item in items:
        identity_fields = ITEM_IDENTITY_FIELDS.get(item.item_type, ())
        identity = tuple(_normalize_merge_value(item.data.get(name)) for name in identity_fields)
        target = None
        if identity_fields and all(identity):
            # ponytail: 结果条目通常只有几十条；若实测达到千级再改为按身份字段建索引。
            for existing in merged:
                if existing.item_type != item.item_type:
                    continue
                existing_identity = tuple(_normalize_merge_value(existing.data.get(name)) for name in identity_fields)
                if existing_identity != identity:
                    continue
                conflicts = any(
                    left and right and left != right
                    for name in set(existing.data) | set(item.data)
                    if name != "source_quote"
                    for left, right in [
                        (
                            _normalize_merge_value(existing.data.get(name)),
                            _normalize_merge_value(item.data.get(name)),
                        )
                    ]
                )
                if not conflicts:
                    target = existing
                    break
        if target is None:
            merged.append(item)
            continue
        for name, value in item.data.items():
            if name != "source_quote" and not _normalize_merge_value(target.data.get(name)):
                target.data[name] = value
        quotes = [quote for quote in (target.source_quote, item.source_quote) if quote]
        target.source_quote = "\n".join(dict.fromkeys(quotes))
        target.data["source_quote"] = target.source_quote
        target.evidence = [*target.evidence, *item.evidence]
    return merged


def _normalize_merge_value(value: Any) -> str:
    if value is None:
        return ""
    normalized = " ".join(str(value).strip().lower().split())
    return "" if normalized == "未明确" else normalized
