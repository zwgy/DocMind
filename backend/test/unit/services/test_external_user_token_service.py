from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.external_user_token_service import (
    exchange_external_user_backend_token,
    exchange_external_user_iframe_token,
)
from yuxi.storage.postgres.models_business import Base, Department, User
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Department(id=1, name="默认部门"))
        await db.commit()
        yield db
    await engine.dispose()


async def test_backend_exchange_creates_default_user_and_token(session):
    result = await exchange_external_user_backend_token(
        session,
        source_system="oa",
        external_user_id="1001",
        external_user_name="张三",
    )

    user = await session.scalar(select(User).where(User.uid == "ext_oa_1001"))
    assert user is not None
    assert user.username == "张三"
    assert user.role == "user"
    assert user.department_id == 1
    assert user.last_login is not None
    assert result["source_system"] == "oa"
    assert result["user"]["uid"] == "ext_oa_1001"
    assert AuthUtils.decode_token(result["access_token"])["sub"] == str(user.id)


async def test_iframe_exchange_requires_enabled_source_and_origin(session, monkeypatch):
    monkeypatch.delenv("CHAT_IFRAME_AUTO_LOGIN_ENABLED", raising=False)
    with pytest.raises(HTTPException) as disabled:
        await exchange_external_user_iframe_token(
            session,
            source_system="oa",
            external_user_id="1001",
            external_user_name="张三",
            origin="https://oa.example.com",
        )
    assert disabled.value.status_code == 403

    monkeypatch.setenv("CHAT_IFRAME_AUTO_LOGIN_ENABLED", "true")
    monkeypatch.setenv("CHAT_IFRAME_ALLOWED_SOURCES", "oa")
    monkeypatch.setenv("CHAT_IFRAME_ALLOWED_ORIGINS", "https://oa.example.com")

    with pytest.raises(HTTPException) as bad_source:
        await exchange_external_user_iframe_token(
            session,
            source_system="erp",
            external_user_id="1001",
            external_user_name="张三",
            origin="https://oa.example.com",
        )
    assert bad_source.value.status_code == 403

    with pytest.raises(HTTPException) as bad_origin:
        await exchange_external_user_iframe_token(
            session,
            source_system="oa",
            external_user_id="1001",
            external_user_name="张三",
            origin="https://erp.example.com",
        )
    assert bad_origin.value.status_code == 403


async def test_exchange_rejects_invalid_uid_parts_and_too_long_uid(session):
    with pytest.raises(HTTPException) as invalid_source:
        await exchange_external_user_backend_token(
            session,
            source_system="oa_system",
            external_user_id="1001",
            external_user_name="张三",
        )
    assert invalid_source.value.status_code == 422

    with pytest.raises(HTTPException) as invalid_external_id:
        await exchange_external_user_backend_token(
            session,
            source_system="oa",
            external_user_id="user_1001",
            external_user_name="张三",
        )
    assert invalid_external_id.value.status_code == 422

    with pytest.raises(HTTPException) as too_long:
        await exchange_external_user_backend_token(
            session,
            source_system="oa",
            external_user_id="x" * 62,
            external_user_name="张三",
        )
    assert too_long.value.status_code == 422


async def test_exchange_rejects_deleted_external_user(session):
    user = User(
        username="已删除",
        uid="ext_oa_1001",
        password_hash="$argon2id$placeholder",
        role="user",
        department_id=1,
        is_deleted=1,
    )
    session.add(user)
    await session.commit()

    with pytest.raises(HTTPException) as deleted:
        await exchange_external_user_backend_token(
            session,
            source_system="oa",
            external_user_id="1001",
            external_user_name="张三",
        )
    assert deleted.value.status_code == 409
