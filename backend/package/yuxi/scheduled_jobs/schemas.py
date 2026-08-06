"""定时任务在来源适配器、服务层和持久化层之间共享的严格契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_TIMEZONE = "Asia/Shanghai"


class _StrictModel(BaseModel):
    """拒绝来源额外字段，避免模型输出绕过既定调度语义。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AtSchedule(_StrictModel):
    kind: Literal["at"] = "at"
    run_at: datetime


class IntervalSchedule(_StrictModel):
    kind: Literal["interval"] = "interval"
    interval_seconds: int = Field(ge=60, multiple_of=60)
    anchor_at: datetime


class CronSchedule(_StrictModel):
    kind: Literal["cron"] = "cron"
    cron_expression: str = Field(min_length=9, max_length=100)

    @field_validator("cron_expression")
    @classmethod
    def validate_five_field_expression(cls, value: str) -> str:
        """第一版只接受分钟精度 Cron，避免秒级规则突破产品精度。"""
        if len(value.split()) != 5 or not croniter.is_valid(value):
            raise ValueError("cron_expression 必须是合法的五段 Cron 表达式")
        return value


Schedule = Annotated[AtSchedule | IntervalSchedule | CronSchedule, Field(discriminator="kind")]


class NotificationAction(_StrictModel):
    type: Literal["notification"] = "notification"
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=4000)


class IncomingSourceSnapshot(_StrictModel):
    incoming_id: str = Field(min_length=1, max_length=64)
    source_document_id: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=100)
    document_number: str | None = Field(default=None, max_length=200)
    incoming_date: str | None = Field(default=None, max_length=32)
    evidence_summary: str | None = Field(default=None, max_length=1000)


class PersonalSourceSnapshot(_StrictModel):
    entry_point: Literal["web_agent", "chat_iframe", "http_api"]
    thread_id: str | None = Field(default=None, max_length=64)


class TaskCreationContext(_StrictModel):
    """由认证和来源用例生成，绝不由模型或 HTTP 请求直接传入。"""

    owner_uid: str = Field(min_length=1, max_length=64)
    created_by_uid: str = Field(min_length=1, max_length=64)
    source_type: Literal["incoming", "personal"]
    source_candidate_id: str | None = Field(default=None, max_length=64)
    source_snapshot: IncomingSourceSnapshot | PersonalSourceSnapshot
    create_request_key: str | None = Field(default=None, max_length=128)
    create_request_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_source_context(self) -> TaskCreationContext:
        if self.source_type == "incoming" and not isinstance(self.source_snapshot, IncomingSourceSnapshot):
            raise ValueError("来文任务必须使用来文来源快照")
        if self.source_type == "personal" and not isinstance(self.source_snapshot, PersonalSourceSnapshot):
            raise ValueError("个人任务必须使用个人来源快照")
        if bool(self.create_request_key) != bool(self.create_request_hash):
            raise ValueError("创建请求键和请求摘要必须同时提供")
        return self


class ScheduledJobDraft(_StrictModel):
    name: str = Field(min_length=1, max_length=100)
    schedule: Schedule
    action: NotificationAction
    recipient_uids: list[str] = Field(min_length=1, max_length=10_000)
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone 必须是有效的 IANA 时区") from error
        return value

    @field_validator("recipient_uids")
    @classmethod
    def validate_recipients(cls, value: list[str]) -> list[str]:
        if any(not uid for uid in value) or len(value) != len(set(value)):
            raise ValueError("recipient_uids 必须为非空且不重复的 UID 列表")
        return value

    @model_validator(mode="after")
    def normalize_schedule_datetime(self) -> ScheduledJobDraft:
        """以任务时区固定分钟精度，并拒绝与声明时区矛盾的 offset。"""
        timezone = ZoneInfo(self.timezone)
        if isinstance(self.schedule, AtSchedule):
            self.schedule.run_at = _normalize_schedule_datetime(self.schedule.run_at, timezone)
        elif isinstance(self.schedule, IntervalSchedule):
            self.schedule.anchor_at = _normalize_schedule_datetime(self.schedule.anchor_at, timezone)
        return self


class PersonalScheduledJobRequest(_StrictModel):
    """HTTP 与 Agent 工具共用的个人创建载荷，身份字段只能由服务端补齐。"""

    name: str = Field(min_length=1, max_length=100)
    schedule: Schedule
    action: NotificationAction
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return ScheduledJobDraft.validate_timezone(value)

    @model_validator(mode="after")
    def normalize_schedule_datetime(self) -> PersonalScheduledJobRequest:
        timezone = ZoneInfo(self.timezone)
        if isinstance(self.schedule, AtSchedule):
            self.schedule.run_at = _normalize_schedule_datetime(self.schedule.run_at, timezone)
        elif isinstance(self.schedule, IntervalSchedule):
            self.schedule.anchor_at = _normalize_schedule_datetime(self.schedule.anchor_at, timezone)
        return self


class IncomingTaskDraft(_StrictModel):
    """来文抽取输出；允许缺失调度信息，但不能伪造成可启用任务。"""

    task_name: str = Field(min_length=1, max_length=100)
    notification_title: str = Field(min_length=1, max_length=100)
    notification_content: str = Field(min_length=1, max_length=4000)
    raw_time_expression: str | None = Field(default=None, max_length=4000)
    schedule: Schedule | None = None
    raw_recipient_expression: str | None = Field(default=None, max_length=4000)
    recipient_scope: Literal["named", "all", "unknown"]
    recipient_names: list[str] = Field(default_factory=list, max_length=10_000)
    timezone: str | None = Field(default=None, max_length=64)
    source_quote: str = Field(min_length=1, max_length=4000)
    source_file_id: str = Field(min_length=1, max_length=512)
    source_location: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_recipient_expression(self) -> IncomingTaskDraft:
        if self.recipient_scope == "named" and not self.recipient_names:
            raise ValueError("recipient_scope 为 named 时 recipient_names 不能为空")
        if self.recipient_scope != "named" and self.recipient_names:
            raise ValueError("recipient_scope 为 all 或 unknown 时 recipient_names 必须为空")
        if self.timezone is not None:
            ScheduledJobDraft.validate_timezone(self.timezone)
        return self


def _normalize_schedule_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    """允许本地时间配合顶层 IANA 时区，持久化前统一消除秒和微秒。"""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone, second=0, microsecond=0)

    localized = value.astimezone(timezone)
    if localized.utcoffset() != value.utcoffset():
        raise ValueError("时间 offset 与 timezone 在该日期的实际偏移不一致")
    return localized.replace(second=0, microsecond=0)
