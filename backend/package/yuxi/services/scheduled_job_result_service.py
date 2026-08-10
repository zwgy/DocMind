"""定时 Agent 运行的终态投影与持久化对账。"""

from __future__ import annotations

import re

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository
from yuxi.storage.postgres.models_business import AgentRun, Conversation, Message
from yuxi.storage.postgres.models_scheduled_jobs import ScheduledJobRun

AGENT_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
SCHEDULED_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class ScheduledJobResultService:
    """把 Agent Run 事实投影为定时运行摘要，完整内容仍只保存在 Conversation。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_agent_run_status(self, *, agent_run_id: str, agent_status: str | None = None) -> bool:
        agent_run = await self.db.get(AgentRun, agent_run_id)
        if agent_run is None:
            return False
        current_status = agent_status or agent_run.status
        projection = None
        if current_status in AGENT_TERMINAL_STATUSES:
            projection = await self._build_projection(agent_run, current_status)
        return await ScheduledJobRepository(self.db).sync_agent_run_status(
            agent_run_id=agent_run_id,
            agent_status=current_status,
            result_projection=projection,
            agent_error_message=agent_run.error_message,
        )

    async def project_existing_terminal_run(self, *, scheduled_run_id: str) -> bool:
        scheduled_run = await self.db.scalar(
            select(ScheduledJobRun).where(ScheduledJobRun.id == scheduled_run_id).with_for_update()
        )
        if (
            scheduled_run is None
            or scheduled_run.status not in SCHEDULED_TERMINAL_STATUSES
            or not scheduled_run.agent_run_id
        ):
            return False
        existing = scheduled_run.result_data if isinstance(scheduled_run.result_data, dict) else {}
        if "result_preview" in existing:
            return False
        agent_run = await self.db.get(AgentRun, scheduled_run.agent_run_id)
        if agent_run is None:
            return False
        projection = await self._build_projection(agent_run, agent_run.status)
        scheduled_run.conversation_thread_id = scheduled_run.conversation_thread_id or agent_run.thread_id
        scheduled_run.result_data = {**existing, "agent_status": agent_run.status, **projection}
        await self.db.flush()
        return True

    async def list_status_mismatches(self, *, limit: int) -> list[tuple[str, str]]:
        result = await self.db.execute(
            select(ScheduledJobRun.agent_run_id, AgentRun.status)
            .join(AgentRun, AgentRun.id == ScheduledJobRun.agent_run_id)
            .where(
                ScheduledJobRun.action_type == "agent",
                ScheduledJobRun.status.in_(("queued", "running")),
                or_(
                    and_(ScheduledJobRun.status == "queued", AgentRun.status == "running"),
                    AgentRun.status.in_(AGENT_TERMINAL_STATUSES),
                ),
            )
            .order_by(ScheduledJobRun.updated_at.asc(), ScheduledJobRun.id.asc())
            .limit(limit)
        )
        return [(run_id, status) for run_id, status in result.all() if run_id]

    async def list_terminal_projection_gaps(self, *, limit: int) -> list[str]:
        result = await self.db.scalars(
            select(ScheduledJobRun.id)
            .where(
                ScheduledJobRun.action_type == "agent",
                ScheduledJobRun.status.in_(SCHEDULED_TERMINAL_STATUSES),
                ScheduledJobRun.conversation_thread_id.is_not(None),
                or_(
                    ScheduledJobRun.result_data.is_(None),
                    ScheduledJobRun.result_data["result_preview"].as_string().is_(None),
                ),
            )
            .order_by(ScheduledJobRun.updated_at.asc(), ScheduledJobRun.id.asc())
            .limit(limit)
        )
        return list(result.all())

    async def _build_projection(self, agent_run: AgentRun, agent_status: str) -> dict:
        conversation = await self.db.scalar(
            select(Conversation).where(
                Conversation.id == agent_run.conversation_id,
                Conversation.thread_id == agent_run.thread_id,
                Conversation.uid == agent_run.uid,
                Conversation.agent_id == agent_run.agent_id,
            )
        )
        messages: list[Message] = []
        if conversation is not None:
            result = await self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.run_id == agent_run.id,
                    Message.role == "assistant",
                )
                .order_by(Message.created_at.asc(), Message.id.asc())
            )
            messages = list(result.all())

        final_message = next(
            (
                message
                for message in reversed(messages)
                if message.id == agent_run.output_message_id
                and message.content
                and self._normalize_preview(message.content)
            ),
            None,
        )
        if final_message is None:
            final_message = next(
                (
                    message
                    for message in reversed(messages)
                    if message.content and self._normalize_preview(message.content)
                ),
                None,
            )
        preview = self._normalize_preview(final_message.content) if final_message else ""
        if not preview:
            preview = {
                "completed": "任务已完成，未生成可展示的最终回复",
                "cancelled": "任务执行已取消",
            }.get(agent_status, "任务执行失败，请查看详情")

        artifact_paths: list[str] = []
        for message in messages:
            metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
            paths = metadata.get("presented_artifacts")
            if isinstance(paths, list):
                artifact_paths.extend(path.strip() for path in paths if isinstance(path, str) and path.strip())

        return {
            "final_message_id": final_message.id if final_message else None,
            "result_preview": preview,
            "artifact_count": len(dict.fromkeys(artifact_paths)),
        }

    @staticmethod
    def _normalize_preview(value: str) -> str:
        normalized = re.sub(r"\s+", " ", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
        return f"{normalized[:300]}…" if len(normalized) > 300 else normalized
