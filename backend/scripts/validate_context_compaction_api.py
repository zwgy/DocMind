"""通过真实 HTTP API 验收多级上下文压缩，并保留可在 chat-iframe 查看的一条线程。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass
class RunEvidence:
    """只保存验收所需诊断，不把大段模型正文复制到脚本输出。"""

    run_id: str
    request_id: str = ""
    status: str = ""
    elapsed_seconds: float = 0.0
    compaction_events: list[dict[str, Any]] = field(default_factory=list)
    output_recovery_events: list[dict[str, Any]] = field(default_factory=list)
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
    parser.add_argument(
        "--model-spec",
        help="仅用于隔离验收会话的 model_spec；不传则沿用智能体默认模型",
    )
    parser.add_argument("--max-growth-rounds", type=int, default=10)
    parser.add_argument("--payload-chars", type=int, default=5000)
    parser.add_argument("--run-timeout", type=float, default=300.0)
    parser.add_argument(
        "--scenario-file",
        type=Path,
        help="JSON 场景文件；每个场景可创建多个隔离线程并串行执行多轮真实提问",
    )
    parser.add_argument(
        "--log-content-limit",
        type=int,
        default=1200,
        help="每轮打印的输入和输出最大字符数；设为 0 时不打印正文",
    )
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
    if args.log_content_limit < 0:
        parser.error("--log-content-limit 不能小于 0")
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
                    "sequence",
                    "cycle_id",
                    "reason",
                    "tokens_before",
                    "tokens_after",
                    "tokens_saved",
                    "messages_before",
                    "messages_after",
                    "candidate_messages",
                    "protected_messages",
                    "input_externalized",
                    "tool_results_projected",
                    "tool_arguments_projected",
                    "messages_removed",
                    "rounds_removed",
                    "archive_count",
                    "archive_path",
                    "summary_revision",
                    "summary_quality",
                )
                if item.get(key) is not None
            }
            if compact and compact not in evidence.compaction_events:
                evidence.compaction_events.append(compact)

        if item.get("type") == "output_recovery":
            recovery = {
                key: item[key]
                for key in (
                    "status",
                    "mode",
                    "attempt",
                    "previous_output_tokens",
                    "target_output_tokens",
                    "prompt_budget",
                )
                if item.get(key) is not None
            }
            if recovery and recovery not in evidence.output_recovery_events:
                evidence.output_recovery_events.append(recovery)

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
    request_id = f"ctx-{tag}-{uuid.uuid4().hex[:12]}"
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
                "request_id": request_id,
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

    # 以服务端回显为准，确保脚本报告能直接对应 LangSmith metadata 和后端日志。
    evidence = RunEvidence(run_id=run_id, request_id=str(run_payload.get("request_id") or request_id))
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


def _load_scenario(path: Path) -> dict[str, Any]:
    """加载小而明确的 JSON 场景，避免验收口径散落在临时命令行参数中。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"场景文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"场景文件不是合法 JSON: {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("场景根节点必须是 JSON object")
    if not isinstance(payload.get("name"), str) or not payload["name"].strip():
        raise ValueError("场景必须提供非空 name")
    threads = payload.get("threads")
    if not isinstance(threads, list) or not threads:
        raise ValueError("场景必须提供至少一个 threads 项")
    for thread_index, thread in enumerate(threads, start=1):
        if not isinstance(thread, dict):
            raise ValueError(f"threads[{thread_index}] 必须是 object")
        if not isinstance(thread.get("name"), str) or not thread["name"].strip():
            raise ValueError(f"threads[{thread_index}] 必须提供非空 name")
        turns = thread.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"threads[{thread_index}] 必须提供至少一个 turns 项")
        attachments = thread.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > 1:
            raise ValueError(f"threads[{thread_index}].attachments 必须是最多一项的数组")
        for attachment in attachments:
            if (
                not isinstance(attachment, dict)
                or not isinstance(attachment.get("file_name"), str)
                or not attachment["file_name"].strip()
                or not isinstance(attachment.get("content"), str)
            ):
                raise ValueError(f"threads[{thread_index}].attachments 必须提供 file_name 和 content")
        if "expect" in thread and not isinstance(thread["expect"], dict):
            raise ValueError(f"threads[{thread_index}].expect 必须是 object")
        for turn_index, turn in enumerate(turns, start=1):
            if not isinstance(turn, dict) or not isinstance(turn.get("query"), str) or not turn["query"].strip():
                raise ValueError(f"threads[{thread_index}].turns[{turn_index}] 必须提供非空 query")
            if "expect" in turn and not isinstance(turn["expect"], dict):
                raise ValueError(f"threads[{thread_index}].turns[{turn_index}].expect 必须是 object")
    return payload


def _render_scenario_value(value: Any, bindings: dict[str, str]) -> Any:
    """只替换已声明占位符，保留用户问题中的其他花括号和代码片段。"""
    if isinstance(value, str):
        for key, replacement in bindings.items():
            value = value.replace(f"{{{key}}}", replacement)
        return value
    if isinstance(value, list):
        return [_render_scenario_value(item, bindings) for item in value]
    if isinstance(value, dict):
        return {key: _render_scenario_value(item, bindings) for key, item in value.items()}
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} 必须是字符串数组")
    return value


def _compact_log_text(value: str, limit: int) -> str:
    if limit == 0:
        return ""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... [已截断，共 {len(value)} 字符]"


async def _read_thread_file(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    thread_id: str,
    path: str,
) -> str:
    payload = await _request_json(
        client,
        "GET",
        f"/api/chat/thread/{thread_id}/files/content",
        headers=headers,
        params={"path": path},
    )
    return "\n".join(str(line) for line in payload.get("content") or [])


async def _validate_scenario_turn(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    thread_id: str,
    assistant_text: str,
    evidence: RunEvidence,
    expect: dict[str, Any],
) -> list[str]:
    """优先校验可重复的协议事实，避免让本地小模型成为唯一裁判。"""
    allowed_fields = {
        "status",
        "assistant_contains",
        "assistant_not_contains",
        "assistant_matches",
        "tools_include",
        "tools_exclude",
        "compaction",
        "compaction_order",
        "output_recovery",
        "files",
    }
    unknown_fields = set(expect) - allowed_fields
    if unknown_fields:
        raise ValueError(f"不支持的 expect 字段: {sorted(unknown_fields)}")

    failures: list[str] = []
    expected_status = expect.get("status", "completed")
    if not isinstance(expected_status, str):
        raise ValueError("expect.status 必须是字符串")
    if evidence.status != expected_status:
        failures.append(f"运行状态为 {evidence.status!r}，期望 {expected_status!r}")

    for required in _string_list(expect.get("assistant_contains"), "expect.assistant_contains"):
        if required not in assistant_text:
            failures.append(f"回复缺少文本: {required!r}")
    for forbidden in _string_list(expect.get("assistant_not_contains"), "expect.assistant_not_contains"):
        if forbidden in assistant_text:
            failures.append(f"回复包含禁止文本: {forbidden!r}")
    for pattern in _string_list(expect.get("assistant_matches"), "expect.assistant_matches"):
        try:
            matched = re.search(pattern, assistant_text, flags=re.DOTALL) is not None
        except re.error as exc:
            raise ValueError(f"expect.assistant_matches 包含非法正则 {pattern!r}: {exc}") from exc
        if not matched:
            failures.append(f"回复未匹配正则: {pattern!r}")

    tools_include = set(_string_list(expect.get("tools_include"), "expect.tools_include"))
    tools_exclude = set(_string_list(expect.get("tools_exclude"), "expect.tools_exclude"))
    missing_tools = tools_include - evidence.tool_names
    unexpected_tools = tools_exclude & evidence.tool_names
    if missing_tools:
        failures.append(f"缺少工具调用: {sorted(missing_tools)}")
    if unexpected_tools:
        failures.append(f"出现禁止工具调用: {sorted(unexpected_tools)}")

    compaction_expectations = expect.get("compaction", [])
    if isinstance(compaction_expectations, dict):
        compaction_expectations = [compaction_expectations]
    if not isinstance(compaction_expectations, list):
        raise ValueError("expect.compaction 必须是 object 或 object 数组")
    for item in compaction_expectations:
        if not isinstance(item, dict):
            raise ValueError("expect.compaction 的每项必须是 object")
        min_count = item.get("min_count", 1)
        if not isinstance(min_count, int) or min_count < 1:
            raise ValueError("expect.compaction.min_count 必须是大于 0 的整数")
        min_values = item.get("min_values", {})
        if not isinstance(min_values, dict) or not all(
            isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool)
            for key, value in min_values.items()
        ):
            raise ValueError("expect.compaction.min_values 必须是数值字段 object")
        criteria = {key: value for key, value in item.items() if key not in {"min_count", "min_values"}}
        if not criteria:
            raise ValueError("expect.compaction 至少需要一个匹配字段")
        matching_events = [
            event
            for event in evidence.compaction_events
            if all(event.get(key) == value for key, value in criteria.items())
            and all(
                isinstance(event.get(key), (int, float))
                and not isinstance(event.get(key), bool)
                and event[key] >= minimum
                for key, minimum in min_values.items()
            )
        ]
        actual_count = len(matching_events)
        if actual_count < min_count:
            failures.append(
                f"压缩事件 {criteria} 且最小值 {min_values} 只出现 {actual_count} 次，期望至少 {min_count} 次"
            )

    expected_order = _string_list(expect.get("compaction_order"), "expect.compaction_order")
    if expected_order:
        events_by_cycle: dict[str, list[str]] = {}
        for event in evidence.compaction_events:
            cycle_id = event.get("cycle_id")
            level = event.get("level")
            if isinstance(cycle_id, str) and isinstance(level, str):
                levels = events_by_cycle.setdefault(cycle_id, [])
                if not levels or levels[-1] != level:
                    levels.append(level)
        if expected_order not in events_by_cycle.values():
            failures.append(f"没有压缩周期满足层级顺序 {expected_order}: {events_by_cycle}")

    recovery_expectations = expect.get("output_recovery", [])
    if isinstance(recovery_expectations, dict):
        recovery_expectations = [recovery_expectations]
    if not isinstance(recovery_expectations, list):
        raise ValueError("expect.output_recovery 必须是 object 或 object 数组")
    for item in recovery_expectations:
        if not isinstance(item, dict):
            raise ValueError("expect.output_recovery 的每项必须是 object")
        min_count = item.get("min_count", 1)
        if not isinstance(min_count, int) or min_count < 1:
            raise ValueError("expect.output_recovery.min_count 必须是大于 0 的整数")
        min_values = item.get("min_values", {})
        if not isinstance(min_values, dict) or not all(
            isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool)
            for key, value in min_values.items()
        ):
            raise ValueError("expect.output_recovery.min_values 必须是数值字段 object")
        criteria = {key: value for key, value in item.items() if key not in {"min_count", "min_values"}}
        if not criteria:
            raise ValueError("expect.output_recovery 至少需要一个匹配字段")
        matching_events = [
            event
            for event in evidence.output_recovery_events
            if all(event.get(key) == value for key, value in criteria.items())
            and all(
                isinstance(event.get(key), (int, float))
                and not isinstance(event.get(key), bool)
                and event[key] >= minimum
                for key, minimum in min_values.items()
            )
        ]
        if len(matching_events) < min_count:
            failures.append(
                f"输出恢复事件 {criteria} 且最小值 {min_values} 只出现 {len(matching_events)} 次，期望至少 {min_count} 次"
            )

    file_expectations = expect.get("files", [])
    if not isinstance(file_expectations, list):
        raise ValueError("expect.files 必须是数组")
    for item in file_expectations:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("expect.files 的每项必须提供字符串 path")
        file_text = await _read_thread_file(client, headers, thread_id=thread_id, path=item["path"])
        for required in _string_list(item.get("contains"), f"expect.files[{item['path']}].contains"):
            if required not in file_text:
                failures.append(f"文件 {item['path']} 缺少文本: {required!r}")
        for forbidden in _string_list(item.get("not_contains"), f"expect.files[{item['path']}].not_contains"):
            if forbidden in file_text:
                failures.append(f"文件 {item['path']} 包含禁止文本: {forbidden!r}")
    return failures


async def _run_configured_scenario(args: argparse.Namespace) -> None:
    scenario = _load_scenario(args.scenario_file)
    tag = time.strftime("%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    scope = f"{args.source_system}:{args.function_id}:{args.business_id}"
    timeout = httpx.Timeout(args.run_timeout, connect=10.0)
    reports: list[dict[str, Any]] = []
    failures: list[str] = []

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

        for thread_index, definition in enumerate(scenario["threads"], start=1):
            thread_name = definition["name"].strip()
            filler_unit = "上下文压力材料仅用于触发预算准入，不改变本轮明确约束。\n"
            payload = (filler_unit * (args.payload_chars // len(filler_unit) + 1))[: args.payload_chars]
            bindings = {"tag": tag, "thread_name": thread_name, "payload": payload}
            if args.model_spec:
                bindings["model_spec"] = args.model_spec
            metadata = _render_scenario_value(definition.get("metadata", {}), bindings)
            if not isinstance(metadata, dict):
                raise ValueError(f"threads[{thread_index}].metadata 必须是 object")
            metadata["source"] = "chat-iframe"
            metadata["conversation_scope_key"] = scope
            title = _render_scenario_value(definition.get("title", f"{scenario['name']}-{thread_name}-{tag}"), bindings)
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"threads[{thread_index}].title 必须是非空字符串")
            agent_id = definition.get("agent_id", args.agent_id)
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError(f"threads[{thread_index}].agent_id 必须是非空字符串")
            thread = await _request_json(
                client,
                "POST",
                "/api/chat/thread",
                headers=headers,
                json={"agent_id": agent_id, "title": title, "metadata": metadata},
            )
            thread_id = str(thread.get("id") or thread.get("thread_id") or "")
            if not thread_id:
                raise RuntimeError(f"创建场景线程后没有 thread_id: {thread}")
            bindings["thread_id"] = thread_id
            thread_report: dict[str, Any] = {
                "thread": thread_name,
                "thread_id": thread_id,
                "title": title,
                "turns": [],
            }
            thread_evidences: list[RunEvidence] = []
            latest_assistant_text = ""
            reports.append(thread_report)
            print(
                json.dumps(
                    {"event": "thread_created", "scenario": scenario["name"], **thread_report},
                    ensure_ascii=False,
                ),
                flush=True,
            )

            attachments = _render_scenario_value(definition.get("attachments", []), bindings)
            if attachments:
                attachment = attachments[0]
                attachment_payload = await _request_json(
                    client,
                    "POST",
                    f"/api/chat/thread/{thread_id}/attachments",
                    headers=headers,
                    files={
                        "file": (
                            attachment["file_name"],
                            attachment["content"].encode("utf-8"),
                            "text/plain",
                        )
                    },
                )
                attachment_path = str(attachment_payload.get("path") or "")
                if not attachment_path:
                    raise RuntimeError(f"线程附件上传后缺少 path: {attachment_payload}")
                bindings["attachment_path"] = attachment_path
                thread_report["attachment"] = {
                    "file_name": attachment_payload.get("file_name"),
                    "path": attachment_path,
                    "size": attachment_payload.get("file_size"),
                }
                print(
                    json.dumps(
                        {"event": "attachment_uploaded", "thread": thread_name, **thread_report["attachment"]},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

            for turn_index, turn in enumerate(definition["turns"], start=1):
                turn_name = str(turn.get("name") or f"turn-{turn_index}")
                query = _render_scenario_value(turn["query"], bindings)
                expect = _render_scenario_value(turn.get("expect", {}), bindings)
                if not isinstance(query, str) or not isinstance(expect, dict):
                    raise ValueError(f"threads[{thread_index}].turns[{turn_index}] 配置类型错误")
                try:
                    evidence = await _create_run(
                        client,
                        headers,
                        thread_id=thread_id,
                        agent_id=agent_id,
                        query=query,
                        tag=tag,
                    )
                    assistant_text = await _latest_assistant_text(client, headers, thread_id)
                    latest_assistant_text = assistant_text
                    thread_evidences.append(evidence)
                    turn_failures = await _validate_scenario_turn(
                        client,
                        headers,
                        thread_id=thread_id,
                        assistant_text=assistant_text,
                        evidence=evidence,
                        expect=expect,
                    )
                    turn_report = {
                        "turn": turn_name,
                        "run_id": evidence.run_id,
                        "request_id": evidence.request_id,
                        "status": evidence.status,
                        "seconds": evidence.elapsed_seconds,
                        "tools": sorted(evidence.tool_names),
                        "compaction": evidence.compaction_events,
                        "output_recovery": evidence.output_recovery_events,
                        "input": _compact_log_text(query, args.log_content_limit),
                        "output": _compact_log_text(assistant_text, args.log_content_limit),
                        "passed": not turn_failures,
                        "failures": turn_failures,
                    }
                except (RuntimeError, ValueError) as exc:
                    turn_failures = [str(exc)]
                    turn_report = {
                        "turn": turn_name,
                        "status": "error",
                        "passed": False,
                        "failures": turn_failures,
                    }
                thread_report["turns"].append(turn_report)
                print(
                    json.dumps(
                        {"event": "turn_finished", "thread": thread_name, **turn_report},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                failures.extend(f"{thread_name}/{turn_name}: {message}" for message in turn_failures)

            thread_expect = _render_scenario_value(definition.get("expect", {}), bindings)
            if not isinstance(thread_expect, dict):
                raise ValueError(f"threads[{thread_index}].expect 必须是 object")
            if thread_expect:
                aggregate_evidence = RunEvidence(
                    run_id=f"thread:{thread_id}",
                    status="completed",
                    compaction_events=[
                        event for evidence in thread_evidences for event in evidence.compaction_events
                    ],
                    output_recovery_events=[
                        event for evidence in thread_evidences for event in evidence.output_recovery_events
                    ],
                    tool_names=set().union(*(evidence.tool_names for evidence in thread_evidences)),
                )
                thread_failures = await _validate_scenario_turn(
                    client,
                    headers,
                    thread_id=thread_id,
                    assistant_text=latest_assistant_text,
                    evidence=aggregate_evidence,
                    expect=thread_expect,
                )
                thread_report["expectation_passed"] = not thread_failures
                thread_report["expectation_failures"] = thread_failures
                failures.extend(f"{thread_name}/thread: {message}" for message in thread_failures)

        visible_threads = await client.get(
            "/api/chat/threads",
            headers=headers,
            params={"agent_id": args.agent_id, "conversation_scope_key": scope, "limit": 100, "offset": 0},
        )
        visible_threads.raise_for_status()
        visible_payload = visible_threads.json()
        visible_items = visible_payload if isinstance(visible_payload, list) else visible_payload.get("threads", [])
        visible_ids = {str(item.get("id")) for item in visible_items if isinstance(item, dict)}
        for report in reports:
            report["visible_in_scope"] = report["thread_id"] in visible_ids
            if not report["visible_in_scope"]:
                failures.append(f"{report['thread']}: 未出现在 chat-iframe 对应业务 scope 的会话列表")

    summary = {
        "status": "passed" if not failures else "failed",
        "scenario": scenario["name"],
        "tag": tag,
        "conversation_scope_key": scope,
        "threads": reports,
        "failure_count": len(failures),
        "failures": failures,
    }
    print(json.dumps({"event": "scenario_finished", **summary}, ensure_ascii=False, indent=2))
    if failures:
        raise RuntimeError(f"场景 {scenario['name']} 未通过，共 {len(failures)} 项失败")


async def _main(args: argparse.Namespace) -> None:
    if args.scenario_file:
        await _run_configured_scenario(args)
        return

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
                    "request_id": bootstrap_evidence.request_id,
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
                        "request_id": evidence.request_id,
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
