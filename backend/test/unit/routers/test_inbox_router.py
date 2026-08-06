from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from server.routers import inbox_router
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.inbox_service import InboxDomainError, InboxItemNotFoundError, InboxService

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class _FakeDb:
    def begin(self):
        return _Transaction()


def _app(*, user_override):
    app = FastAPI()
    app.include_router(inbox_router.inbox, prefix="/api")

    async def override_db():
        yield _FakeDb()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_required_user] = user_override
    return app


async def test_invalid_notification_cursor_returns_422():
    async def current_user():
        return SimpleNamespace(uid="alice")

    app = _app(user_override=current_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/inbox/notifications", params={"cursor": "not-a-cursor"})

    assert response.status_code == 422
    assert response.json() == {"detail": "cursor 无效"}


@pytest.mark.parametrize(
    ("path", "service_method", "item_argument", "not_found_detail"),
    [
        (
            "/api/inbox/notifications/inbox-owned-by-bob/read",
            "mark_notification_read",
            "item_id",
            "notification_not_found",
        ),
        ("/api/inbox/tasks/job-owned-by-bob/read", "mark_task_read", "job_id", "scheduled_job_not_found"),
    ],
)
async def test_mark_read_hides_items_owned_by_another_user(
    monkeypatch, path, service_method, item_argument, not_found_detail
):
    captured = {}

    class FakeService:
        def __init__(self, _db):
            pass

        async def mark_notification_read(self, **kwargs):
            captured.update(kwargs)
            raise InboxItemNotFoundError("notification_not_found")

        async def mark_task_read(self, **kwargs):
            captured.update(kwargs)
            raise InboxItemNotFoundError("scheduled_job_not_found")

    monkeypatch.setattr(inbox_router, "InboxService", FakeService)

    async def current_user():
        return SimpleNamespace(uid="alice")

    app = _app(user_override=current_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path)

    assert response.status_code == 404
    assert response.json() == {"detail": not_found_detail}
    assert captured[item_argument] in {"inbox-owned-by-bob", "job-owned-by-bob"}
    assert captured["recipient_uid" if service_method == "mark_notification_read" else "owner_uid"] == "alice"


async def test_read_all_rejects_unknown_category_before_service_is_called(monkeypatch):
    class FakeService:
        def __init__(self, _db):
            raise AssertionError("invalid category must not reach the service")

    monkeypatch.setattr(inbox_router, "InboxService", FakeService)

    async def current_user():
        return SimpleNamespace(uid="alice")

    app = _app(user_override=current_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/inbox/read-all", json={"category": "all"})

    assert response.status_code == 422


async def test_inbox_routes_require_authenticated_user():
    async def reject_user():
        raise HTTPException(status_code=401, detail="not_authenticated")

    app = _app(user_override=reject_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/inbox/unread-count")

    assert response.status_code == 401
    assert response.json() == {"detail": "not_authenticated"}


async def test_notification_cursor_rejects_wrong_category_payload():
    cursor = InboxService._encode_cursor(
        {"category": "task", "has_unread": 1, "sort_at": "2030-01-02T09:00:00+08:00", "id": "sj_1"}
    )

    with pytest.raises(InboxDomainError, match="cursor 无效"):
        InboxService._decode_notification_cursor(cursor)
