"""Agent 的个人定时任务工具，所有权始终从运行时身份取得。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from langchain.tools import InjectedToolCallId
from langchain_core.tools import ToolException
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import Field

from yuxi.agents.toolkits.registry import tool
from yuxi.scheduled_jobs.schemas import PersonalScheduledJobRequest
from yuxi.services.scheduled_job_service import ScheduledJobDomainError, ScheduledJobService
from yuxi.storage.postgres.manager import pg_manager

SCHEDULED_TASK_TOOL_CONFIG_GUIDE = "由定时任务 Skill 按需加载，不作为 Agent 基础工具直接配置。"


def _runtime_value(runtime: ToolRuntime | None, key: str) -> str:
    """兼容 LangGraph 的 context、state 与 configurable 注入位置。"""
    config = getattr(runtime, "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    for source in (configurable, getattr(runtime, "context", None), getattr(runtime, "state", None)):
        value = source.get(key) if isinstance(source, Mapping) else getattr(source, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _owner_uid(runtime: ToolRuntime | None) -> str:
    uid = _runtime_value(runtime, "uid")
    if not uid:
        raise ToolException("当前运行时缺少 uid，不能操作个人定时任务")
    return uid


def _creation_key(runtime: ToolRuntime | None, tool_call_id: str) -> str:
    """同一次 LangGraph 工具调用重放时复用创建请求，避免生成重复任务。"""
    uid = _owner_uid(runtime)
    thread_id = _runtime_value(runtime, "file_thread_id") or _runtime_value(runtime, "thread_id")
    if not thread_id:
        raise ToolException("当前运行时缺少 thread_id，不能创建个人定时任务")
    digest = hashlib.sha256(f"{uid}:{thread_id}:{tool_call_id}".encode()).hexdigest()
    return f"agent-v1-{digest}"


def _job_payload(job) -> dict[str, Any]:
    schedule = {"kind": job.schedule_kind}
    if job.schedule_kind == "at":
        schedule["run_at"] = job.run_at.isoformat() if job.run_at else None
    elif job.schedule_kind == "interval":
        schedule.update(
            {
                "interval_seconds": job.interval_seconds,
                "anchor_at": job.anchor_at.isoformat() if job.anchor_at else None,
            }
        )
    else:
        schedule["cron_expression"] = job.cron_expression
    return {
        "job_id": job.id,
        "name": job.name,
        "status": job.status,
        "version": job.version,
        "timezone": job.timezone,
        "schedule": schedule,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        "action": job.action_data,
    }


@tool(
    category="scheduled_task",
    tags=["定时任务"],
    display_name="创建个人定时任务",
    config_guide=SCHEDULED_TASK_TOOL_CONFIG_GUIDE,
)
async def create_personal_scheduled_task(
    request: Annotated[PersonalScheduledJobRequest, Field(description="任务名称、调度规则、通知内容和 IANA 时区")],
    tool_call_id: Annotated[str, InjectedToolCallId],
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """为当前用户创建站内通知或指定顶层 Agent 的定时任务。"""
    owner_uid = _owner_uid(runtime)
    try:
        async with pg_manager.get_async_session_context() as db:
            job = await ScheduledJobService(db).create_personal_job(
                owner_uid=owner_uid,
                request=request,
                idempotency_key=_creation_key(runtime, tool_call_id),
            )
            return _job_payload(job)
    except ScheduledJobDomainError as exc:
        raise ToolException(str(exc)) from exc


@tool(
    category="scheduled_task",
    tags=["定时任务"],
    display_name="查询个人定时任务",
    config_guide=SCHEDULED_TASK_TOOL_CONFIG_GUIDE,
)
async def list_personal_scheduled_tasks(
    statuses: Annotated[
        list[Literal["active", "paused", "completed", "cancelled"]] | None,
        Field(description="按状态过滤；省略时查询 active 和 paused 任务", max_length=4),
    ] = None,
    cursor: Annotated[str | None, Field(description="上一页返回的游标")]=None,
    limit: Annotated[int, Field(ge=1, le=50, description="返回数量，最大 50")]=20,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """查询当前用户拥有的个人定时任务。"""
    try:
        async with pg_manager.get_async_session_context() as db:
            jobs, next_cursor = await ScheduledJobService(db).list_owned_jobs(
                owner_uid=_owner_uid(runtime),
                statuses=tuple(statuses or ("active", "paused")),
                cursor=cursor,
                limit=limit,
            )
            return {"items": [_job_payload(job) for job in jobs], "next_cursor": next_cursor}
    except ScheduledJobDomainError as exc:
        raise ToolException(str(exc)) from exc


@tool(
    category="scheduled_task",
    tags=["定时任务"],
    display_name="暂停或恢复个人定时任务",
    config_guide=SCHEDULED_TASK_TOOL_CONFIG_GUIDE,
)
async def set_personal_scheduled_task_status(
    job_id: Annotated[str, Field(min_length=1, max_length=64, description="任务 ID")],
    version: Annotated[int, Field(ge=1, description="查询结果中的任务版本，用于避免覆盖并发修改")],
    action: Annotated[Literal["pause", "resume"], Field(description="暂停或恢复")],
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """暂停或恢复当前用户的周期性任务。"""
    try:
        async with pg_manager.get_async_session_context() as db:
            service = ScheduledJobService(db)
            method = service.pause if action == "pause" else service.resume
            return _job_payload(await method(job_id=job_id, owner_uid=_owner_uid(runtime), version=version))
    except ScheduledJobDomainError as exc:
        raise ToolException(str(exc)) from exc


@tool(
    category="scheduled_task",
    tags=["定时任务"],
    display_name="取消个人定时任务",
    config_guide=SCHEDULED_TASK_TOOL_CONFIG_GUIDE,
)
async def cancel_personal_scheduled_task(
    job_id: Annotated[str, Field(min_length=1, max_length=64, description="任务 ID")],
    version: Annotated[int, Field(ge=1, description="查询结果中的任务版本，用于避免覆盖并发修改")],
    reason: Annotated[str | None, Field(max_length=500, description="可选取消原因")]=None,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """取消当前用户尚未终止的定时任务。"""
    try:
        async with pg_manager.get_async_session_context() as db:
            job = await ScheduledJobService(db).cancel(
                job_id=job_id,
                owner_uid=_owner_uid(runtime),
                version=version,
                reason=reason,
            )
            return _job_payload(job)
    except ScheduledJobDomainError as exc:
        raise ToolException(str(exc)) from exc
