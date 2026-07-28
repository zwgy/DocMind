from __future__ import annotations

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def test_visualization_skill_specs_use_independent_skill_directories() -> None:
    expected_tools = {
        "data-chart": "render_data_chart",
        "flowchart": "render_flowchart",
        "mindmap": "render_mindmap",
    }
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}

    assert specs["visualization"].skill_dependencies == tuple(expected_tools)
    for slug, tool_name in expected_tools.items():
        spec = specs[slug]
        assert spec.source_dir.joinpath("SKILL.md").is_file()
        assert tool_name in spec.tool_dependencies
        assert not spec.source_dir.joinpath("skills").exists()


def test_mysql_reporter_uses_data_chart_without_retired_chart_mcp() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}

    assert specs["mysql-reporter"].skill_dependencies == ("data-chart",)
    assert "mcp-server-chart" not in specs["mysql-reporter"].mcp_dependencies


def test_echarts_renderers_dispose_their_ssr_instance() -> None:
    visualization_spec = next(spec for spec in BUILTIN_SKILLS if spec.slug == "visualization")
    scripts_dir = visualization_spec.source_dir.parents[1] / "scripts"

    for script_name in ("render_data_chart.mjs", "render_mindmap.mjs"):
        assert "chart.dispose()" in (scripts_dir / script_name).read_text(encoding="utf-8")


def test_flowchart_skill_provides_the_renderer_json_contract() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}
    content = specs["flowchart"].source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")

    # 小模型必须能在首次调用前得到渲染器要求的真实字段名，避免靠错误信息反复猜测。
    for field_name in ('"kind"', '"source"', '"target"', '"label"'):
        assert field_name in content


def test_visualization_skills_require_ascii_output_names() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}

    # 工具 Schema 的简短字段说明不足以稳定约束本地小模型，必须在激活后的 Skill 主流程中重复这一硬契约。
    for slug in ("data-chart", "flowchart", "mindmap"):
        content = specs[slug].source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
        assert "`output_name`" in content
        assert "ASCII" in content
        assert "不含扩展名" in content


def test_mindmap_skill_requires_the_written_outline_path() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}
    content = specs["mindmap"].source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")

    assert "`outline_path`" in content
    assert "`write_file` 成功返回的完整路径" in content
    assert "`.mindmap.md` 扩展名" in content
