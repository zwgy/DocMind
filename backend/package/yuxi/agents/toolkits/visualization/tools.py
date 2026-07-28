from __future__ import annotations

from typing import Literal

from langchain_core.tools import ToolException
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool
from yuxi.services.visualization_service import (
    VisualizationError,
    chart_source_path,
    flow_source_path,
    mindmap_source_path,
    render_visualization,
)


class ChartEncoding(BaseModel):
    model_config = {"extra": "forbid"}
    category: str | None = Field(default=None, description="分类或时间列")
    values: list[str] = Field(default_factory=list, max_length=12, description="数值列列表")
    series: str | None = Field(default=None, description="系列列")
    name: str | None = Field(default=None, description="饼图名称列")
    value: str | None = Field(default=None, description="饼图数值列")
    x: str | None = Field(default=None, description="散点图 X 数值列")
    y: str | None = Field(default=None, description="散点图 Y 数值列")
    label: str | None = Field(default=None, description="散点标签列")
    size: str | None = Field(default=None, description="散点大小列")


class RenderDataChartInput(BaseModel):
    model_config = {"extra": "forbid"}
    source_path: str = Field(description="当前会话中的 CSV 虚拟路径")
    chart_type: Literal["bar", "line", "area", "pie", "scatter"] = Field(description="图表类型")
    title: str = Field(min_length=1, max_length=200, description="中文图表标题")
    encoding: ChartEncoding = Field(description="字段角色映射")
    output_name: str = Field(description="ASCII 文件名主体，不含扩展名")


class RenderFlowchartInput(BaseModel):
    model_config = {"extra": "forbid"}
    definition_path: str = Field(description="当前会话中的 .flow.json 虚拟路径")
    output_name: str = Field(description="ASCII 文件名主体，不含扩展名")


class RenderMindmapInput(BaseModel):
    model_config = {"extra": "forbid"}
    outline_path: str = Field(description="当前会话中的 .mindmap.md 虚拟路径")
    layout: Literal["horizontal", "radial"] = Field(default="horizontal", description="思维导图布局")
    output_name: str = Field(description="ASCII 文件名主体，不含扩展名")


def _scope(runtime: ToolRuntime) -> tuple[str, str]:
    context = runtime.context
    uid = str(getattr(context, "uid", "") or "").strip()
    thread_id = str(getattr(context, "file_thread_id", None) or getattr(context, "thread_id", "") or "").strip()
    if not uid or not thread_id:
        raise ToolException("当前运行时缺少用户或文件线程信息")
    return uid, thread_id


@tool(category="visualization", tags=["可视化"], display_name="生成数据图表", args_schema=RenderDataChartInput)
async def render_data_chart(
    source_path: str,
    chart_type: str,
    title: str,
    encoding: dict,
    output_name: str,
    runtime: ToolRuntime,
) -> dict:
    """根据当前会话 CSV 和受限字段映射生成静态 SVG 数据图表。"""
    uid, thread_id = _scope(runtime)
    try:
        if not title.strip():
            raise VisualizationError("图表标题不能为空")
        source = chart_source_path(thread_id, uid, source_path)
        return await render_visualization(
            thread_id=thread_id,
            uid=uid,
            script_name="render_data_chart.mjs",
            output_name=output_name,
            request={
                "source_path": str(source),
                "chart_type": chart_type,
                "title": title,
                "encoding": encoding,
            },
        )
    except VisualizationError as exc:
        raise ToolException(str(exc)) from exc


@tool(category="visualization", tags=["可视化"], display_name="生成流程图", args_schema=RenderFlowchartInput)
async def render_flowchart(definition_path: str, output_name: str, runtime: ToolRuntime) -> dict:
    """根据受限流程 JSON 生成静态 SVG 流程图。"""
    uid, thread_id = _scope(runtime)
    try:
        source = flow_source_path(thread_id, uid, definition_path)
        return await render_visualization(
            thread_id=thread_id,
            uid=uid,
            script_name="render_flowchart.py",
            output_name=output_name,
            request={"source_path": str(source)},
        )
    except VisualizationError as exc:
        raise ToolException(str(exc)) from exc


@tool(category="visualization", tags=["可视化"], display_name="生成思维导图", args_schema=RenderMindmapInput)
async def render_mindmap(outline_path: str, layout: str, output_name: str, runtime: ToolRuntime) -> dict:
    """根据受限 Markdown 大纲生成静态 SVG 思维导图。"""
    uid, thread_id = _scope(runtime)
    try:
        source = mindmap_source_path(thread_id, uid, outline_path)
        return await render_visualization(
            thread_id=thread_id,
            uid=uid,
            script_name="render_mindmap.mjs",
            output_name=output_name,
            request={"source_path": str(source), "layout": layout},
        )
    except VisualizationError as exc:
        raise ToolException(str(exc)) from exc


def _validation_error(error) -> str:
    """小模型无法利用 Pydantic 默认英文错误，因此统一压缩为中文修正提示。"""
    fields = [".".join(str(part) for part in item.get("loc", ())) for item in error.errors()[:3]]
    return f"参数校验失败，请检查字段：{'、'.join(fields) or '输入参数'}"


for _tool in (render_data_chart, render_flowchart, render_mindmap):
    _tool.handle_validation_error = _validation_error
