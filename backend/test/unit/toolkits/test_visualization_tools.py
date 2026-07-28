from __future__ import annotations

from types import SimpleNamespace

from langgraph.prebuilt.tool_node import ToolRuntime

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
