from __future__ import annotations

from pydantic import BaseModel

from yuxi.knowledge.extraction.schemas import DocumentCategoryResult, field_description_lines


def build_category_prompt(chunk_text: str) -> str:
    fields = "\n".join(field_description_lines(DocumentCategoryResult))
    return f"""你是企业管理文档分类助手。只根据给定文本判断类别，不能编造。

输出必须是 JSON 对象，字段为 DocumentCategoryResult。每个类别包含 matched 和 evidence。
类别说明：
{fields}

文本：
{chunk_text}
"""


def build_extraction_prompt(schema: type[BaseModel], chunk_text: str) -> str:
    fields = "\n".join(field_description_lines(schema))
    return f"""你是企业管理文档结构化抽取助手。只从给定文本抽取 {schema.__name__}。

规则：
1. 只能返回 JSON 对象，格式为 {{"items": [...]}}。
2. 没有明确证据时返回 {{"items": []}}。
3. source_quote 必须逐字摘录原文，不要改写。
4. 不确定的可选字段填 null，不要猜测。

字段说明：
{fields}

文本：
{chunk_text}
"""
