from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.models_business import Base, Conversation, Department, User
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        dept = Department(id=1, name="默认部门")
        user = User(username="张三", uid="ext_oa_1001", password_hash="$argon2id$placeholder", department=dept)
        now = utc_now_naive()
        db.add_all(
            [
                dept,
                user,
                Conversation(
                    thread_id="scope-a-pinned",
                    uid="ext_oa_1001",
                    agent_id="default-chatbot",
                    title="A pinned",
                    status="active",
                    is_pinned=True,
                    extra_metadata={"conversation_scope_key": "oa:contract:001"},
                    created_at=now,
                    updated_at=now,
                ),
                Conversation(
                    thread_id="scope-a-normal",
                    uid="ext_oa_1001",
                    agent_id="default-chatbot",
                    title="A normal",
                    status="active",
                    extra_metadata={"conversation_scope_key": "oa:contract:001"},
                    created_at=now,
                    updated_at=now,
                ),
                Conversation(
                    thread_id="scope-b",
                    uid="ext_oa_1001",
                    agent_id="default-chatbot",
                    title="B",
                    status="active",
                    extra_metadata={"conversation_scope_key": "oa:contract:002"},
                    created_at=now,
                    updated_at=now,
                ),
                Conversation(
                    thread_id="no-scope",
                    uid="ext_oa_1001",
                    agent_id="default-chatbot",
                    title="No scope",
                    status="active",
                    extra_metadata={},
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


async def test_list_conversations_filters_pinned_and_normal_by_scope(session):
    repo = ConversationRepository(session)

    scoped = await repo.list_conversations(
        uid="ext_oa_1001",
        agent_id="default-chatbot",
        conversation_scope_key="oa:contract:001",
        limit=100,
    )

    assert [item.thread_id for item in scoped] == ["scope-a-pinned", "scope-a-normal"]


async def test_list_conversations_without_scope_keeps_existing_behavior(session):
    repo = ConversationRepository(session)

    all_threads = await repo.list_conversations(uid="ext_oa_1001", agent_id="default-chatbot", limit=100)

    assert {item.thread_id for item in all_threads} == {"scope-a-pinned", "scope-a-normal", "scope-b", "no-scope"}


async def test_update_conversation_persists_merged_json_metadata(session):
    repo = ConversationRepository(session)

    conversation = await repo.update_conversation(
        "scope-a-normal",
        metadata={"tool_approval_mode": "always_trust"},
    )

    assert conversation is not None
    session.expire(conversation)
    await session.refresh(conversation)
    assert conversation.extra_metadata == {
        "conversation_scope_key": "oa:contract:001",
        "tool_approval_mode": "always_trust",
    }


async def test_list_conversations_keeps_pinned_threads_outside_non_pinned_page_limit(session):
    now = utc_now_naive()
    session.add_all(
        [
            Conversation(
                thread_id="scope-a-pinned-2",
                uid="ext_oa_1001",
                agent_id="default-chatbot",
                title="A pinned 2",
                status="active",
                is_pinned=True,
                extra_metadata={"conversation_scope_key": "oa:contract:001"},
                created_at=now,
                updated_at=now + timedelta(seconds=1),
            ),
            Conversation(
                thread_id="scope-a-normal-2",
                uid="ext_oa_1001",
                agent_id="default-chatbot",
                title="A normal 2",
                status="active",
                extra_metadata={"conversation_scope_key": "oa:contract:001"},
                created_at=now,
                updated_at=now - timedelta(seconds=1),
            ),
        ]
    )
    await session.commit()

    page = await ConversationRepository(session).list_conversations(
        uid="ext_oa_1001",
        agent_id="default-chatbot",
        conversation_scope_key="oa:contract:001",
        limit=1,
        offset=1,
    )

    assert {item.thread_id for item in page[:2]} == {"scope-a-pinned", "scope-a-pinned-2"}
    assert page[2].thread_id == "scope-a-normal-2"
