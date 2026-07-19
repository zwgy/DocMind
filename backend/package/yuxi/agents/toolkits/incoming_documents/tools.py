from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field, StringConstraints, model_validator

from yuxi.agents.toolkits.registry import tool
from yuxi.document_extraction.schemas import (
    document_category_label,
    document_category_label_mapping,
    extraction_schema_display_metadata,
    normalize_document_category_ids,
)
from yuxi.repositories.document_business_extraction_repository import DocumentBusinessExtractionRepository
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.services.incoming_document_markdown_service import (
    IncomingDocumentMarkdownError,
    IncomingDocumentMarkdownService,
)

INCOMING_TOOL_CONFIG_GUIDE = "由来文业务 Skill 按需加载，不作为 Agent 基础工具直接配置。"

FilterValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SourceFileId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class IncomingDocumentFilters(BaseModel):
    date_from: date | None = Field(default=None, description="来文日期起点，格式 YYYY-MM-DD")
    date_to: date | None = Field(default=None, description="来文日期终点，格式 YYYY-MM-DD")
    classifications: list[FilterValue] = Field(
        default_factory=list,
        max_length=50,
        description="有效主分类列表，支持稳定 ID 或当前中文名称，列表内按任一匹配",
    )
    item_types: list[FilterValue] = Field(
        default_factory=list,
        max_length=50,
        description="正式结构化结果的条目类型列表，支持内部 ID 或当前中文名称",
    )
    keyword: str | None = Field(default=None, max_length=200, description="标题、文号或附件文件名关键词")

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        return self


class SearchIncomingDocumentsInput(IncomingDocumentFilters):
    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=100, description="每页文档数，最多 100")


class ReadIncomingDocumentInput(BaseModel):
    incoming_id: str = Field(min_length=1, max_length=64, description="来文 ID")
    source_file_ids: list[SourceFileId] | None = Field(
        default=None,
        max_length=100,
        description="需要写入 sandbox 的附件 source_file_id；读取全文时必填",
    )
    include_full_text: bool = Field(default=False, description="是否将选定附件 Markdown 写入当前线程 sandbox")


def _iso(value: Any) -> Any:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _document_payload(document) -> dict[str, Any]:
    classification = document.confirmed_classification or document.ai_classification
    return {
        "incoming_id": document.incoming_id,
        "source_system": document.source_system,
        "source_function_id": document.source_function_id,
        "source_document_id": document.source_document_id,
        "document_metadata": document.document_metadata or {},
        "classification": classification,
        "classification_label": document_category_label(classification),
        "ai_classification": document.ai_classification,
        "ai_classification_label": document_category_label(document.ai_classification),
        "classification_confidence": document.classification_confidence,
        "classification_evidence": document.classification_evidence,
        "additional_classifications": [
            item | {"classification_label": document_category_label(item.get("classification"))}
            for item in document.additional_classifications or []
            if isinstance(item, dict)
        ],
        "summary": document.summary,
        "status": document.status,
        "review_status": document.review_status,
        "created_at": _iso(document.created_at),
    }


def _item_type_labels() -> dict[str, str]:
    """复用抽取 Schema 的显示元数据，避免工具和业务模型各维护一份映射。"""
    return extraction_schema_display_metadata().get("schemaLabels") or {}


def _normalize_item_types(values: list[str] | None) -> list[str]:
    """将模型传入的条目 ID 或中文名称统一为数据库保存的 Schema ID。"""
    labels = _item_type_labels()
    lookup = {value.casefold(): schema_id for schema_id, label in labels.items() for value in (schema_id, label)}
    normalized: list[str] = []
    unknown: list[str] = []
    for raw_value in values or []:
        value = raw_value.strip()
        schema_id = lookup.get(value.casefold())
        if schema_id is None:
            unknown.append(value)
        elif schema_id not in normalized:
            normalized.append(schema_id)
    if unknown:
        supported = "、".join(f"{label}（{schema_id}）" for schema_id, label in labels.items())
        raise ValueError(f"未知条目类型：{'、'.join(unknown)}。当前支持：{supported}")
    return normalized


def _result_groups(extraction: dict[str, Any] | None) -> list[dict[str, Any]]:
    labels = _item_type_labels()
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in (extraction or {}).get("items") or []:
        groups.setdefault(item.get("item_type") or "unknown", []).append(item)
    return [
        {
            "item_type": item_type,
            "item_type_label": labels.get(item_type, item_type),
            "summary": _group_summary(details),
            "details": details,
        }
        for item_type, details in groups.items()
    ]


def _group_summary(details: list[dict[str, Any]]) -> str:
    data = (details[0].get("data") or {}) if details else {}
    return next(
        (str(value) for key, value in data.items() if key != "source_quote" and value not in (None, "", [], {})),
        "已提取业务事项",
    )


def _runtime_thread_scope(runtime: ToolRuntime | None) -> tuple[str, str]:
    """原文落盘必须使用当前运行时会话，查询和统计无需依赖用户权限。"""
    context = getattr(runtime, "context", None)
    uid = str(getattr(context, "uid", None) or "").strip()
    thread_id = str(getattr(context, "file_thread_id", None) or getattr(context, "thread_id", None) or "").strip()
    if not uid or not thread_id:
        raise ValueError("当前运行时缺少 uid 或 thread_id")
    return uid, thread_id


@tool(
    category="incoming_document",
    tags=["来文", "检索"],
    display_name="查询来文",
    config_guide=INCOMING_TOOL_CONFIG_GUIDE,
    args_schema=SearchIncomingDocumentsInput,
)
async def search_incoming_documents(
    date_from: date | None = None,
    date_to: date | None = None,
    classifications: list[str] | None = None,
    item_types: list[str] | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any] | str:
    """按时间、分类、条目类型或关键词查找来文；先用本工具定位来文，再按需读取详情。"""
    try:
        normalized_classifications = normalize_document_category_ids(classifications)
        normalized_item_types = _normalize_item_types(item_types)
    except ValueError as exc:
        return str(exc)

    repo = IncomingDocumentRepository()
    documents, total = await repo.search_business_documents(
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
        classifications=normalized_classifications or None,
        item_types=normalized_item_types or None,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    facets = await repo.get_business_document_facets([document.incoming_id for document in documents])
    return {
        "items": [
            _document_payload(document)
            | {
                "item_types": facets[document.incoming_id]["item_types"],
                "attachment_count": facets[document.incoming_id]["attachment_count"],
            }
            for document in documents
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "classification_labels": document_category_label_mapping(),
        "item_type_labels": _item_type_labels(),
    }


@tool(
    category="incoming_document",
    tags=["来文", "读取"],
    display_name="读取来文",
    config_guide=INCOMING_TOOL_CONFIG_GUIDE,
    args_schema=ReadIncomingDocumentInput,
)
async def read_incoming_document(
    incoming_id: str,
    source_file_ids: list[str] | None = None,
    include_full_text: bool = False,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """读取来文结论、附件和结构化结果；核验原文时再落盘指定附件，并用 read_file 读取返回路径。"""
    if include_full_text and not source_file_ids:
        return "include_full_text=true 时必须指定 source_file_ids"

    incoming_repo = IncomingDocumentRepository()
    document = await incoming_repo.get_by_incoming_id(incoming_id)
    if document is None:
        return f"来文不存在: {incoming_id}"
    files = await incoming_repo.list_files(incoming_id)
    extraction = (
        await DocumentBusinessExtractionRepository().get_latest_by_incoming_id(incoming_id)
        if document.status == "ready"
        else None
    )
    markdown_files = []
    if include_full_text:
        try:
            uid, thread_id = _runtime_thread_scope(runtime)
            markdown_files = await IncomingDocumentMarkdownService(incoming_repo).materialize(
                incoming_id=incoming_id,
                source_file_ids=source_file_ids or [],
                uid=uid,
                thread_id=thread_id,
            )
        except (IncomingDocumentMarkdownError, ValueError) as exc:
            return f"读取来文原文失败：{exc}"

    return _document_payload(document) | {
        "files": [
            {
                "source_file_id": file.source_file_id,
                "filename": file.filename,
                "is_main_file": file.is_main_file,
                "status": file.status,
                "has_markdown": bool(file.markdown_file_url),
            }
            for file in files
        ],
        "result_groups": _result_groups(extraction),
        "categories": (extraction or {}).get("categories") or {},
        "schema_ids": (extraction or {}).get("schema_ids") or [],
        "classification_labels": document_category_label_mapping(),
        "item_type_labels": _item_type_labels(),
        "markdown_files": markdown_files,
    }


@tool(
    category="incoming_document",
    tags=["来文", "统计"],
    display_name="统计来文",
    config_guide=INCOMING_TOOL_CONFIG_GUIDE,
    args_schema=IncomingDocumentFilters,
)
async def get_incoming_document_statistics(
    date_from: date | None = None,
    date_to: date | None = None,
    classifications: list[str] | None = None,
    item_types: list[str] | None = None,
    keyword: str | None = None,
) -> dict[str, Any] | str:
    """统计筛选范围内的来文总数，并按分类、条目类型和月份聚合。"""
    try:
        normalized_classifications = normalize_document_category_ids(classifications)
        normalized_item_types = _normalize_item_types(item_types)
    except ValueError as exc:
        return str(exc)
    result = await IncomingDocumentRepository().get_business_statistics(
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
        classifications=normalized_classifications or None,
        item_types=normalized_item_types or None,
        keyword=keyword,
    )
    labels = _item_type_labels()
    for item in result.get("by_classification") or []:
        item["classification_label"] = document_category_label(item.get("classification"))
    for item in result.get("by_item_type") or []:
        item["item_type_label"] = labels.get(item.get("item_type"), item.get("item_type"))
    result["classification_labels"] = document_category_label_mapping()
    result["item_type_labels"] = labels
    return result
