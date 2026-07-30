from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from langchain.tools import InjectedToolCallId
from langchain_core.tools import ToolException
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import Field

from yuxi.agents.artifacts import deliver_artifacts
from yuxi.agents.toolkits.registry import tool
from yuxi.services.office_export_service import OfficeExportError, export_office_file as run_office_export
from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS

_ALLOWED_SOURCE_DIRS = ("workspace", "uploads", "outputs")
_MAX_SOURCE_BYTES = 30 * 1024 * 1024


def _scope(runtime: ToolRuntime) -> tuple[str, str]:
    context = runtime.context
    uid = str(getattr(context, "uid", "") or "").strip()
    thread_id = str(getattr(context, "file_thread_id", None) or getattr(context, "thread_id", "") or "").strip()
    if not uid or not thread_id:
        raise ToolException("当前运行时缺少用户或文件线程信息")
    return uid, thread_id


def _source_resolver(thread_id: str, uid: str):
    # 模型只能看到虚拟路径。路径解析必须留在具有 ToolRuntime 的原生工具边界，
    # 避免 Service 或子进程接触用户不可见的宿主目录。
    from yuxi.agents.backends.sandbox.paths import resolve_virtual_path

    def resolve(virtual_path: str, suffixes: set[str]) -> Path:
        value = str(virtual_path or "").strip()
        if not any(value.startswith(f"/home/gem/user-data/{name}/") for name in _ALLOWED_SOURCE_DIRS):
            raise OfficeExportError("输入文件只能位于当前会话的 workspace、uploads 或 outputs 目录")
        if Path(value).suffix.lower() not in suffixes:
            raise OfficeExportError(f"输入文件格式不受支持，允许：{', '.join(sorted(suffixes))}")
        try:
            actual = resolve_virtual_path(thread_id, value, uid=uid)
        except ValueError as exc:
            raise OfficeExportError("输入文件路径不合法，请使用当前会话的虚拟路径") from exc
        if not actual.is_file():
            raise OfficeExportError("输入文件不存在或不是普通文件")
        if actual.stat().st_size > _MAX_SOURCE_BYTES:
            raise OfficeExportError("输入文件超过 30 MB 限制")
        return actual

    return resolve


@tool(category="document", tags=["文档", "导出"], display_name="导出 Office 文件")
async def export_office_file(
    definition_path: Annotated[str, Field(description="当前会话中的 Office JSON 定义文件虚拟路径")],
    output_format: Annotated[Literal["docx", "pdf", "xlsx"], Field(description="导出格式")],
    output_name: Annotated[str, Field(min_length=1, max_length=100, description="不含路径和扩展名的输出文件名")],
    tool_call_id: Annotated[str, InjectedToolCallId],
    runtime: ToolRuntime = None,
) -> Command:
    """根据受限定义文件生成带表格和本地图片的 DOCX、PDF 或 XLSX。"""
    uid, thread_id = _scope(runtime)
    resolver = _source_resolver(thread_id, uid)
    try:
        definition = resolver(definition_path, {".json"})
        from yuxi.agents.backends.sandbox.paths import ensure_thread_dirs, resolve_virtual_path

        ensure_thread_dirs(thread_id, uid)
        output_directory = resolve_virtual_path(thread_id, VIRTUAL_PATH_OUTPUTS, uid=uid)
        result = await run_office_export(
            definition_path=definition,
            output_format=output_format,
            output_name=output_name,
            output_directory=output_directory,
            virtual_output_directory=VIRTUAL_PATH_OUTPUTS,
            source_resolver=resolver,
        )
        return deliver_artifacts(
            filepaths=[result["artifact_path"]],
            runtime=runtime,
            tool_call_id=tool_call_id,
            content=result,
        )
    except OfficeExportError as exc:
        raise ToolException(str(exc)) from exc


def _validation_error(error) -> str:
    fields = [".".join(str(part) for part in item.get("loc", ())) for item in error.errors()[:3]]
    return f"参数校验失败，请检查字段：{'、'.join(fields) or '输入参数'}"


export_office_file.handle_validation_error = _validation_error
