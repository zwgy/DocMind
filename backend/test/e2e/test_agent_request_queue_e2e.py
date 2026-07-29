from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


async def _create_thread(client: httpx.AsyncClient, headers: dict[str, str], agent_id: str) -> str:
    response = await client.post(
        "/api/chat/thread",
        json={"agent_id": agent_id, "title": f"agent-request-queue-e2e-{uuid.uuid4().hex[:8]}", "metadata": {}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, payload
    return str(thread_id)


async def _create_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    agent_id: str,
    thread_id: str,
    query: str,
    queue_policy: str,
) -> dict:
    response = await client.post(
        "/api/agent/runs",
        json={
            "query": query,
            "agent_id": agent_id,
            "thread_id": thread_id,
            "meta": {"request_id": f"agent-request-queue-e2e-{uuid.uuid4()}"},
            "queue_policy": queue_policy,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_busy_thread_enqueues_and_cancels_follow_up_request(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str | int],
):
    agent_id = str(e2e_agent_context["agent_id"])
    thread_id = await _create_thread(e2e_client, e2e_headers, agent_id)
    first_run_id: str | None = None

    try:
        first = await _create_run(
            e2e_client,
            e2e_headers,
            agent_id=agent_id,
            thread_id=thread_id,
            # 足够长的确定性输出为下一请求制造真实的忙碌窗口，避免测试依赖工具调用耗时。
            query="请只输出从 1 到 1000 的整数，每行一个数字，不要使用工具或解释。",
            queue_policy="reject",
        )
        first_run_id = str(first["run_id"])

        queued = await _create_run(
            e2e_client,
            e2e_headers,
            agent_id=agent_id,
            thread_id=thread_id,
            query="【队列验收】此请求应排队且随后被取消。",
            queue_policy="enqueue",
        )
        assert queued == {
            "request_id": queued["request_id"],
            "thread_id": thread_id,
            "agent_id": agent_id,
            "status": "queued",
            "queued": True,
            "run_id": None,
            "content": "【队列验收】此请求应排队且随后被取消。",
        }

        requests_response = await e2e_client.get(
            f"/api/agent/thread/{thread_id}/requests",
            params={"agent_id": agent_id},
            headers=e2e_headers,
        )
        assert requests_response.status_code == 200, requests_response.text
        assert requests_response.json() == {
            "requests": [
                {
                    **queued,
                    "queue_position": 1,
                }
            ]
        }

        cancel_response = await e2e_client.post(
            f"/api/agent/requests/{queued['request_id']}/cancel",
            headers=e2e_headers,
        )
        assert cancel_response.status_code == 200, cancel_response.text
        assert cancel_response.json() == {
            **queued,
            "status": "cancelled",
            "queued": False,
        }
    finally:
        if first_run_id:
            # 测试不等待长输出完成，主动取消首个 run，避免它继续占用同一用户的 worker 配额。
            await e2e_client.post(f"/api/agent/runs/{first_run_id}/cancel", headers=e2e_headers)
