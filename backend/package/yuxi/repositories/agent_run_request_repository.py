"""Persistence helpers for queued agent input requests."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentRunRequest
from yuxi.utils.datetime_utils import utc_now_naive


class AgentRunRequestRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_by_request_id(self, request_id: str) -> AgentRunRequest | None:
        result = await self.db.execute(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
        return result.scalar_one_or_none()

    async def get_for_user(self, *, request_id: str, uid: str) -> AgentRunRequest | None:
        result = await self.db.execute(
            select(AgentRunRequest).where(
                AgentRunRequest.request_id == request_id,
                AgentRunRequest.uid == str(uid),
            )
        )
        return result.scalar_one_or_none()

    async def get_for_user_for_update(self, *, request_id: str, uid: str) -> AgentRunRequest | None:
        result = await self.db.execute(
            select(AgentRunRequest)
            .where(
                AgentRunRequest.request_id == request_id,
                AgentRunRequest.uid == str(uid),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def has_queued_request(self, *, thread_id: str, uid: str) -> bool:
        result = await self.db.execute(
            select(AgentRunRequest.id)
            .where(
                AgentRunRequest.thread_id == thread_id,
                AgentRunRequest.uid == str(uid),
                AgentRunRequest.status == "queued",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_next_queued_for_thread_for_update(self, *, thread_id: str, uid: str) -> AgentRunRequest | None:
        result = await self.db.execute(
            select(AgentRunRequest)
            .where(
                AgentRunRequest.thread_id == thread_id,
                AgentRunRequest.uid == str(uid),
                AgentRunRequest.status == "queued",
            )
            .order_by(AgentRunRequest.id.asc())
            .with_for_update()
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_queued_thread_keys(self) -> list[tuple[str, str]]:
        result = await self.db.execute(
            select(AgentRunRequest.thread_id, AgentRunRequest.uid)
            .where(AgentRunRequest.status == "queued")
            .distinct()
        )
        return [(str(thread_id), str(uid)) for thread_id, uid in result.all()]

    async def list_queued_for_thread(self, *, thread_id: str, agent_id: str, uid: str) -> list[AgentRunRequest]:
        result = await self.db.execute(
            select(AgentRunRequest)
            .where(
                AgentRunRequest.thread_id == thread_id,
                AgentRunRequest.agent_id == agent_id,
                AgentRunRequest.uid == str(uid),
                AgentRunRequest.status == "queued",
            )
            .order_by(AgentRunRequest.id.asc())
        )
        return list(result.scalars().all())

    async def create_request(
        self,
        *,
        request_id: str,
        thread_id: str,
        agent_id: str,
        uid: str,
        input_payload: dict,
    ) -> AgentRunRequest:
        request = AgentRunRequest(
            request_id=request_id,
            thread_id=thread_id,
            agent_id=agent_id,
            uid=str(uid),
            input_payload=input_payload or {},
            status="queued",
        )
        self.db.add(request)
        await self.db.flush()
        return request

    async def mark_dispatched(self, request: AgentRunRequest, *, run_id: str) -> None:
        request.status = "dispatched"
        request.dispatched_run_id = run_id
        request.error_message = None
        request.updated_at = utc_now_naive()
        await self.db.flush()

    async def cancel_queued(self, request: AgentRunRequest) -> None:
        request.status = "cancelled"
        request.updated_at = utc_now_naive()
        await self.db.flush()
