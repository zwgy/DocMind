"""问题和选项规范化工具"""

import uuid
from typing import Any


def normalize_options(raw_options: Any) -> list[dict[str, str]]:
    """规范化选项列表"""
    if not isinstance(raw_options, list):
        return []

    options: list[dict[str, str]] = []
    for item in raw_options:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("value") or "").strip()
            value = str(item.get("value") or item.get("label") or "").strip()
        else:
            label = str(item).strip()
            value = label
        if label and value:
            options.append({"label": label, "value": value})
    return options


def normalize_questions(raw_questions: Any, default_question_id_prefix: str = "q") -> list[dict[str, Any]]:
    """规范化问题列表"""
    if not isinstance(raw_questions, list):
        return []

    questions: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_questions):
        # 显式工具 Schema 校验后，LangChain 可能保留 Pydantic 子模型实例，需要先回到统一字典边界。
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue

        # 本地模型偶发沿用选项结构，把题干写到 label/text；这里只统一字段名，不补造问题。
        question = str(item.get("question") or item.get("label") or item.get("text") or "").strip()
        if not question:
            continue

        question_id = str(item.get("question_id") or f"{default_question_id_prefix}-{idx + 1}").strip()
        if not question_id:
            question_id = str(uuid.uuid4())

        normalized_question: dict[str, Any] = {
            "question_id": question_id,
            "question": question,
            "options": normalize_options(item.get("options")),
            "multi_select": bool(item.get("multi_select", False)),
            "allow_other": bool(item.get("allow_other", True)),
        }

        operation = item.get("operation")
        if isinstance(operation, str) and operation.strip():
            normalized_question["operation"] = operation.strip()

        questions.append(normalized_question)

    return questions
