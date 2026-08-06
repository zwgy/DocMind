"""来文定时任务使用的本地人员目录边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import User

ResolutionStatus = Literal["resolved", "ambiguous", "not_found"]


@dataclass(frozen=True)
class PersonnelResolution:
    """姓名解析结果保留候选，调用方可将不可确定状态写回待确认候选。"""

    status: ResolutionStatus
    name: str
    users: tuple[User, ...]

    @property
    def uid(self) -> str | None:
        return self.users[0].uid if self.status == "resolved" else None


class LocalUserPersonnelDirectory:
    """第一版只读取有效本地账号，权限范围由操作者角色在查询层收紧。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def find_by_name(self, *, name: str, operator: User) -> PersonnelResolution:
        normalized_name = name.strip()
        if not normalized_name:
            return PersonnelResolution(status="not_found", name="", users=())
        users = tuple(
            (await self.db.scalars(self._visible_users_query(operator).where(User.username == normalized_name))).all()
        )
        if len(users) == 1:
            return PersonnelResolution(status="resolved", name=normalized_name, users=users)
        return PersonnelResolution(
            status="ambiguous" if users else "not_found",
            name=normalized_name,
            users=users,
        )

    async def list_active_users(self, *, operator: User) -> list[User]:
        result = await self.db.scalars(
            self._visible_users_query(operator).order_by(User.username.asc(), User.uid.asc())
        )
        return list(result.all())

    async def get_by_uids(self, *, uids: list[str], operator: User) -> list[User]:
        unique_uids = list(dict.fromkeys(uid for uid in uids if uid))
        if not unique_uids:
            return []
        result = await self.db.scalars(
            self._visible_users_query(operator)
            .where(User.uid.in_(unique_uids))
            .order_by(User.username.asc(), User.uid.asc())
        )
        return list(result.all())

    @staticmethod
    def _visible_users_query(operator: User):
        """普通用户只能解析自身；部门管理员仅可扩展本部门，防止来文越权送达。"""
        statement = select(User).where(User.is_deleted == 0)
        if operator.role == "superadmin":
            return statement
        if operator.role == "admin":
            return statement.where(User.department_id == operator.department_id)
        return statement.where(User.uid == operator.uid)
