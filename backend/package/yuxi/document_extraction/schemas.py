from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from yuxi.scheduled_jobs.schemas import Schedule


class AdditionalClassification(BaseModel):
    """有独立原文证据支持的附加业务分类。"""

    classification: str = Field(description="稳定分类 ID，例如 risk_management")
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)


class IncomingDocumentClassificationResult(BaseModel):
    """来文摘要阶段的模型输出。"""

    classification: str = Field(description="稳定分类 ID，例如 risk_management")
    classification_confidence: float = Field(ge=0, le=1)
    classification_evidence: str | None = Field(default=None)
    summary: str = Field(min_length=1)
    additional_classifications: list[AdditionalClassification] = Field(default_factory=list)


class IncomingAttachmentSummary(BaseModel):
    """副附件的轻量内容摘要；不承担来文分类或业务条目抽取职责。"""

    summary: str = Field(min_length=1, description="基于附件原文的简洁内容摘要")


class CategoryDecision(BaseModel):
    matched: bool = Field(default=False, description="该类别是否命中文档或片段内容")
    evidence: str | None = Field(default=None, description="判断该类别成立的原文依据；未命中时为 null")


class DocumentCategoryResult(BaseModel):
    notification: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="通报类：包含通报、批评、问题曝光、情况通报、后续整改要求等内容",
        json_schema_extra={"label": "通报类", "extraction_schemas": ["reward_punishment_item", "task_item"]},
    )
    assessment: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="考评类：包含考评项目、排名、成绩、评价结果、考评原因等内容",
        json_schema_extra={"label": "考评类", "extraction_schemas": ["assessment_item", "reward_punishment_item"]},
    )
    reward_punishment: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="奖励、表彰、处罚类：包含奖励、表彰、处罚、问责、批评等内容",
        json_schema_extra={"label": "奖惩处置类", "extraction_schemas": ["reward_punishment_item", "task_item"]},
    )
    regulation: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="规章制度类：制度、规定、办法、流程、长期遵循要求等规范性文件",
        json_schema_extra={"label": "规章制度类", "extraction_schemas": ["management_requirement_item"]},
    )
    technical_standard: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="技术规范、标准、管理要求类：包含技术标准、作业标准、管理要求、专业要求等内容",
        json_schema_extra={"label": "技术标准类", "extraction_schemas": ["management_requirement_item", "task_item"]},
    )
    safety_management: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="安全管理类：包含安全生产、现场作业、安全检查、平安建设等内容",
        json_schema_extra={
            "label": "安全管理类",
            "extraction_schemas": ["risk_item", "task_item", "management_requirement_item"],
        },
    )
    risk_management: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="风险管理类：包含安全风险、网络安全风险、平安建设风险、风险防控要求等内容",
        json_schema_extra={
            "label": "风险管理类",
            "extraction_schemas": ["risk_item", "task_item", "management_requirement_item"],
        },
    )
    staged_work: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="阶段性工作类：专项行动、近期重点工作、阶段安排、阶段性任务等内容",
        json_schema_extra={"label": "阶段性工作类", "extraction_schemas": ["task_item"]},
    )
    long_term_requirement: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="长期性、持续性管理要求类：长期执行、周期性管理要求、持续整改要求等内容",
        json_schema_extra={
            "label": "长期管理要求类",
            "extraction_schemas": ["task_item", "management_requirement_item"],
        },
    )
    general: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="通用类：仅当文档不符合任何其他业务类别时命中，用于提取核心事实、结论、说明或请求",
        json_schema_extra={"label": "通用类", "extraction_schemas": ["general_item"]},
    )


PeriodType = Literal["阶段性", "长期性", "周期性", "未明确"]


def _normalize_period_type(value: Any) -> Any:
    # 模型常用 null 表示原文未说明周期，业务契约中该语义就是“未明确”；其他非法值仍由枚举校验拒绝。
    return "未明确" if value is None else value


ExtractedPeriodType = Annotated[PeriodType, BeforeValidator(_normalize_period_type)]


class RiskItem(BaseModel):
    model_config = {"json_schema_extra": {"label": "风险事项"}}

    risk_name: str = Field(
        description="需要用户重点关注的关键风险事项名称，必须有原文支持，不要自行编造",
        json_schema_extra={"label": "风险名称"},
    )
    department: str | None = Field(
        default=None,
        description="涉及部门；原文没有明确部门时为 null",
        json_schema_extra={"label": "涉及部门"},
    )
    profession: str | None = Field(
        default=None,
        description="涉及专业，例如安全、网络安全、运维等；没有则为 null",
        json_schema_extra={"label": "涉及专业"},
    )
    role: str | None = Field(
        default=None,
        description="涉及岗位、人员角色或责任对象；没有则为 null",
        json_schema_extra={"label": "涉及岗位、角色"},
    )
    period_type: ExtractedPeriodType = Field(
        default="未明确",
        description="风险是阶段性、长期性、周期性还是未明确",
        json_schema_extra={"label": "风险类型"},
    )
    requirement: str | None = Field(
        default=None,
        description="对应的管理要求、整改要求或防控措施",
        json_schema_extra={"label": "管理要求"},
    )
    source_quote: str = Field(
        description="帮助后续回读原文的参考片段，必须基于输入文本",
        json_schema_extra={"label": "参考片段"},
    )


class TaskItem(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True, "json_schema_extra": {"label": "任务要求"}}

    task_name: str = Field(
        description="需要用户重点跟进的关键任务、整改要求或工作要求名称，必须有原文支持",
        json_schema_extra={"label": "任务名称"},
    )
    notification_title: str | None = Field(
        default=None,
        max_length=100,
        description="需要发送的通知标题；原文未明确时为 null，不能自行编造",
        json_schema_extra={"label": "通知标题"},
    )
    notification_content: str | None = Field(
        default=None,
        max_length=4000,
        description="需要发送的通知正文；原文未明确时为 null，不能自行编造",
        json_schema_extra={"label": "通知内容"},
    )
    raw_time_expression: str | None = Field(
        default=None,
        max_length=4000,
        description="原文中的时间表达；无法规范化为调度规则时仍保留，供人工补全",
        json_schema_extra={"label": "原始时间表达"},
    )
    schedule: Schedule | None = Field(
        default=None,
        description="可直接执行的触发规则；时间含糊或缺失时必须为 null",
        json_schema_extra={"label": "调度规则"},
    )
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="调度规则采用的 IANA 时区；未明确时由候选适配器采用系统默认时区",
        json_schema_extra={"label": "任务时区"},
    )
    department: str | None = Field(
        default=None,
        description="责任部门；原文没有明确部门时为 null",
        json_schema_extra={"label": "责任部门"},
    )
    role: str | None = Field(
        default=None,
        description="责任岗位、责任人或角色；没有则为 null",
        json_schema_extra={"label": "责任岗位、角色"},
    )
    deadline: str | None = Field(
        default=None,
        description="明确时间节点、周期或截止日期；没有则为 null",
        json_schema_extra={"label": "时间节点"},
    )
    recipient_expression: str | None = Field(
        default=None,
        description="原文中的接收人或范围表达；没有明确对象时为 null",
        json_schema_extra={"label": "接收人表达"},
    )
    raw_recipient_expression: str | None = Field(
        default=None,
        max_length=4000,
        description="原文中的接收人表达；与兼容字段 recipient_expression 保持相同语义",
        json_schema_extra={"label": "原始接收人表达"},
    )
    recipient_scope: Literal["named", "all", "unknown"] = Field(
        default="unknown",
        description="接收人是具名人员、全体范围或无法确定",
        json_schema_extra={"label": "接收人范围"},
    )
    recipient_names: list[str] = Field(
        default_factory=list,
        description="仅当接收人范围为 named 时填写原文姓名，不填写 UID",
        json_schema_extra={"label": "接收人姓名"},
    )
    period_type: ExtractedPeriodType = Field(
        default="未明确",
        description="任务是阶段性、长期性、周期性还是未明确",
        json_schema_extra={"label": "任务类型"},
    )
    source_quote: str = Field(
        description="帮助后续回读原文的参考片段，必须基于输入文本",
        json_schema_extra={"label": "参考片段"},
    )
    source_file_id: str | None = Field(
        default=None,
        max_length=512,
        description="引用原文所在的来源文件 ID；缺失时由抽取结果证据补全",
        json_schema_extra={"label": "来源文件 ID"},
    )
    source_location: str | None = Field(
        default=None,
        max_length=500,
        description="原文中的页码、段落或字符范围定位",
        json_schema_extra={"label": "来源定位"},
    )

    @model_validator(mode="after")
    def validate_recipient_scope(self) -> TaskItem:
        """防止模型在未能确定人员范围时伪造姓名列表。"""
        if self.recipient_scope == "named" and not self.recipient_names:
            raise ValueError("recipient_scope 为 named 时 recipient_names 不能为空")
        if self.recipient_scope != "named" and self.recipient_names:
            raise ValueError("recipient_scope 为 all 或 unknown 时 recipient_names 必须为空")
        return self


class AssessmentItem(BaseModel):
    model_config = {"json_schema_extra": {"label": "考评事项"}}

    target: str = Field(
        description="被考评的部门、岗位、人员或对象",
        json_schema_extra={"label": "考评对象"},
    )
    project: str | None = Field(
        default=None,
        description="考评项目、指标或事项；没有则为 null",
        json_schema_extra={"label": "考评项目"},
    )
    reason: str | None = Field(
        default=None,
        description="考评原因或依据；没有则为 null",
        json_schema_extra={"label": "考评原因"},
    )
    result: str | None = Field(
        default=None,
        description="考评结果、排名、成绩或评价；没有则为 null",
        json_schema_extra={"label": "考评结果"},
    )
    source_quote: str = Field(
        description="帮助后续回读原文的参考片段，必须基于输入文本",
        json_schema_extra={"label": "参考片段"},
    )


class RewardPunishmentItem(BaseModel):
    model_config = {"json_schema_extra": {"label": "奖惩处置"}}

    target: str = Field(
        description="被通报、表彰、奖励、处罚或批评的部门、岗位、人员或对象",
        json_schema_extra={"label": "处置对象"},
    )
    action_type: str = Field(
        description="通报、表彰、奖励、处罚、批评、问责等类型",
        json_schema_extra={"label": "处置类型"},
    )
    reason: str | None = Field(
        default=None,
        description="原因、依据或背景；没有则为 null",
        json_schema_extra={"label": "原因依据"},
    )
    result: str | None = Field(
        default=None,
        description="处理结果、奖励结果、处罚结果或后续影响；没有则为 null",
        json_schema_extra={"label": "处置结果"},
    )
    requirement: str | None = Field(
        default=None,
        description="整改要求或后续要求；没有则为 null",
        json_schema_extra={"label": "后续要求"},
    )
    source_quote: str = Field(
        description="帮助后续回读原文的参考片段，必须基于输入文本",
        json_schema_extra={"label": "参考片段"},
    )


class ManagementRequirementItem(BaseModel):
    model_config = {"json_schema_extra": {"label": "管理要求"}}

    requirement: str = Field(
        description="需要用户重点掌握的核心管理要求、制度要求、技术标准或长期要求",
        json_schema_extra={"label": "管理要求"},
    )
    department: str | list[str] | None = Field(
        default=None,
        description="涉及部门；多个部门使用字符串数组，单个部门使用字符串，没有则为 null",
        json_schema_extra={"label": "涉及部门"},
    )
    role: str | list[str] | None = Field(
        default=None,
        description="涉及岗位、角色或人员；多个对象使用字符串数组，单个对象使用字符串，没有则为 null",
        json_schema_extra={"label": "涉及岗位、角色"},
    )
    period_type: ExtractedPeriodType = Field(
        default="未明确",
        description="要求是阶段性、长期性、周期性还是未明确",
        json_schema_extra={"label": "周期类型"},
    )
    source_quote: str = Field(
        description="帮助后续回读原文的参考片段，必须基于输入文本",
        json_schema_extra={"label": "参考片段"},
    )


class GeneralItem(BaseModel):
    model_config = {"json_schema_extra": {"label": "通用事项"}}

    content: str = Field(
        description="可独立理解的核心事实、结论、说明或请求；不要抽取背景套话",
        json_schema_extra={"label": "事项内容"},
    )
    subject: str | None = Field(
        default=None,
        description="事项涉及的单位、人员或对象；没有则为 null",
        json_schema_extra={"label": "涉及对象"},
    )
    time: str | None = Field(
        default=None,
        description="原文明示的时间、日期或期限；没有则为 null",
        json_schema_extra={"label": "相关时间"},
    )
    source_quote: str = Field(
        description="帮助后续回读原文的参考片段，必须基于输入文本",
        json_schema_extra={"label": "参考片段"},
    )


EXTRACTION_SCHEMAS: dict[str, type[BaseModel]] = {
    "risk_item": RiskItem,
    "task_item": TaskItem,
    "assessment_item": AssessmentItem,
    "reward_punishment_item": RewardPunishmentItem,
    "management_requirement_item": ManagementRequirementItem,
    "general_item": GeneralItem,
}


def get_extraction_schema(schema_id: str) -> type[BaseModel]:
    try:
        return EXTRACTION_SCHEMAS[schema_id]
    except KeyError as exc:
        raise ValueError(f"Unknown extraction schema: {schema_id}") from exc


def field_description_lines(model: type[BaseModel]) -> list[str]:
    lines = []
    for name, field in model.model_fields.items():
        description = field.description or ""
        lines.append(f"- {name}: {description}")
    return lines


def document_category_label_mapping() -> dict[str, str]:
    return {
        name: str((field.json_schema_extra or {}).get("label") or name)
        for name, field in DocumentCategoryResult.model_fields.items()
    }


def document_category_id(value: str | None) -> str | None:
    """将稳定 ID 或当前中文名称统一为稳定 ID。"""
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    labels = document_category_label_mapping()
    return next(
        (
            category_id
            for category_id, label in labels.items()
            if normalized in {category_id.casefold(), label.casefold()}
        ),
        None,
    )


def normalize_document_category_ids(values: list[str] | None) -> list[str]:
    """严格归一分类筛选值，避免错误名称静默返回空结果。"""
    normalized: list[str] = []
    unknown: list[str] = []
    for value in values or []:
        category_id = document_category_id(value)
        if category_id is None:
            unknown.append(str(value).strip())
        elif category_id not in normalized:
            normalized.append(category_id)
    if unknown:
        supported = "、".join(
            f"{label}（{category_id}）" for category_id, label in document_category_label_mapping().items()
        )
        raise ValueError(f"未知分类：{'、'.join(unknown)}。当前支持：{supported}")
    return normalized


def document_category_label(category_id: str | None) -> str | None:
    if category_id is None:
        return None
    return document_category_label_mapping().get(category_id, category_id)


def extraction_schema_display_metadata(schema_ids: list[str] | None = None) -> dict[str, Any]:
    selected_ids = [
        schema_id for schema_id in (schema_ids or list(EXTRACTION_SCHEMAS)) if schema_id in EXTRACTION_SCHEMAS
    ]
    schema_labels: dict[str, str] = {}
    field_labels: dict[str, dict[str, str]] = {}
    for schema_id in selected_ids:
        model = EXTRACTION_SCHEMAS[schema_id]
        schema_extra = model.model_config.get("json_schema_extra") or {}
        schema_labels[schema_id] = str(schema_extra.get("label") or schema_id)
        field_labels[schema_id] = {
            name: str((field.json_schema_extra or {}).get("label") or name)
            for name, field in model.model_fields.items()
        }
    return {
        "categoryLabels": document_category_label_mapping(),
        "schemaLabels": schema_labels,
        "fieldLabels": field_labels,
    }


def extraction_schema_ids_for_categories(categories: dict[str, bool | CategoryDecision]) -> list[str]:
    selected: list[str] = []
    for category_id, value in categories.items():
        matched = value.matched if isinstance(value, CategoryDecision) else bool(value)
        if not matched:
            continue
        field = DocumentCategoryResult.model_fields.get(category_id)
        if not field:
            continue
        extra = field.json_schema_extra or {}
        for schema_id in extra.get("extraction_schemas", []):
            if schema_id not in selected:
                selected.append(schema_id)
    return selected


def category_result_for_classification_labels(labels: list[str] | None) -> DocumentCategoryResult:
    result = DocumentCategoryResult()
    matched_names = normalize_document_category_ids(labels)
    # 通用类仅作为兜底，不能与明确业务类型同时触发抽取。
    if any(name != "general" for name in matched_names):
        matched_names = [name for name in matched_names if name != "general"]
    for name in matched_names:
        field = DocumentCategoryResult.model_fields[name]
        label = str((field.json_schema_extra or {}).get("label") or name)
        setattr(result, name, CategoryDecision(matched=True, evidence=f"摘要阶段分类：{label}"))
    return result


def category_result_for_classification_label(label: str | None) -> DocumentCategoryResult:
    return category_result_for_classification_labels([label] if label else [])


def category_result_to_mapping(result: DocumentCategoryResult) -> dict[str, bool]:
    return {name: getattr(result, name).matched for name in DocumentCategoryResult.model_fields}


def category_result_to_dump(result: DocumentCategoryResult) -> dict[str, Any]:
    return result.model_dump()
