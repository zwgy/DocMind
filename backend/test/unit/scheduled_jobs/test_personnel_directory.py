from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.services.personnel_directory_service import LocalUserPersonnelDirectory, PersonnelResolution


pytestmark = pytest.mark.unit


def _operator(*, uid: str, role: str, department_id: int | None = None):
    return SimpleNamespace(uid=uid, role=role, department_id=department_id)


def test_local_directory_scopes_queries_by_operator_role():
    superadmin_sql = str(LocalUserPersonnelDirectory._visible_users_query(_operator(uid="root", role="superadmin")))
    admin_sql = str(
        LocalUserPersonnelDirectory._visible_users_query(_operator(uid="admin", role="admin", department_id=7))
    )
    user_sql = str(LocalUserPersonnelDirectory._visible_users_query(_operator(uid="alice", role="user")))

    assert "users.is_deleted" in superadmin_sql
    assert "users.department_id" in admin_sql
    assert "users.uid" in user_sql


def test_personnel_resolution_only_exposes_uid_for_unique_match():
    user = SimpleNamespace(uid="alice")

    assert PersonnelResolution(status="resolved", name="Alice", users=(user,)).uid == "alice"
    assert PersonnelResolution(status="ambiguous", name="Alice", users=(user, user)).uid is None
    assert PersonnelResolution(status="not_found", name="Alice", users=()).uid is None


@pytest.mark.asyncio
async def test_empty_name_is_not_resolved_without_querying_database():
    class FakeDb:
        async def scalars(self, _statement):
            raise AssertionError("blank names must not query the directory")

    result = await LocalUserPersonnelDirectory(FakeDb()).find_by_name(
        name="  ", operator=_operator(uid="alice", role="user")
    )

    assert result == PersonnelResolution(status="not_found", name="", users=())
