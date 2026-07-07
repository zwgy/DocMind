from __future__ import annotations

import os
import re
import secrets

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.domain_constants import DEFAULT_DEPARTMENT_ID
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive

MAX_EXTERNAL_UID_LENGTH = 64
SOURCE_SYSTEM_RE = re.compile(r"^[A-Za-z0-9]+$")
EXTERNAL_USER_ID_RE = re.compile(r"^[A-Za-z0-9]+$")


def _csv_env(name: str) -> set[str]:
    return {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}


def _normalize_external_identity(
    source_system: str,
    external_user_id: str,
    external_user_name: str,
) -> tuple[str, str, str, str]:
    source = str(source_system or "").strip()
    external_id = str(external_user_id or "").strip()
    external_name = str(external_user_name or "").strip()

    if not SOURCE_SYSTEM_RE.fullmatch(source):
        raise HTTPException(
            status_code=422,
            detail="source_system 只能包含英文字母和数字，且不能包含下划线",
        )
    if not EXTERNAL_USER_ID_RE.fullmatch(external_id):
        raise HTTPException(
            status_code=422,
            detail="external_user_id 只能包含英文字母和数字，且不能包含下划线",
        )
    if not external_name:
        raise HTTPException(status_code=422, detail="external_user_name 不能为空")

    uid = f"ext_{source}_{external_id}"
    if len(uid) > MAX_EXTERNAL_UID_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"生成的 DocMind uid 不能超过 {MAX_EXTERNAL_UID_LENGTH} 个字符",
        )
    return source, external_id, external_name, uid


def _ensure_iframe_exchange_allowed(source_system: str, origin: str | None) -> None:
    enabled = os.getenv("CHAT_IFRAME_AUTO_LOGIN_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="chat-iframe 自助登录未启用")

    allowed_sources = _csv_env("CHAT_IFRAME_ALLOWED_SOURCES")
    if allowed_sources and source_system not in allowed_sources:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="source_system 不在允许列表中")

    allowed_origins = _csv_env("CHAT_IFRAME_ALLOWED_ORIGINS")
    request_origin = str(origin or "").strip()
    if allowed_origins and request_origin not in allowed_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请求来源不在允许列表中")


async def _build_unique_username(db: AsyncSession, display_name: str, uid: str) -> str:
    base = display_name[:80] or uid
    result = await db.execute(select(User.id).where(User.username == base))
    if result.scalar_one_or_none() is None:
        return base

    candidate = f"{base}_{uid}"[:120]
    result = await db.execute(select(User.id).where(User.username == candidate))
    if result.scalar_one_or_none() is None:
        return candidate

    for index in range(2, 100):
        suffix = f"_{index}"
        candidate = f"{base[: 120 - len(suffix)]}{suffix}"
        result = await db.execute(select(User.id).where(User.username == candidate))
        if result.scalar_one_or_none() is None:
            return candidate

    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="无法生成可用的外部用户名称")


async def _get_default_department(db: AsyncSession) -> Department:
    department = await db.scalar(select(Department).where(Department.id == DEFAULT_DEPARTMENT_ID))
    if not department:
        # 外部用户必须落到一个真实部门，否则现有 get_required_user 会拒绝后续普通接口访问。
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"默认部门 id={DEFAULT_DEPARTMENT_ID} 不存在",
        )
    return department


async def _get_or_create_external_user(
    db: AsyncSession,
    *,
    source_system: str,
    display_name: str,
    uid: str,
) -> tuple[User, str]:
    user = await db.scalar(select(User).where(User.uid == uid))
    if user:
        if user.is_deleted:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="外部用户对应的 DocMind 账号已被删除")
        user.last_login = utc_now_naive()
        await db.commit()
        await db.refresh(user)
        return user, source_system

    department = await _get_default_department(db)
    username = await _build_unique_username(db, display_name, uid)
    user = User(
        username=username,
        uid=uid,
        phone_number=None,
        avatar=None,
        password_hash=AuthUtils.hash_password(secrets.token_urlsafe(32)),
        role="user",
        department_id=department.id,
        last_login=utc_now_naive(),
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="外部用户账号创建冲突，请重试") from exc
    return user, source_system


def _token_response(user: User, source_system: str) -> dict:
    return {
        "access_token": AuthUtils.create_access_token({"sub": str(user.id)}),
        "token_type": "bearer",
        "source_system": source_system,
        "user_id": user.id,
        "username": user.username,
        "uid": user.uid,
        "phone_number": user.phone_number,
        "avatar": user.avatar,
        "role": user.role,
        "department_id": user.department_id,
        "user": {
            "id": user.id,
            "username": user.username,
            "uid": user.uid,
            "role": user.role,
            "department_id": user.department_id,
        },
    }


async def exchange_external_user_backend_token(
    db: AsyncSession,
    *,
    source_system: str,
    external_user_id: str,
    external_user_name: str,
) -> dict:
    source, _external_id, display_name, uid = _normalize_external_identity(
        source_system, external_user_id, external_user_name
    )
    user, source = await _get_or_create_external_user(
        db,
        source_system=source,
        display_name=display_name,
        uid=uid,
    )
    return _token_response(user, source)


async def exchange_external_user_iframe_token(
    db: AsyncSession,
    *,
    source_system: str,
    external_user_id: str,
    external_user_name: str,
    origin: str | None,
) -> dict:
    source, _external_id, display_name, uid = _normalize_external_identity(
        source_system, external_user_id, external_user_name
    )
    _ensure_iframe_exchange_allowed(source, origin)
    user, source = await _get_or_create_external_user(
        db,
        source_system=source,
        display_name=display_name,
        uid=uid,
    )
    return _token_response(user, source)
