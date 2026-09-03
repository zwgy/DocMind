from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any

from langchain_core.tools import ToolException
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, model_validator

from yuxi.agents.toolkits.buildin.tools import ask_user_question
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
    IncomingDocumentOriginalFileError,
)

INCOMING_TOOL_CONFIG_GUIDE = "由来文业务 Skill 按需加载，不作为 Agent 基础工具直接配置。"

FilterValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SourceFileId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


def _decode_source_file_ids(value: Any) -> Any:
    # 本地模型偶尔会把数组参数编码成 JSON 字符串；只解析合法 JSON，其他输入继续由 Schema 明确拒绝。
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


OptionalSourceFileIds = Annotated[
    list[SourceFileId],
    BeforeValidator(_decode_source_file_ids),
    Field(max_length=100, description="需要写入 sandbox 的附件 source_file_id；读取全文时必填"),
]
RequiredSourceFileIds = Annotated[
    list[SourceFileId],
    BeforeValidator(_decode_source_file_ids),
    Field(min_length=1, max_length=100, description="需要下载的主文件或附件 source_file_id"),
]


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
    title: str | None = Field(default=None, max_length=200, description="来文标题关键词")
    document_number: str | None = Field(default=None, max_length=100, description="来文文号关键词")
    source_unit: str | None = Field(default=None, max_length=100, description="发文单位关键词")
    keyword: str | None = Field(
        default=None,
        max_length=200,
        description="同时匹配标题、文号、发文单位、摘要或附件文件名的关键词",
    )

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        return self


class SearchIncomingDocumentsInput(IncomingDocumentFilters):
    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=100, description="每页文档数，最多 100")


def _iso(value: Any) -> Any:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _document_payload(document) -> dict[str, Any]:
    classification = document.confirmed_classification or document.ai_classification
    return {
        "incoming_id": document.incoming_id,
        "source_system": document.source_system,
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
    config = getattr(runtime, "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    context = getattr(runtime, "context", None)
    state = getattr(runtime, "state", None)

    def runtime_value(key: str) -> str:
        for source in (configurable, context, state):
            value = source.get(key) if isinstance(source, Mapping) else getattr(source, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    uid = runtime_value("uid")
    thread_id = runtime_value("file_thread_id") or runtime_value("thread_id")
    if not uid or not thread_id:
        raise ValueError("当前运行时缺少 uid 或 thread_id")
    return uid, thread_id


def _tool_message_payload(message: Any) -> Mapping[str, Any] | None:
    content = getattr(message, "content", "")
    if isinstance(content, Mapping):
        return content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, Mapping) else None
    return None


def _current_search_candidates(runtime: ToolRuntime | None) -> tuple[str, list[dict[str, Any]], str]:
    """只读取当前用户轮次最近一次搜索及其结构化选择，避免复用旧候选。"""
    state = getattr(runtime, "state", None)
    messages = state.get("messages", []) if isinstance(state, Mapping) else getattr(state, "messages", [])
    messages = list(messages or [])
    latest_user_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if getattr(messages[index], "type", None) == "human"),
        None,
    )
    if latest_user_index is None:
        return "", [], ""

    user_content = getattr(messages[latest_user_index], "content", "")
    user_message = user_content.strip() if isinstance(user_content, str) else ""
    tool_messages = [
        message for message in messages[latest_user_index + 1 :] if getattr(message, "type", None) == "tool"
    ]
    selected_incoming_id = ""
    if tool_messages and getattr(tool_messages[-1], "name", None) == "ask_user_question":
        answer_payload = _tool_message_payload(tool_messages[-1])
        answers = answer_payload.get("answer") if answer_payload else None
        selected = answers.get("incoming_id") if isinstance(answers, Mapping) else None
        if isinstance(selected, Mapping) and selected.get("type") == "other":
            selected = selected.get("text")
        selected_incoming_id = str(selected or "").strip()

    for message in reversed(tool_messages):
        if getattr(message, "name", None) != "search_incoming_documents":
            continue
        payload = _tool_message_payload(message)
        if payload is None:
            return user_message, [], ""
        items = payload.get("items") if isinstance(payload, Mapping) else None
        total = payload.get("total") if isinstance(payload, Mapping) else 0
        candidates = list(items or []) if isinstance(total, int) and total > 1 else []
        return user_message, candidates, selected_incoming_id
    return user_message, [], ""


def _document_identified_in_message(user_message: str, document: Any) -> bool:
    """完整标题、文号或业务 ID 已由用户给出时，不重复要求选择。"""
    metadata = document.document_metadata or {}
    identifiers = (
        document.incoming_id,
        document.source_document_id,
        metadata.get("title"),
        metadata.get("document_number"),
    )
    folded_message = user_message.casefold()
    return any(
        isinstance(identifier, str) and identifier.strip() and identifier.strip().casefold() in folded_message
        for identifier in identifiers
    )


def _owns_ambiguity_prompt(runtime: ToolRuntime | None) -> bool:
    """并行读取多个候选时只让首个工具调用发起一次 interrupt。"""
    tool_call_id = getattr(runtime, "tool_call_id", None)
    if not tool_call_id:
        return True
    state = getattr(runtime, "state", None)
    messages = state.get("messages", []) if isinstance(state, Mapping) else getattr(state, "messages", [])
    for message in reversed(messages or []):
        if getattr(message, "type", None) != "ai":
            continue
        read_call_ids = [
            call.get("id")
            for call in getattr(message, "tool_calls", []) or []
            if call.get("name") == "read_incoming_document"
        ]
        return not read_call_ids or tool_call_id == read_call_ids[0]
    return True


def _resolve_ambiguous_document_choice(
    runtime: ToolRuntime | None,
    incoming_id: str,
    document: Any,
) -> str:
    """单篇操作命中多份时在工具边界阻止模型自行挑选。"""
    user_message, candidates, selected_incoming_id = _current_search_candidates(runtime)
    if not candidates or _document_identified_in_message(user_message, document):
        return incoming_id
    candidate_ids = {str(candidate.get("incoming_id") or "") for candidate in candidates}
    if selected_incoming_id in candidate_ids:
        return selected_incoming_id
    if len(candidates) < 2:
        raise ToolException("命中多篇来文但当前页候选不足，请将 page_size 调整为至少 2 后重新搜索")
    if not _owns_ambiguity_prompt(runtime):
        raise ToolException("目标不唯一，已在同批读取中请求用户选择，请等待选择结果")

    options = []
    for candidate in candidates[:5]:
        metadata = candidate.get("document_metadata") or {}
        title = metadata.get("title") or candidate.get("incoming_id")
        number = metadata.get("document_number")
        options.append(
            {
                "label": f"{title}（{number}）" if number else str(title),
                "value": candidate.get("incoming_id"),
            }
        )
    answer = ask_user_question.func(
        questions=[
            {
                "question_id": "incoming_id",
                "question": "请选择要查看的来文",
                "options": options,
                "multi_select": False,
                "allow_other": True,
            }
        ]
    )
    selected: Any = (answer.get("answer") or {}).get("incoming_id") if isinstance(answer, Mapping) else None
    if isinstance(selected, Mapping) and selected.get("type") == "other":
        selected = selected.get("text")
    selected = str(selected or "").strip()
    for candidate in candidates:
        metadata = candidate.get("document_metadata") or {}
        if selected in {
            str(candidate.get("incoming_id") or ""),
            str(metadata.get("title") or ""),
            str(metadata.get("document_number") or ""),
        }:
            return str(candidate["incoming_id"])
    raise ToolException("未能确认要查看的来文，请重新选择或提供标题、文号")


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
    title: str | None = None,
    document_number: str | None = None,
    source_unit: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any] | str:
    """按时间、分类、条目类型、标题、文号、发文单位或关键词查找来文。"""
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
        title=title,
        document_number=document_number,
        source_unit=source_unit,
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
                "has_main_file": facets[document.incoming_id]["has_main_file"],
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
)
async def read_incoming_document(
    incoming_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
        Field(description="来文 ID"),
    ],
    source_file_ids: OptionalSourceFileIds | None = None,
    include_full_text: Annotated[bool, Field(description="是否将选定附件 Markdown 写入当前线程 sandbox")] = False,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """读取来文结论、附件和结构化结果；核验原文时再落盘指定附件，并用 read_file 读取返回路径。"""
    if include_full_text and not source_file_ids:
        return "include_full_text=true 时必须指定 source_file_ids"

    incoming_repo = IncomingDocumentRepository()
    document = await incoming_repo.get_by_incoming_id(incoming_id)
    if document is None:
        return f"来文不存在: {incoming_id}"
    if not include_full_text:
        selected_incoming_id = _resolve_ambiguous_document_choice(runtime, incoming_id, document)
        if selected_incoming_id != incoming_id:
            incoming_id = selected_incoming_id
            document = await incoming_repo.get_by_incoming_id(incoming_id)
            if document is None:
                return f"来文不存在: {incoming_id}"
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
            raise ToolException(f"读取来文原文失败：{exc}") from exc
        # 原文模式只负责把指定附件交付到会话目录，避免重复返回与文件无关的整套结构化结果。
        return {"incoming_id": incoming_id, "markdown_files": markdown_files}

    extraction = (
        await DocumentBusinessExtractionRepository().get_latest_by_incoming_id(incoming_id)
        if document.status == "ready"
        else None
    )
    files = await incoming_repo.list_files(incoming_id)

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
        "markdown_files": [],
    }


@tool(
    category="incoming_document",
    tags=["来文", "下载"],
    display_name="下载来文原始文件",
    config_guide=INCOMING_TOOL_CONFIG_GUIDE,
)
async def download_incoming_document_files(
    incoming_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
        Field(description="来文 ID"),
    ],
    source_file_ids: RequiredSourceFileIds,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """将指定来文的原始主文件或附件写入当前线程 outputs，供用户预览或下载。"""
    try:
        uid, thread_id = _runtime_thread_scope(runtime)
        original_files = await IncomingDocumentMarkdownService().materialize_original(
            incoming_id=incoming_id,
            source_file_ids=source_file_ids,
            uid=uid,
            thread_id=thread_id,
        )
    except (IncomingDocumentOriginalFileError, ValueError) as exc:
        raise ToolException(f"下载来文原始文件失败：{exc}") from exc
    return {"incoming_id": incoming_id, "original_files": original_files}


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
    title: str | None = None,
    document_number: str | None = None,
    source_unit: str | None = None,
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
        title=title,
        document_number=document_number,
        source_unit=source_unit,
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
