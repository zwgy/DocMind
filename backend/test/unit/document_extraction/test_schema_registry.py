import pytest

from yuxi.document_extraction.schemas import (
    DocumentCategoryResult,
    category_result_for_classification_label,
    extraction_schema_display_metadata,
    extraction_schema_ids_for_categories,
    field_description_lines,
    get_extraction_schema,
    normalize_document_category_ids,
)


def test_category_fields_carry_extraction_schema_mapping():
    schema_ids = extraction_schema_ids_for_categories({"risk_management": True, "assessment": True})

    assert "risk_item" in schema_ids
    assert "task_item" in schema_ids
    assert "assessment_item" in schema_ids


def test_classification_label_maps_to_category_result():
    result = category_result_for_classification_label("规章制度类")
    schema_ids = extraction_schema_ids_for_categories({"regulation": result.regulation})

    assert result.regulation.matched is True
    assert schema_ids == ["management_requirement_item"]


def test_classification_filter_accepts_id_or_label_and_rejects_unknown():
    assert normalize_document_category_ids(["assessment", "考评类", "notification"]) == [
        "assessment",
        "notification",
    ]
    with pytest.raises(ValueError, match="未知分类.*当前支持"):
        normalize_document_category_ids(["考核类"])


def test_general_classification_maps_to_general_schema():
    result = category_result_for_classification_label("通用类")
    schema_ids = extraction_schema_ids_for_categories({"general": result.general})

    assert result.general.matched is True
    assert schema_ids == ["general_item"]


def test_category_schema_descriptions_are_prompt_ready():
    lines = field_description_lines(DocumentCategoryResult)

    assert any("risk_management" in line and "风险管理类" in line for line in lines)
    assert any("general" in line and "通用类" in line for line in lines)


def test_extraction_schema_exposes_field_descriptions():
    schema = get_extraction_schema("risk_item")
    lines = field_description_lines(schema)

    assert any("risk_name" in line and "风险事项" in line for line in lines)
    assert any("source_quote" in line and "原文" in line for line in lines)


def test_extraction_schema_display_metadata_uses_schema_labels():
    display = extraction_schema_display_metadata(["management_requirement_item", "general_item"])

    assert display["categoryLabels"]["regulation"] == "规章制度类"
    assert display["categoryLabels"]["general"] == "通用类"
    assert display["schemaLabels"]["management_requirement_item"] == "管理要求"
    assert display["schemaLabels"]["general_item"] == "通用事项"
    assert display["fieldLabels"]["management_requirement_item"]["department"] == "涉及部门"
    assert display["fieldLabels"]["management_requirement_item"]["source_quote"] == "原文依据"
