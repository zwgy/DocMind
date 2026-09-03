from __future__ import annotations

from typing import Annotated, Literal

from langchain.tools import InjectedToolCallId
from langchain_core.tools import ToolException
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from yuxi.agents.artifacts import deliver_artifacts
from yuxi.agents.toolkits.registry import tool
from yuxi.services.visualization_service import (
    VisualizationError,
    chart_source_path,
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


class FlowNode(BaseModel):
    model_config = {"extra": "forbid"}
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$", description="唯一节点 ID")
    kind: Literal["start", "process", "decision", "end"] = Field(description="节点类型")
    label: str = Field(min_length=1, max_length=80, description="节点文本")


class FlowEdge(BaseModel):
    model_config = {"extra": "forbid"}
    source: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$", description="起点 ID")
    target: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$", description="终点 ID")
    label: str = Field(default="", max_length=40, description="可选分支标签")


class FlowDefinition(BaseModel):
    model_config = {"extra": "forbid"}
    nodes: list[FlowNode] = Field(min_length=2, max_length=80, description="流程节点")
    edges: list[FlowEdge] = Field(max_length=160, description="流程连线")
    direction: Literal["TB", "LR"] = Field(default="TB", description="从上到下或从左到右")


def _scope(runtime: ToolRuntime) -> tuple[str, str]:
    context = runtime.context
    uid = str(getattr(context, "uid", "") or "").strip()
    thread_id = str(getattr(context, "file_thread_id", None) or getattr(context, "thread_id", "") or "").strip()
    if not uid or not thread_id:
        raise ToolException("当前运行时缺少用户或文件线程信息")
    return uid, thread_id


@tool(category="visualization", tags=["可视化"], display_name="生成数据图表")
async def render_data_chart(
    source_path: Annotated[str, Field(description="当前会话中的 CSV 虚拟路径")],
    chart_type: Annotated[Literal["bar", "line", "area", "pie", "scatter"], Field(description="图表类型")],
    title: Annotated[str, Field(min_length=1, max_length=200, description="中文图表标题")],
    encoding: Annotated[ChartEncoding, Field(description="字段角色映射")],
    output_name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=80,
            description="文件名主体，不含扩展名；用户明确指定名称时保留原名称，可使用中文",
        ),
    ],
    tool_call_id: Annotated[str, InjectedToolCallId],
    runtime: ToolRuntime = None,
) -> Command:
    """根据当前会话 CSV 和受限字段映射生成静态 SVG 数据图表。"""
    uid, thread_id = _scope(runtime)
    try:
        if not title.strip():
            raise VisualizationError("图表标题不能为空")
        source = chart_source_path(thread_id, uid, source_path)
        result = await render_visualization(
            thread_id=thread_id,
            uid=uid,
            script_name="render_data_chart.mjs",
            output_name=output_name,
            request={
                "source_path": str(source),
                "chart_type": chart_type,
                "title": title,
                "encoding": encoding.model_dump(exclude_none=True),
            },
        )
        return deliver_artifacts(
            filepaths=[result["artifact_path"]],
            runtime=runtime,
            tool_call_id=tool_call_id,
            content=result,
        )
    except VisualizationError as exc:
        raise ToolException(str(exc)) from exc


@tool(category="visualization", tags=["可视化"], display_name="生成流程图")
async def render_flowchart(
    definition: Annotated[FlowDefinition, Field(description="包含节点、连线和方向的流程定义")],
    output_name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=80,
            description="文件名主体，不含扩展名；用户明确指定名称时保留原名称，可使用中文",
        ),
    ],
    tool_call_id: Annotated[str, InjectedToolCallId],
    runtime: ToolRuntime = None,
) -> Command:
    """直接根据受限流程定义生成并自动交付静态 SVG 流程图。"""
    uid, thread_id = _scope(runtime)
    try:
        result = await render_visualization(
            thread_id=thread_id,
            uid=uid,
            script_name="render_flowchart.py",
            output_name=output_name,
            request={"definition": definition.model_dump()},
        )
        return deliver_artifacts(
            filepaths=[result["artifact_path"]],
            runtime=runtime,
            tool_call_id=tool_call_id,
            content=result,
        )
    except VisualizationError as exc:
        raise ToolException(str(exc)) from exc


@tool(
    name_or_callable="render_mind_map",
    category="visualization",
    tags=["可视化"],
    display_name="生成思维导图",
)
async def render_mindmap(
    outline: Annotated[
        str,
        Field(
            min_length=3,
            max_length=8_000,
            description=(
                "Markdown 无序列表大纲正文；参数名必须是 outline，不是 outline_path、file_path 或 source_path；"
                "首行是唯一根节点且以 '- ' 开头，子级每层缩进两个空格"
            ),
        ),
    ],
    output_name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=80,
            description="文件名主体，不含扩展名；用户明确指定名称时保留原名称，可使用中文",
        ),
    ],
    tool_call_id: Annotated[str, InjectedToolCallId],
    layout: Annotated[
        Literal["horizontal", "radial"],
        Field(description="默认使用 horizontal；只有用户明确要求径向布局时使用 radial"),
    ] = "horizontal",
    runtime: ToolRuntime = None,
) -> Command:
    """直接根据受限 Markdown 大纲正文生成并自动交付静态 SVG 思维导图。"""
    uid, thread_id = _scope(runtime)
    try:
        result = await render_visualization(
            thread_id=thread_id,
            uid=uid,
            script_name="render_mindmap.mjs",
            output_name=output_name,
            request={"outline": outline, "layout": layout},
        )
        return deliver_artifacts(
            filepaths=[result["artifact_path"]],
            runtime=runtime,
            tool_call_id=tool_call_id,
            content=result,
        )
    except VisualizationError as exc:
        raise ToolException(str(exc)) from exc


def _validation_error(error) -> str:
    """小模型无法利用 Pydantic 默认英文错误，因此统一压缩为中文修正提示。"""
    fields = [".".join(str(part) for part in item.get("loc", ())) for item in error.errors()[:3]]
    return f"参数校验失败，请检查字段：{'、'.join(fields) or '输入参数'}"


for _tool in (render_data_chart, render_flowchart, render_mindmap):
    _tool.handle_validation_error = _validation_error
