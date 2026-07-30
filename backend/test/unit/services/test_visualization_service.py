from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from yuxi.services.visualization_service import (
    VisualizationError,
    _renderer_error_detail,
    _reserve_output,
    _validate_svg,
)


def test_renderer_error_detail_prefers_actionable_node_error() -> None:
    stderr = b"""file:///app/render_mindmap.mjs:14
throw new Error("invalid outline");
^

Error: outline must use unordered list items
    at file:///app/render_mindmap.mjs:14:20

Node.js v24.18.0
"""

    assert _renderer_error_detail(stderr) == "Error: outline must use unordered list items"


def test_validate_svg_accepts_local_fragment_reference(tmp_path: Path) -> None:
    output = tmp_path / "chart.svg"
    output.write_text('<svg xmlns="http://www.w3.org/2000/svg"><use href="#legend" /></svg>', encoding="utf-8")

    _validate_svg(output)


@pytest.mark.parametrize(
    "content",
    [
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg>&xxe;</svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/image" /></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)" /></svg>',
    ],
)
def test_validate_svg_rejects_active_or_external_content(tmp_path: Path, content: str) -> None:
    output = tmp_path / "unsafe.svg"
    output.write_text(content, encoding="utf-8")

    with pytest.raises(VisualizationError):
        _validate_svg(output)


def test_reserve_output_never_overwrites_existing_artifact(tmp_path: Path) -> None:
    (tmp_path / "report.svg").write_text("previous", encoding="utf-8")

    reserved, virtual_path = _reserve_output(tmp_path, "report")

    assert reserved.name == "report-2.svg"
    assert reserved.read_bytes() == b""
    assert virtual_path.endswith("/report-2.svg")


def _load_flowchart_renderer():
    script = (
        Path(__file__).parents[3]
        / "package"
        / "yuxi"
        / "agents"
        / "skills"
        / "buildin"
        / "visualization"
        / "scripts"
        / "render_flowchart.py"
    )
    spec = importlib.util.spec_from_file_location("flowchart_renderer_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flowchart_renderer_builds_controlled_d2_and_valid_svg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "flow.svg"
    renderer = _load_flowchart_renderer()
    data = {
        "nodes": [
            {"id": "start", "kind": "start", "label": "开始"},
            {"id": "end", "kind": "end", "label": "结束"},
        ],
        "edges": [{"source": "start", "target": "end"}],
    }
    d2_source = renderer._build_d2(data)
    captured: dict = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><text>开始</text></svg>',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(renderer.subprocess, "run", fake_run)
    renderer._render_d2(d2_source, output)

    assert "classes:" in d2_source
    assert 'node_1: "开始"' in d2_source
    assert captured["command"][:3] == ["d2", "--layout=dagre", "--theme=0"]
    _validate_svg(output)


@pytest.mark.parametrize(
    ("script_name", "source_name", "source_content", "render_request"),
    [
        (
            "render_data_chart.mjs",
            "chart.csv",
            "月份,销量\n一月,10\n二月,14\n",
            {
                "chart_type": "bar",
                "title": "月度销量",
                "encoding": {"category": "月份", "values": ["销量"]},
            },
        ),
        (
            "render_mindmap.mjs",
            "map.mindmap.md",
            "- 项目\n  - 计划\n    - 里程碑\n  - 风险\n",
            {
                "outline": "- 项目\n  - 计划\n    - 里程碑\n  - 风险\n",
                "layout": "horizontal",
            },
        ),
    ],
)
def test_echarts_renderers_generate_themed_safe_svg(
    tmp_path: Path,
    script_name: str,
    source_name: str,
    source_content: str,
    render_request: dict,
) -> None:
    source = tmp_path / source_name
    output = tmp_path / f"{source_name}.svg"
    source.write_text(source_content, encoding="utf-8")
    script = (
        Path(__file__).parents[3]
        / "package"
        / "yuxi"
        / "agents"
        / "skills"
        / "buildin"
        / "visualization"
        / "scripts"
        / script_name
    )

    request = render_request if script_name == "render_mindmap.mjs" else render_request | {"source_path": str(source)}
    subprocess.run(
        ["node", str(script)],
        input=json.dumps(request | {"output": str(output)}, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        check=True,
        capture_output=True,
    )

    content = output.read_text(encoding="utf-8")
    assert "#2F6F5E" in content or "#2f6f5e" in content
    _validate_svg(output)
    if script_name == "render_mindmap.mjs":
        svg = ElementTree.fromstring(content)
        text_positions = {
            "".join(element.itertext()): float(element.attrib["x"])
            for element in svg.iter()
            if element.tag.rsplit("}", 1)[-1] == "text"
        }
        # 默认横向脑图必须把一级分支分布在中心主题两侧，而不是退化回单向树。
        assert text_positions["风险"] < text_positions["项目"] < text_positions["计划"]


@pytest.mark.parametrize(
    ("outline", "expected_error"),
    [
        (f"- {'节' * 49}", "节点不能为空且不得超过 48 个字符"),
        ("- 根节点\n" + "".join(f"  - 节点{i}\n" for i in range(100)), "节点超过 100 个"),
    ],
)
def test_mindmap_renderer_rejects_content_beyond_published_limits(
    tmp_path: Path,
    outline: str,
    expected_error: str,
) -> None:
    script = (
        Path(__file__).parents[3]
        / "package"
        / "yuxi"
        / "agents"
        / "skills"
        / "buildin"
        / "visualization"
        / "scripts"
        / "render_mindmap.mjs"
    )
    output = tmp_path / "mindmap.svg"

    result = subprocess.run(
        ["node", str(script)],
        input=json.dumps(
            {
                "outline": outline,
                "layout": "horizontal",
                "output": str(output),
            },
            ensure_ascii=False,
        ),
        text=True,
        encoding="utf-8",
        check=False,
        capture_output=True,
    )

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert not output.exists()
