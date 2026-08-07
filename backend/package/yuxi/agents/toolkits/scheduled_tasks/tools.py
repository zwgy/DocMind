"""Agent 的个人定时任务工具，所有权始终从运行时身份取得。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from langchain.tools import InjectedToolCallId
from langchain_core.tools import ToolException
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import Field

from yuxi.agents.toolkits.buildin.tools import ask_user_question
from yuxi.agents.toolkits.registry import tool
from yuxi.scheduled_jobs.schemas import PersonalScheduledJobRequest
from yuxi.services.scheduled_job_service import ScheduledJobDomainError, ScheduledJobService
from yuxi.storage.postgres.manager import pg_manager

SCHEDULED_TASK_TOOL_CONFIG_GUIDE = "由定时任务 Skill 按需加载，不作为 Agent 基础工具直接配置。"
_CLOCK_TIME_PATTERN = re.compile(
    r"(?:凌晨|早上|上午|中午|下午|傍晚|晚上|夜里|早晨|晨间|午后)?\s*"
    r"(?:[01]?\d|2[0-3])\s*(?::|：|点)(?:\s*[0-5]?\d\s*分?)?"
    r"|(?:凌晨|早上|上午|中午|下午|傍晚|晚上|夜里|早晨|晨间|午后)?\s*"
    r"[零一二三四五六七八九十]{1,3}点(?:[零一二三四五六七八九十]{1,3}分)?"
)


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


def _latest_user_message(runtime: ToolRuntime | None) -> str:
    """只从当前会话状态读取用户原文，避免把模型臆测的时间当成用户已确认的信息。"""
    state = getattr(runtime, "state", None)
    messages = state.get("messages", []) if isinstance(state, Mapping) else getattr(state, "messages", [])
    for message in reversed(messages or []):
        if getattr(message, "type", None) != "human":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(
                item.get("text", "") if isinstance(item, Mapping) else str(item)
                for item in content
            ).strip()
    return ""


def _has_answered_periodic_time_question(runtime: ToolRuntime | None) -> bool:
    """结构化反问会以 ToolMessage 回放答案，不能只查看最初的 HumanMessage。"""
    state = getattr(runtime, "state", None)
    messages = state.get("messages", []) if isinstance(state, Mapping) else getattr(state, "messages", [])
    for message in reversed(messages or []):
        if getattr(message, "type", None) != "tool" or getattr(message, "name", None) != "ask_user_question":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, Mapping):
            payload = content
        elif isinstance(content, str):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
        else:
            continue
        answer = payload.get("answer") if isinstance(payload, Mapping) else None
        questions = payload.get("questions", []) if isinstance(payload, Mapping) else []
        question_text = json.dumps(questions, ensure_ascii=False) if questions else ""
        if answer and any(keyword in question_text for keyword in ("scheduled_task_time", "每周几", "几点", "提醒")):
            return True
    return False


def _needs_periodic_time_clarification(schedule_kind: str, runtime: ToolRuntime | None) -> bool:
    """周期任务必须由用户给出钟点，不能让本地模型以默认时间补全。"""
    return schedule_kind in {"interval", "cron"} and bool(
        (user_message := _latest_user_message(runtime))
        and not _CLOCK_TIME_PATTERN.search(user_message)
        and not _has_answered_periodic_time_question(runtime)
    )


def _ask_periodic_schedule_time(schedule_kind: str) -> dict[str, Any]:
    question = "请指定首次提醒的具体日期和时间。" if schedule_kind == "interval" else "请指定每周几、几点提醒您。"
    answer = ask_user_question.func(
        questions=[
            {
                "question_id": "scheduled_task_time",
                "question": question,
                "options": [
                    {"label": "上午 9:00", "value": "上午 9:00"},
                    {"label": "下午 5:00", "value": "下午 5:00"},
                ],
                "allow_other": True,
            }
        ]
    )
    return {"status": "needs_clarification", "answer": answer}


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
    name: Annotated[str, Field(min_length=1, max_length=100, description="任务名称")],
    timezone: Annotated[str, Field(description="IANA 时区，例如 Asia/Shanghai")],
    schedule_kind: Annotated[Literal["at", "interval", "cron"], Field(description="调度类型")],
    tool_call_id: Annotated[str, InjectedToolCallId],
    run_at: Annotated[
        datetime | None, Field(description="单次任务的未来触发时间，schedule_kind 为 at 时必填")
    ] = None,
    interval_seconds: Annotated[
        int | None, Field(ge=60, multiple_of=60, description="间隔秒数，schedule_kind 为 interval 时必填")
    ] = None,
    anchor_at: Annotated[
        datetime | None, Field(description="间隔任务首次触发时间，schedule_kind 为 interval 时必填")
    ] = None,
    cron_expression: Annotated[
        str | None, Field(description="五段 Cron 表达式，schedule_kind 为 cron 时必填")
    ] = None,
    action_type: Annotated[Literal["notification", "agent"], Field(description="动作类型")] = "notification",
    title: Annotated[
        str | None, Field(max_length=100, description="通知标题，action_type 为 notification 时必填")
    ] = None,
    content: Annotated[
        str | None, Field(max_length=4000, description="通知正文，action_type 为 notification 时必填")
    ] = None,
    agent_slug: Annotated[
        str | None, Field(max_length=80, description="目标顶层 Agent，action_type 为 agent 时必填")
    ] = None,
    instruction: Annotated[
        str | None, Field(max_length=8000, description="Agent 执行指令，action_type 为 agent 时必填")
    ] = None,
    timeout_seconds: Annotated[
        int | None, Field(ge=60, le=3600, description="Agent 超时秒数，action_type 为 agent 时可选")
    ] = None,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """为当前用户创建站内通知或指定顶层 Agent 的定时任务。"""
    owner_uid = _owner_uid(runtime)
    if _needs_periodic_time_clarification(schedule_kind, runtime):
        return _ask_periodic_schedule_time(schedule_kind)
    schedule: dict[str, Any] = {"kind": schedule_kind}
    if schedule_kind == "at":
        schedule["run_at"] = run_at
    elif schedule_kind == "interval":
        schedule.update({"interval_seconds": interval_seconds, "anchor_at": anchor_at})
    else:
        schedule["cron_expression"] = cron_expression
    action: dict[str, Any] = {"type": action_type}
    if action_type == "notification":
        action.update({"title": title, "content": content})
    else:
        action.update({"agent_slug": agent_slug, "instruction": instruction})
        if timeout_seconds is not None:
            action["timeout_seconds"] = timeout_seconds
    request = PersonalScheduledJobRequest.model_validate(
        {"name": name, "timezone": timezone, "schedule": schedule, "action": action}
    )
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
