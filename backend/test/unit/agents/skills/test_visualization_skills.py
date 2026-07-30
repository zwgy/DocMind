from __future__ import annotations

import json
import re
from pathlib import Path

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def test_visualization_skill_specs_use_independent_skill_directories() -> None:
    expected_tools = {
        "data-chart": "render_data_chart",
        "flowchart": "render_flowchart",
        "mindmap": "render_mind_map",
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


def test_backend_node_manifest_is_not_owned_by_a_skill() -> None:
    backend_root = Path(__file__).resolve().parents[4]
    visualization_spec = next(spec for spec in BUILTIN_SKILLS if spec.slug == "visualization")
    scripts_dir = visualization_spec.source_dir.parents[1] / "scripts"
    converter = backend_root / "package" / "yuxi" / "services" / "scripts" / "svg_to_png.mjs"

    assert backend_root.joinpath("package.json").is_file()
    assert backend_root.joinpath("package-lock.json").is_file()
    manifest = json.loads(backend_root.joinpath("package.json").read_text(encoding="utf-8"))
    assert manifest["dependencies"]["@antv/hierarchy"] == "0.7.1"
    assert not scripts_dir.joinpath("package.json").exists()
    assert "visualization" not in converter.read_text(encoding="utf-8")


def test_static_renderers_use_d2_and_multi_hue_echarts_themes() -> None:
    visualization_spec = next(spec for spec in BUILTIN_SKILLS if spec.slug == "visualization")
    scripts_dir = visualization_spec.source_dir.parents[1] / "scripts"
    flowchart = scripts_dir.joinpath("render_flowchart.py").read_text(encoding="utf-8")
    data_chart = scripts_dir.joinpath("render_data_chart.mjs").read_text(encoding="utf-8")
    mindmap = scripts_dir.joinpath("render_mindmap.mjs").read_text(encoding="utf-8")

    assert '["d2", "--layout=dagre", "--theme=0"' in flowchart
    assert "graphviz" not in flowchart.lower()
    for color in ("#2F6F5E", "#4F6F8F", "#B56B2D", "#9B4D5B"):
        assert color in data_chart
        assert color in mindmap
    assert "wrapLabel" in mindmap
    assert 'import { mindmap as mindmapLayout } from "@antv/hierarchy"' in mindmap
    assert 'direction: radial ? "LR" : "H"' in mindmap
    assert 'type: "bezierCurve"' in mindmap


def test_flowchart_skill_provides_the_renderer_json_contract() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}
    content = specs["flowchart"].source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")

    # 小模型必须能在首次调用前得到渲染器要求的真实字段名，避免靠错误信息反复猜测。
    for field_name in ('"kind"', '"source"', '"target"', '"label"'):
        assert field_name in content


def test_visualization_skills_preserve_user_requested_output_names() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}

    # 本地小模型容易自行翻译文件名，必须在激活后的 Skill 主流程中重复保留用户原名的契约。
    for slug in ("data-chart", "flowchart", "mindmap"):
        content = specs[slug].source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
        assert "`output_name`" in content
        assert "用户明确指定名称时原样使用" in content
        assert "不要翻译或改写" in content
        assert "不含扩展名" in content


def test_mindmap_skill_uses_direct_outline_and_automatic_delivery() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}
    content = specs["mindmap"].source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")

    assert "`render_mind_map.outline`" in content
    assert '"outline"' in content
    assert "不调用 `ls`、`write_file`、`edit_file` 或 `execute`" in content
    assert "不再调用 `present_artifacts`" in content
    assert "优先使用 `horizontal` 双向中心布局" in content
    assert "100 个节点" in content
    assert "48 个字符" in content
    assert specs["mindmap"].tool_dependencies == ("render_mind_map",)


def test_visualization_renderers_deliver_without_exposing_present_artifacts() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}

    for slug, tool_name in (
        ("data-chart", "render_data_chart"),
        ("flowchart", "render_flowchart"),
        ("mindmap", "render_mind_map"),
    ):
        content = specs[slug].source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
        assert specs[slug].tool_dependencies == (tool_name,)
        assert "不再调用 `present_artifacts`" in content


def test_visualization_skill_metadata_routes_explicit_requests_to_child_skills() -> None:
    specs = {spec.slug: spec for spec in BUILTIN_SKILLS}
    expected = {
        "data-chart": ("数据图表", "render_data_chart"),
        "flowchart": ("流程图", "render_flowchart"),
        "mindmap": ("思维导图", "render_mind_map"),
    }

    # description 是小模型选择 Skill 前唯一可见的信息，必须直接给出触发词和排他性的执行入口。
    assert "仅当用户未明确可视化类型时" in specs["visualization"].description
    for slug, (trigger, renderer) in expected.items():
        description = specs[slug].description
        assert trigger in description
        assert "必须先读取此 Skill" in description
        assert renderer in description
        assert "禁止改用文档生成工具或手写 SVG" in description

        content = specs[slug].source_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
        frontmatter_description = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
        assert frontmatter_description is not None
        assert frontmatter_description.group(1) == description
