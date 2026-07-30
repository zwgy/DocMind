from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph.prebuilt.tool_node import ToolRuntime

from yuxi.agents.toolkits.visualization import tools
from yuxi.agents.toolkits.registry import get_extra_metadata
from yuxi.agents.toolkits.visualization import render_data_chart, render_flowchart, render_mindmap


def test_visualization_tools_are_not_default_buildin_tools() -> None:
    for tool in (render_data_chart, render_flowchart, render_mindmap):
        metadata = get_extra_metadata(tool.name)
        assert metadata is not None
        assert metadata.category == "visualization"
        assert "runtime" not in tool.tool_call_schema.model_fields


def test_visualization_tool_schema_rejects_unexpected_fields() -> None:
    result = render_data_chart.tool_call_schema.model_validate(
        {
            "source_path": "/home/gem/user-data/outputs/.visualization-data/chart.csv",
            "chart_type": "bar",
            "title": "月度销量",
            "encoding": {"category": "month", "values": ["sales"]},
            "output_name": "monthly-sales",
        }
    )

    assert result.output_name == "monthly-sales"


def test_mindmap_tool_schema_accepts_outline_without_intermediate_path() -> None:
    result = render_mindmap.tool_call_schema.model_validate(
        {
            "outline": "- 项目治理\n  - 计划\n    - 里程碑",
            "output_name": "project-governance",
            "layout": "horizontal",
        }
    )

    assert result.outline.startswith("- 项目治理")
    assert render_mindmap.name == "render_mind_map"
    assert set(render_mindmap.tool_call_schema.model_fields) == {"outline", "output_name", "layout"}
    assert render_mindmap.tool_call_schema.model_json_schema()["properties"]["outline"]["maxLength"] == 8_000


def test_visualization_tools_accept_injected_runtime_without_exposing_it_to_model() -> None:
    runtime = ToolRuntime(
        state={},
        tool_call_id="call-1",
        config={"configurable": {}},
        context=SimpleNamespace(uid="user-1", thread_id="thread-1"),
        store=None,
        stream_writer=lambda _: None,
    )
    tool_input = {
        "source_path": "/home/gem/user-data/outputs/.visualization-data/chart.csv",
        "chart_type": "bar",
        "title": "月度销售",
        "encoding": {"category": "month", "values": ["sales"]},
        "output_name": "monthly-sales",
        "runtime": runtime,
    }

    parsed = render_data_chart._parse_input(tool_input, "call-1")

    assert parsed["runtime"] is runtime
    assert "runtime" not in render_data_chart.tool_call_schema.model_fields


@pytest.mark.asyncio
async def test_data_chart_serializes_encoding_before_rendering(monkeypatch) -> None:
    captured: dict = {}
    runtime = ToolRuntime(
        state={},
        tool_call_id="call-1",
        config={"configurable": {}},
        context=SimpleNamespace(uid="user-1", thread_id="thread-1"),
        store=None,
        stream_writer=lambda _: None,
    )

    monkeypatch.setattr(tools, "chart_source_path", lambda *_: "/tmp/chart.csv")

    async def fake_render_visualization(**kwargs):
        captured.update(kwargs)
        return {"artifact_path": "/home/gem/user-data/outputs/monthly-sales.svg"}

    monkeypatch.setattr(tools, "render_visualization", fake_render_visualization)

    await render_data_chart.coroutine(
        source_path="/home/gem/user-data/outputs/.visualization-data/chart.csv",
        chart_type="bar",
        title="月度销售",
        encoding=tools.ChartEncoding(category="month", values=["sales"]),
        output_name="monthly-sales",
        runtime=runtime,
    )

    assert captured["request"]["encoding"] == {"category": "month", "values": ["sales"]}


@pytest.mark.asyncio
async def test_mindmap_sends_outline_directly_to_renderer(monkeypatch) -> None:
    captured: dict = {}
    runtime = ToolRuntime(
        state={},
        tool_call_id="call-1",
        config={"configurable": {}},
        context=SimpleNamespace(uid="user-1", thread_id="thread-1"),
        store=None,
        stream_writer=lambda _: None,
    )

    async def fake_render_visualization(**kwargs):
        captured.update(kwargs)
        return {"artifact_path": "/home/gem/user-data/outputs/project-governance.svg"}

    monkeypatch.setattr(tools, "render_visualization", fake_render_visualization)
    outline = "- 项目治理\n  - 计划\n    - 里程碑"

    await render_mindmap.coroutine(
        outline=outline,
        output_name="project-governance",
        layout="horizontal",
        runtime=runtime,
    )

    assert captured["request"] == {"outline": outline, "layout": "horizontal"}
