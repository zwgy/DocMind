from __future__ import annotations

import json
import re

from yuxi.agents.skills.buildin import BUILTIN_SKILLS
from yuxi.services.office_export_service import OFFICE_DEFINITION_ADAPTER, validate_definition_format


def test_office_export_skill_exposes_native_tool_and_format_references() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}
    spec = specs["office-export"]

    assert spec.tool_dependencies == ("export_office_file",)
    assert spec.mcp_dependencies == ()
    content = spec.source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
    assert "不调用 `task` 委派读取 reference、生成定义、导出或验收" in content
    assert "先用 `write_todos` 制定计划并在每步完成后更新状态" in content
    assert "成功后文件已由系统自动交付" in content
    assert "不要重复调用 `present_artifacts`" in content
    assert "写入后立即调用 `export_office_file`" in content
    assert "失败时只依据错误修改同一份定义并重试一次" in content
    for format_name in ("docx", "pdf", "xlsx"):
        assert spec.source_dir.joinpath("references", f"{format_name}.md").is_file()


def test_office_export_reference_examples_match_runtime_schema() -> None:
    spec = next(spec for spec in BUILTIN_SKILLS if spec.slug == "office-export")

    for output_format in ("docx", "pdf", "xlsx"):
        content = spec.source_dir.joinpath("references", f"{output_format}.md").read_text(encoding="utf-8")
        match = re.search(r"```json\s*(.*?)\s*```", content, flags=re.DOTALL)
        assert match is not None
        definition = OFFICE_DEFINITION_ADAPTER.validate_python(json.loads(match.group(1)))
        validate_definition_format(definition, output_format)


def test_business_skills_reuse_office_export_without_mcp_dependency() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}

    for slug in ("build-risk-ledger", "summarize-assessment-actions"):
        spec = specs[slug]
        content = spec.source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
        assert "export_office_file" in spec.tool_dependencies
        assert spec.skill_dependencies == ("office-export",)
        assert spec.mcp_dependencies == ()
        assert "依赖 Skill `office-export` 的入口文件" in content
        assert "/home/gem/skills/office-export/references/" not in content
