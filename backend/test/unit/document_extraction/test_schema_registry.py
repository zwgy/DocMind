from yuxi.document_extraction.schemas import (
    DocumentCategoryResult,
    extraction_schema_ids_for_categories,
    field_description_lines,
    get_extraction_schema,
)


def test_category_fields_carry_extraction_schema_mapping():
    schema_ids = extraction_schema_ids_for_categories({"risk_management": True, "assessment": True})

    assert "risk_item" in schema_ids
    assert "task_item" in schema_ids
    assert "assessment_item" in schema_ids


def test_category_schema_descriptions_are_prompt_ready():
    lines = field_description_lines(DocumentCategoryResult)

    assert any("risk_management" in line and "风险管理类" in line for line in lines)


def test_extraction_schema_exposes_field_descriptions():
    schema = get_extraction_schema("risk_item")
    lines = field_description_lines(schema)

    assert any("risk_name" in line and "风险事项" in line for line in lines)
    assert any("source_quote" in line and "原文" in line for line in lines)
