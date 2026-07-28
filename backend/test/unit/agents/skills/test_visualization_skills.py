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
