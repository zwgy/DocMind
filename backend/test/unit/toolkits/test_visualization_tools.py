from __future__ import annotations

from yuxi.agents.toolkits.registry import get_extra_metadata
from yuxi.agents.toolkits.visualization import render_data_chart, render_flowchart, render_mindmap


def test_visualization_tools_are_not_default_buildin_tools() -> None:
    for tool in (render_data_chart, render_flowchart, render_mindmap):
        metadata = get_extra_metadata(tool.name)
        assert metadata is not None
        assert metadata.category == "visualization"
        assert "ToolRuntime" not in str(tool.args_schema.model_json_schema())


def test_visualization_tool_schema_rejects_unexpected_fields() -> None:
    result = render_data_chart.args_schema.model_validate(
        {
            "source_path": "/home/gem/user-data/outputs/.visualization-data/chart.csv",
            "chart_type": "bar",
            "title": "月度销量",
            "encoding": {"category": "month", "values": ["sales"]},
            "output_name": "monthly-sales",
        }
    )

    assert result.output_name == "monthly-sales"
