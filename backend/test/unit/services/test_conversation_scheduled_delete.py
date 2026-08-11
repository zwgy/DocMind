from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import conversation_service

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_personal_scheduled_conversation_can_be_deleted(monkeypatch):
    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return SimpleNamespace(
                uid="alice",
                status="active",
                extra_metadata={"source": "scheduled_job", "scheduled_source_type": "personal"},
            )

        async def delete_conversation(self, thread_id, *, soft_delete):
            assert (thread_id, soft_delete) == ("thread-1", True)
            return True

    monkeypatch.setattr(conversation_service, "ConversationRepository", FakeRepository)

    result = await conversation_service.delete_thread_view(
        thread_id="thread-1", db=SimpleNamespace(), current_uid="alice"
    )

    assert result == {"message": "删除成功"}


async def test_incoming_scheduled_conversation_remains_protected(monkeypatch):
    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return SimpleNamespace(
                uid="alice",
                status="active",
                extra_metadata={"source": "scheduled_job", "scheduled_source_type": "incoming"},
            )

    monkeypatch.setattr(conversation_service, "ConversationRepository", FakeRepository)

    with pytest.raises(HTTPException) as exc_info:
        await conversation_service.delete_thread_view(thread_id="thread-1", db=SimpleNamespace(), current_uid="alice")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "scheduled_run_conversation_protected"
