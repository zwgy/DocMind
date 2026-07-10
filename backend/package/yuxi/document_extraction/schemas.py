from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
        json_schema_extra={"label": "安全管理类", "extraction_schemas": ["risk_item", "task_item", "management_requirement_item"]},
    )
    risk_management: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="风险管理类：包含安全风险、网络安全风险、平安建设风险、风险防控要求等内容",
        json_schema_extra={"label": "风险管理类", "extraction_schemas": ["risk_item", "task_item", "management_requirement_item"]},
    )
    staged_work: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="阶段性工作类：专项行动、近期重点工作、阶段安排、阶段性任务等内容",
        json_schema_extra={"label": "阶段性工作类", "extraction_schemas": ["task_item"]},
    )
    long_term_requirement: CategoryDecision = Field(
        default_factory=CategoryDecision,
        description="长期性、持续性管理要求类：长期执行、周期性管理要求、持续整改要求等内容",
        json_schema_extra={"label": "长期管理要求类", "extraction_schemas": ["task_item", "management_requirement_item"]},
    )


PeriodType = Literal["阶段性", "长期性", "周期性", "未明确"]


class RiskItem(BaseModel):
    model_config = {"json_schema_extra": {"label": "风险事项"}}

    risk_name: str = Field(
        description="风险事项名称，必须来自原文，不要自行编造",
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
    period_type: PeriodType = Field(
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
        description="支持该风险抽取结果的原文片段，必须逐字摘录",
        json_schema_extra={"label": "原文依据"},
    )


class TaskItem(BaseModel):
    model_config = {"json_schema_extra": {"label": "任务要求"}}

    task_name: str = Field(
        description="任务、整改要求或工作要求名称，必须来自原文",
        json_schema_extra={"label": "任务名称"},
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
    period_type: PeriodType = Field(
        default="未明确",
        description="任务是阶段性、长期性、周期性还是未明确",
        json_schema_extra={"label": "任务类型"},
    )
    source_quote: str = Field(
        description="支持该任务抽取结果的原文片段，必须逐字摘录",
        json_schema_extra={"label": "原文依据"},
    )


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
        description="支持该考评抽取结果的原文片段，必须逐字摘录",
        json_schema_extra={"label": "原文依据"},
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
        description="支持该奖惩通报抽取结果的原文片段，必须逐字摘录",
        json_schema_extra={"label": "原文依据"},
    )


class ManagementRequirementItem(BaseModel):
    model_config = {"json_schema_extra": {"label": "管理要求"}}

    requirement: str = Field(
        description="管理要求、制度要求、技术标准或长期要求内容",
        json_schema_extra={"label": "管理要求"},
    )
    department: str | None = Field(
        default=None,
        description="涉及部门；没有则为 null",
        json_schema_extra={"label": "涉及部门"},
    )
    role: str | None = Field(
        default=None,
        description="涉及岗位、角色或人员；没有则为 null",
        json_schema_extra={"label": "涉及岗位、角色"},
    )
    period_type: PeriodType = Field(
        default="未明确",
        description="要求是阶段性、长期性、周期性还是未明确",
        json_schema_extra={"label": "要求类型"},
    )
    source_quote: str = Field(
        description="支持该管理要求抽取结果的原文片段，必须逐字摘录",
        json_schema_extra={"label": "原文依据"},
    )


EXTRACTION_SCHEMAS: dict[str, type[BaseModel]] = {
    "risk_item": RiskItem,
    "task_item": TaskItem,
    "assessment_item": AssessmentItem,
    "reward_punishment_item": RewardPunishmentItem,
    "management_requirement_item": ManagementRequirementItem,
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


def document_category_labels() -> list[str]:
    labels: list[str] = []
    for field in DocumentCategoryResult.model_fields.values():
        extra = field.json_schema_extra or {}
        label = str(extra.get("label") or "").strip()
        if label:
            labels.append(label)
    return labels + ["其他"]


def document_category_label_mapping() -> dict[str, str]:
    return {
        name: str((field.json_schema_extra or {}).get("label") or name)
        for name, field in DocumentCategoryResult.model_fields.items()
    }


def extraction_schema_display_metadata(schema_ids: list[str] | None = None) -> dict[str, Any]:
    selected_ids = [
        schema_id
        for schema_id in (schema_ids or list(EXTRACTION_SCHEMAS))
        if schema_id in EXTRACTION_SCHEMAS
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


def category_result_for_classification_label(label: str | None) -> DocumentCategoryResult:
    result = DocumentCategoryResult()
    normalized = str(label or "").strip()
    if not normalized:
        return result
    for name, field in DocumentCategoryResult.model_fields.items():
        extra = field.json_schema_extra or {}
        if str(extra.get("label") or "").strip() != normalized:
            continue
        # 摘要分类已经由同一套标签约束产生；复用它，避免结构化抽取阶段重复分类并被模型漏判短路。
        setattr(result, name, CategoryDecision(matched=True, evidence=f"摘要阶段分类：{normalized}"))
        return result
    return result


def category_result_to_mapping(result: DocumentCategoryResult) -> dict[str, bool]:
    return {name: getattr(result, name).matched for name in DocumentCategoryResult.model_fields}


def category_result_to_dump(result: DocumentCategoryResult) -> dict[str, Any]:
    return result.model_dump()
