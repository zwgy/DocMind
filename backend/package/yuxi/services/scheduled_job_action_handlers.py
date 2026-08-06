"""定时任务动作处理器，隔离不同动作的提交与外部投递边界。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.scheduled_job_repository import ScheduledJobRepository
from yuxi.scheduled_jobs.schemas import AgentAction
from yuxi.services.agent_run_service import create_agent_run
from yuxi.storage.postgres.models_business import User


@dataclass(frozen=True)
class ActionDispatchResult:
    status: str | None
    agent_run_id: str | None = None


class NotificationActionHandler:
    async def dispatch(
        self, *, repository: ScheduledJobRepository, run_id: str, instance_id: str
    ) -> ActionDispatchResult:
        return ActionDispatchResult(status=await repository.deliver_notification(run_id=run_id, instance_id=instance_id))


class AgentActionHandler:
    async def dispatch(
        self, *, repository: ScheduledJobRepository, run_id: str, instance_id: str
    ) -> ActionDispatchResult:
        run, job, now = await repository.lock_dispatching_run_with_job(run_id=run_id, instance_id=instance_id)
        if run is None or job is None:
            return ActionDispatchResult(status=None)

        action = AgentAction.model_validate(
            {
                "type": run.action_snapshot.get("type"),
                "agent_slug": run.action_snapshot.get("agent_slug"),
                "instruction": run.action_snapshot.get("instruction"),
                "timeout_seconds": run.action_snapshot.get("timeout_seconds"),
            }
        )
        user = await repository.db.scalar(select(User).where(User.uid == job.owner_uid, User.is_deleted == 0))
        if user is None:
            raise ValueError("任务所有者不存在或已删除")
        agent = await AgentRepository(repository.db).get_visible_by_slug(slug=action.agent_slug, user=user)
        if agent is None or agent.is_subagent:
            raise ValueError("目标 Agent 不存在、不可见或不能作为定时任务执行")

        # 会话、运行和调度运行关联必须同一提交，ARQ 只能在提交后读取它们。
        conversation = await ConversationRepository(repository.db).create_conversation(
            uid=job.owner_uid,
            agent_id=action.agent_slug,
            title=f"定时任务：{job.name}",
            metadata={"source": "scheduled_job", "scheduled_job_id": job.id, "scheduled_job_run_id": run.id},
            commit=False,
        )
        agent_run, _ = await create_agent_run(
            query=action.instruction,
            agent_id=action.agent_slug,
            thread_id=conversation.thread_id,
            meta={"source": "scheduled_job", "request_id": f"scheduled:{run.id}"},
            image_content=None,
            current_uid=job.owner_uid,
            db=repository.db,
            run_type="scheduled",
            commit=False,
        )
        input_payload = dict(agent_run.input_payload or {})
        input_payload["scheduled_job"] = {"scheduled_job_run_id": run.id, "timeout_seconds": action.timeout_seconds}
        agent_run.input_payload = input_payload
        await repository.mark_agent_run_queued(
            run=run,
            job=job,
            agent_run_id=agent_run.id,
            conversation_id=str(conversation.id),
            now=now,
        )
        return ActionDispatchResult(status="queued", agent_run_id=agent_run.id)


_HANDLERS = {
    "notification": NotificationActionHandler(),
    "agent": AgentActionHandler(),
}


def get_action_handler(action_type: str):
    handler = _HANDLERS.get(action_type)
    if handler is None:
        raise ValueError(f"不支持的定时任务动作类型: {action_type}")
    return handler
