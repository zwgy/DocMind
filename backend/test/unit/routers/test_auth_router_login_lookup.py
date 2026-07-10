from types import SimpleNamespace

import pytest

from server.routers.auth_router import _find_login_user


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeDb:
    def __init__(self, users):
        self.users = users

    async def execute(self, statement):
        sql = str(statement)
        params = statement.compile().params
        field = "uid"
        if "users.phone_number" in sql:
            field = "phone_number"
        elif "users.username" in sql:
            field = "username"

        value = params.get(f"{field}_1")
        only_active = "users.is_deleted" in sql
        user = next(
            (
                item
                for item in self.users
                if getattr(item, field) == value and (not only_active or item.is_deleted == 0)
            ),
            None,
        )
        return _Result(user)


@pytest.mark.asyncio
async def test_login_lookup_prefers_active_username_over_deleted_uid():
    deleted_user = SimpleNamespace(uid="demo", username="已注销用户-1", phone_number=None, is_deleted=1)
    new_user = SimpleNamespace(uid="demo1", username="demo", phone_number=None, is_deleted=0)

    assert await _find_login_user(_FakeDb([deleted_user, new_user]), "demo") is new_user
