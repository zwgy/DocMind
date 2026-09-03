from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import ValidationError

from yuxi.agents.artifacts import ARTIFACT_DELIVERY_SCHEMA
from yuxi.agents.backends.sandbox import ensure_thread_dirs, sandbox_outputs_dir
from yuxi.agents.toolkits.registry import get_extra_metadata
from yuxi.agents.toolkits.visualization import tools
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


def test_flowchart_tool_schema_accepts_definition_without_intermediate_path() -> None:
    result = render_flowchart.tool_call_schema.model_validate(
        {
            "definition": {
                "nodes": [
                    {"id": "start", "kind": "start", "label": "开始"},
                    {"id": "end", "kind": "end", "label": "结束"},
                ],
                "edges": [{"source": "start", "target": "end"}],
                "direction": "TB",
            },
            "output_name": "simple-flow",
        }
    )

    assert result.definition.nodes[0].kind == "start"
    assert set(render_flowchart.tool_call_schema.model_fields) == {"definition", "output_name"}
    schema = render_flowchart.tool_call_schema.model_json_schema()
    assert "不能放在 definition 内" in schema["properties"]["definition"]["description"]
    assert "工具顶层参数" in schema["properties"]["output_name"]["description"]


def test_flowchart_tool_schema_accepts_chinese_node_ids() -> None:
    result = render_flowchart.tool_call_schema.model_validate(
        {
            "definition": {
                "nodes": [
                    {"id": "开始", "kind": "start", "label": "开始"},
                    {"id": "结束", "kind": "end", "label": "结束"},
                ],
                "edges": [{"source": "开始", "target": "结束"}],
            },
            "output_name": "simple-flow",
        }
    )

    assert result.definition.edges[0].source == "开始"


@pytest.mark.parametrize(
    ("definition", "expected"),
    [
        ({"nodes": [], "edges": []}, "definition.nodes：至少需要 2 项"),
        (
            {
                "nodes": [
                    {"id": "start", "kind": "start", "label": ""},
                    {"id": "end", "kind": "end", "label": "结束"},
                ],
                "edges": [{"source": "start", "target": "end"}],
            },
            "definition.nodes.0.label：不能为空",
        ),
        ("not-an-object", "definition：必须是对象"),
    ],
)
def test_visualization_validation_error_includes_actionable_reason(definition, expected) -> None:
    with pytest.raises(ValidationError) as exc_info:
        render_flowchart.tool_call_schema.model_validate(
            {"definition": definition, "output_name": "simple-flow"}
        )

    assert expected in tools._validation_error(exc_info.value)


def test_visualization_tool_schema_accepts_user_requested_chinese_output_name() -> None:
    result = render_mindmap.tool_call_schema.model_validate(
        {
            "outline": "- 端到端验收\n  - 思维导图",
            "output_name": "端到端验收-思维导图-0730",
            "layout": "horizontal",
        }
    )

    assert result.output_name == "端到端验收-思维导图-0730"


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
        ensure_thread_dirs("thread-1", "user-1")
        sandbox_outputs_dir("thread-1").joinpath("monthly-sales.svg").write_text("<svg/>", encoding="utf-8")
        return {"artifact_path": "/home/gem/user-data/outputs/monthly-sales.svg"}

    monkeypatch.setattr(tools, "render_visualization", fake_render_visualization)

    command = await render_data_chart.coroutine(
        source_path="/home/gem/user-data/outputs/.visualization-data/chart.csv",
        chart_type="bar",
        title="月度销售",
        encoding=tools.ChartEncoding(category="month", values=["sales"]),
        output_name="monthly-sales",
        tool_call_id="call-1",
        runtime=runtime,
    )

    assert captured["request"]["encoding"] == {"category": "month", "values": ["sales"]}
    assert command.update["messages"][0].artifact == {
        "schema": ARTIFACT_DELIVERY_SCHEMA,
        "paths": ["/home/gem/user-data/outputs/monthly-sales.svg"],
    }


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
        ensure_thread_dirs("thread-1", "user-1")
        sandbox_outputs_dir("thread-1").joinpath("project-governance.svg").write_text("<svg/>", encoding="utf-8")
        return {"artifact_path": "/home/gem/user-data/outputs/project-governance.svg"}

    monkeypatch.setattr(tools, "render_visualization", fake_render_visualization)
    outline = "- 项目治理\n  - 计划\n    - 里程碑"

    command = await render_mindmap.coroutine(
        outline=outline,
        output_name="project-governance",
        tool_call_id="call-1",
        layout="horizontal",
        runtime=runtime,
    )

    assert captured["request"] == {"outline": outline, "layout": "horizontal"}
    assert command.update["artifacts"] == ["/home/gem/user-data/outputs/project-governance.svg"]


@pytest.mark.asyncio
async def test_flowchart_sends_definition_directly_to_renderer(monkeypatch) -> None:
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
        ensure_thread_dirs("thread-1", "user-1")
        sandbox_outputs_dir("thread-1").joinpath("simple-flow.svg").write_text("<svg/>", encoding="utf-8")
        return {"artifact_path": "/home/gem/user-data/outputs/simple-flow.svg"}

    monkeypatch.setattr(tools, "render_visualization", fake_render_visualization)
    definition = tools.FlowDefinition(
        nodes=[
            tools.FlowNode(id="start", kind="start", label="开始"),
            tools.FlowNode(id="end", kind="end", label="结束"),
        ],
        edges=[tools.FlowEdge(source="start", target="end")],
    )

    command = await render_flowchart.coroutine(
        definition=definition,
        output_name="simple-flow",
        tool_call_id="call-1",
        runtime=runtime,
    )

    assert captured["request"] == {
        "definition": {
            "nodes": [
                {"id": "start", "kind": "start", "label": "开始"},
                {"id": "end", "kind": "end", "label": "结束"},
            ],
            "edges": [{"source": "start", "target": "end", "label": ""}],
            "direction": "TB",
        }
    }
    assert command.update["artifacts"] == ["/home/gem/user-data/outputs/simple-flow.svg"]
