"""通过真实 HTTP API 验收多级上下文压缩，并保留可在 chat-iframe 查看的一条线程。"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RunEvidence:
    """只保存验收所需诊断，不把大段模型正文复制到脚本输出。"""

    run_id: str
    status: str = ""
    elapsed_seconds: float = 0.0
    compaction_events: list[dict[str, Any]] = field(default_factory=list)
    tool_names: set[str] = field(default_factory=set)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:5050")
    parser.add_argument("--source-system", default="oa")
    parser.add_argument("--function-id", default="incomingDocument")
    parser.add_argument("--business-id", default="37908")
    parser.add_argument("--external-user-id", default="1001")
    parser.add_argument("--external-user-name", default="张三")
    parser.add_argument("--agent-id", default="default-chatbot")
    parser.add_argument("--max-growth-rounds", type=int, default=10)
    parser.add_argument("--payload-chars", type=int, default=5000)
    parser.add_argument("--run-timeout", type=float, default=300.0)
    parser.add_argument(
        "--allow-no-l5",
        action="store_true",
        help="仅用于排查环境；正式验收不得使用，否则没有证明真实线程触发 L5。",
    )
    args = parser.parse_args()
    if args.max_growth_rounds < 1:
        parser.error("--max-growth-rounds 必须大于 0")
    if args.payload_chars < 1000:
        parser.error("--payload-chars 至少为 1000，过小无法形成有效压力")
    return args


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _collect_event_evidence(payload: dict[str, Any], evidence: RunEvidence) -> None:
    for item in _walk_dicts(payload):
        if item.get("type") == "context_compaction":
            # verbose SSE 会携带 level/token/归档数量；这里只保留诊断字段，避免摘要正文泄漏到日志。
            compact = {
                key: item[key]
                for key in (
                    "status",
                    "level",
                    "reason",
                    "tokens_before",
                    "tokens_after",
                    "messages_removed",
                    "rounds_removed",
                    "archive_count",
                )
                if item.get(key) is not None
            }
            if compact and compact not in evidence.compaction_events:
                evidence.compaction_events.append(compact)

        tool_name = item.get("tool_name")
        if isinstance(tool_name, str) and tool_name:
            evidence.tool_names.add(tool_name)
        if item.get("type") in {"tool_call", "tool_call_delta"}:
            name = item.get("name")
            if isinstance(name, str) and name:
                evidence.tool_names.add(name)


async def _iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    event_name = "message"
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                yield event_name, json.loads("\n".join(data_lines))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    response = await client.request(method, path, headers=headers, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} failed ({response.status_code}): {response.text[:1000]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{method} {path} returned non-object JSON")
    return payload


async def _create_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    thread_id: str,
    agent_id: str,
    query: str,
    tag: str,
) -> RunEvidence:
    started = time.monotonic()
    run_payload = await _request_json(
        client,
        "POST",
        "/api/agent/runs",
        headers=headers,
        json={
            "query": query,
            "agent_id": agent_id,
            "thread_id": thread_id,
            "meta": {
                # 运行记录列限制为 VARCHAR(64)，验收 ID 保留场景前缀和随机段即可保证可追踪且唯一。
                "request_id": f"ctx-{tag}-{uuid.uuid4().hex[:12]}",
                "source": "chat-iframe",
                "iframe_context": {
                    "page": {
                        "title": "多级上下文压缩 API 验收",
                        "url": f"api-e2e://{tag}",
                        "text": "该页面用于验证多轮对话、Skills、工具和上下文压缩后的任务连续性。",
                    },
                    "files": [],
                    "prepare_file_paths": False,
                },
            },
        },
    )
    run_id = str(run_payload.get("run_id") or run_payload.get("id") or "")
    if not run_id:
        raise RuntimeError(f"创建 Agent Run 后没有 run_id: {run_payload}")

    evidence = RunEvidence(run_id=run_id)
    try:
        async with asyncio.timeout(client.timeout.read or 300.0):
            async with client.stream(
                "GET",
                f"/api/agent/runs/{run_id}/events",
                params={"verbose": "true"},
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise RuntimeError(f"读取 Run SSE 失败 ({response.status_code}): {body[:1000]!r}")
                async for event_name, payload in _iter_sse(response):
                    _collect_event_evidence(payload, evidence)
                    if event_name == "error":
                        raise RuntimeError(f"Run SSE 返回 error: {payload}")
                    if event_name == "end":
                        break
    except TimeoutError as exc:
        raise RuntimeError(f"Run {run_id} 在限定时间内未结束") from exc

    run_result = await _request_json(client, "GET", f"/api/agent/runs/{run_id}", headers=headers)
    run = run_result.get("run") or {}
    evidence.status = str(run.get("status") or "")
    evidence.elapsed_seconds = round(time.monotonic() - started, 2)
    if evidence.status != "completed":
        raise RuntimeError(f"Run {run_id} 未完成: {run}")
    return evidence


async def _latest_assistant_text(client: httpx.AsyncClient, headers: dict[str, str], thread_id: str) -> str:
    payload = await _request_json(client, "GET", f"/api/chat/thread/{thread_id}/history", headers=headers)
    history = payload.get("history") or []
    latest_user_index = -1
    for index, message in enumerate(history):
        if isinstance(message, dict) and (message.get("type") == "human" or message.get("role") == "user"):
            latest_user_index = index

    assistant_parts: list[str] = []
    for message in history[latest_user_index + 1 :]:
        if not isinstance(message, dict):
            continue
        # 历史接口沿用 LangGraph 类型名 ai，其他客户端可能返回标准 assistant role。
        if message.get("type") != "ai" and message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            assistant_parts.append(content)
            continue
        if isinstance(content, list):
            text = "".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
            if text:
                assistant_parts.append(text)
    # 一个 Run 在工具调用前后会持久化为多个 AI 消息，验收必须覆盖本轮完整执行过程。
    return "\n".join(assistant_parts)


def _has_finished_l5(evidence: RunEvidence) -> bool:
    return any(event.get("level") == "L5" and event.get("status") == "finished" for event in evidence.compaction_events)


async def _main(args: argparse.Namespace) -> None:
    tag = time.strftime("%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    scope = f"{args.source_system}:{args.function_id}:{args.business_id}"
    early_marker = f"EARLY-CONTEXT-{tag}"
    final_marker = f"FINAL-CONTEXT-{tag}"
    output_path = f"/home/gem/user-data/outputs/context-api-e2e-{tag}.md"
    timeout = httpx.Timeout(args.run_timeout, connect=10.0)

    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=timeout) as client:
        token_payload = await _request_json(
            client,
            "POST",
            "/api/chat-iframe/token",
            json={
                "source_system": args.source_system,
                "external_user_id": args.external_user_id,
                "external_user_name": args.external_user_name,
            },
        )
        token = str(token_payload.get("access_token") or "")
        if not token:
            raise RuntimeError("chat-iframe 换票响应缺少 access_token")
        headers = {"Authorization": f"Bearer {token}"}

        thread = await _request_json(
            client,
            "POST",
            "/api/chat/thread",
            headers=headers,
            json={
                "agent_id": args.agent_id,
                "title": f"上下文压缩API验收-{tag}",
                "metadata": {"source": "chat-iframe", "conversation_scope_key": scope},
            },
        )
        thread_id = str(thread.get("id") or thread.get("thread_id") or "")
        if not thread_id:
            raise RuntimeError(f"创建线程后没有 thread_id: {thread}")

        evidences: list[RunEvidence] = []
        bootstrap = (
            f"这是多级上下文压缩验收，早期硬约束标记为 {early_marker}，禁止实现 L4。"
            "请依次使用 read_file 阅读 /skills/visualization/SKILL.md、/skills/mindmap/SKILL.md "
            "和 /skills/office-export/SKILL.md；然后使用 write_file 创建文件 "
            f"{output_path}，写入早期标记、禁止实现 L4、三个 Skill 名称和待办“压缩后继续修改本文件”。"
            "完成后只简短回复早期标记和文件路径。"
        )
        bootstrap_evidence = await _create_run(
            client,
            headers,
            thread_id=thread_id,
            agent_id=args.agent_id,
            query=bootstrap,
            tag=tag,
        )
        evidences.append(bootstrap_evidence)
        print(
            json.dumps(
                {
                    "phase": "bootstrap",
                    "run_id": bootstrap_evidence.run_id,
                    "seconds": bootstrap_evidence.elapsed_seconds,
                    "tools": sorted(bootstrap_evidence.tool_names),
                    "compaction": bootstrap_evidence.compaction_events,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        # 大背景放进多条真实用户消息而不是一次塞爆最新请求，才能验收“旧轮可压缩、最新轮保留”。
        filler_unit = "背景材料仅用于形成上下文压力，不改变早期硬约束。"
        filler = (filler_unit * (args.payload_chars // len(filler_unit) + 1))[: args.payload_chars]
        l5_seen = _has_finished_l5(bootstrap_evidence)
        growth_rounds = 0
        for round_index in range(1, args.max_growth_rounds + 1):
            round_marker = f"GROWTH-{tag}-{round_index:02d}"
            query = (
                f"第 {round_index} 轮背景登记，轮次标记 {round_marker}。"
                "下面是背景材料；不要调用工具，不要复述材料，只回复本轮标记。\n"
                f"{filler}\n本轮标记仍为 {round_marker}。"
            )
            evidence = await _create_run(
                client,
                headers,
                thread_id=thread_id,
                agent_id=args.agent_id,
                query=query,
                tag=tag,
            )
            evidences.append(evidence)
            growth_rounds = round_index
            l5_seen = l5_seen or _has_finished_l5(evidence)
            print(
                json.dumps(
                    {
                        "phase": "growth",
                        "round": round_index,
                        "run_id": evidence.run_id,
                        "seconds": evidence.elapsed_seconds,
                        "compaction": evidence.compaction_events,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if l5_seen:
                break

        if not l5_seen and not args.allow_no_l5:
            raise RuntimeError("达到最大增长轮次仍未观察到 L5 finished；请增加 --max-growth-rounds 或 --payload-chars")

        verify_query = (
            "现在执行压缩后恢复验收：不要从本条消息猜答案，先复述第一轮的早期硬约束标记、"
            "禁止项、三个已激活 Skill 名称和输出文件路径；再用 read_file 读取第一轮创建的文件，"
            f"保留原内容并用 write_file 追加最终标记 {final_marker} 和“恢复验收通过”。"
            "最后回复早期标记、最终标记、文件路径和下一步待办。"
        )
        verify_evidence = await _create_run(
            client,
            headers,
            thread_id=thread_id,
            agent_id=args.agent_id,
            query=verify_query,
            tag=tag,
        )
        evidences.append(verify_evidence)
        assistant_text = await _latest_assistant_text(client, headers, thread_id)
        file_payload = await _request_json(
            client,
            "GET",
            f"/api/chat/thread/{thread_id}/files/content",
            headers=headers,
            params={"path": output_path},
        )
        file_text = "\n".join(str(line) for line in file_payload.get("content") or [])

        required_tool_names = {"read_file", "write_file"}
        all_tool_names = set().union(*(evidence.tool_names for evidence in evidences))
        missing_tools = required_tool_names - all_tool_names
        if missing_tools:
            raise RuntimeError(f"没有观察到必需工具调用: {sorted(missing_tools)}")
        for required in (early_marker, final_marker):
            if required not in assistant_text:
                raise RuntimeError(f"最终回复丢失标记 {required}: {assistant_text[:1000]}")
            if required not in file_text:
                raise RuntimeError(f"恢复后的文件丢失标记 {required}: {file_text[:1000]}")
        if "L4" not in assistant_text or "L4" not in file_text:
            raise RuntimeError("压缩后丢失“禁止实现 L4”硬约束")

        visible_threads = await client.get(
            "/api/chat/threads",
            headers=headers,
            params={
                "agent_id": args.agent_id,
                "conversation_scope_key": scope,
                "limit": 100,
                "offset": 0,
            },
        )
        visible_threads.raise_for_status()
        if thread_id not in {str(item.get("id")) for item in visible_threads.json()}:
            raise RuntimeError("验收线程没有出现在 chat-iframe 对应业务 scope 的会话列表中")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "thread_id": thread_id,
                    "title": thread.get("title"),
                    "conversation_scope_key": scope,
                    "growth_rounds": growth_rounds,
                    "tools": sorted(all_tool_names),
                    "compaction_events": [event for evidence in evidences for event in evidence.compaction_events],
                    "early_marker": early_marker,
                    "final_marker": final_marker,
                    "output_path": output_path,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(_main(_parse_args()))
