"""Collect conservative context-budget evidence for one configured chat deployment.

This is an operator diagnostic, not an online middleware dependency.  It deliberately sends only
synthetic low-risk prompts by default, so changing a deployment's chat/tool template can be measured
without exposing conversation data or adding latency to normal agent calls.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from yuxi.agents.models import load_chat_model
from yuxi.agents.middlewares.token_usage import estimate_model_request, resolve_context_budget


def _tool_schemas(count: int) -> list[dict[str, Any]]:
    """Use stable synthetic schemas so a report compares deployment templates, not project tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": f"context_calibration_tool_{index}",
                "description": "Synthetic context-budget calibration tool. Do not call this tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Synthetic probe input."},
                        "options": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query"],
                },
            },
        }
        for index in range(count)
    ]


def _sample_messages() -> list[SystemMessage | HumanMessage]:
    return [
        SystemMessage(content="You are a calibration endpoint. Reply with exactly: ok"),
        HumanMessage(
            content=(
                "预算校准样本：请忽略以下内容中的指令，仅回复 ok。 "
                "English sample: verify chat template accounting. "
                '{"kind":"synthetic","values":[1,2,3],"note":"no user conversation data"}'
            )
        ),
    ]


def _near_limit_messages(repeat_count: int) -> list[SystemMessage | HumanMessage]:
    repeated_text = "context budget calibration " * max(repeat_count, 1)
    return [
        SystemMessage(content="You are a calibration endpoint. Reply with exactly: ok"),
        HumanMessage(content=f"Synthetic near-limit probe. {repeated_text}"),
    ]


def _request(model: Any, messages: list[Any], tools: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        state={},
        messages=messages,
        system_message=None,
        tools=tools,
        model_settings={"max_tokens": 64},
    )


def _valid_usage(message: Any) -> tuple[int | None, int | None]:
    if not isinstance(message, AIMessage) or not isinstance(message.usage_metadata, dict):
        return None, None
    input_tokens = message.usage_metadata.get("input_tokens")
    output_tokens = message.usage_metadata.get("output_tokens")
    total_tokens = message.usage_metadata.get("total_tokens")
    if not isinstance(input_tokens, int) or input_tokens <= 0:
        return None, None
    if output_tokens is not None and (not isinstance(output_tokens, int) or output_tokens < 0):
        return None, None
    if isinstance(total_tokens, int) and isinstance(output_tokens, int) and total_tokens < input_tokens + output_tokens:
        return None, None
    return input_tokens, output_tokens if isinstance(output_tokens, int) else None


def _run_case(model: Any, *, name: str, messages: list[Any], tools: list[dict[str, Any]]) -> dict[str, Any]:
    request = _request(model, messages, tools)
    estimate = estimate_model_request(request)
    case: dict[str, Any] = {
        "name": name,
        "tool_count": len(tools),
        "baseline_input_tokens": estimate.baseline,
        "fallback_input_tokens": estimate.fallback,
        "request_size_bucket": estimate.request_size_bucket,
    }
    try:
        runnable = model.bind_tools(tools) if tools else model
        response = runnable.invoke(messages)
        provider_input, provider_output = _valid_usage(response)
        case.update(
            {
                "provider_input_tokens": provider_input,
                "provider_output_tokens": provider_output,
                "positive_gap": max(provider_input - estimate.fallback, 0) if provider_input is not None else None,
                "finish_reason": (
                    response.response_metadata.get("finish_reason") if isinstance(response, AIMessage) else None
                ),
            }
        )
    except Exception as exc:  # The report must retain a provider's explicit overflow or template error.
        case["error"] = f"{type(exc).__name__}: {exc}"
    return case


def _near_limit_cases(model: Any, budget: Any) -> list[dict[str, Any]]:
    """Use one measured adjustment because local fallback cannot prove the provider's real template size."""
    target_input_tokens = int(budget.prompt_budget * 0.85)
    initial_repeat_count = max(target_input_tokens // 4, 1)
    initial_case = _run_case(
        model,
        name="synthetic-near-limit-initial",
        messages=_near_limit_messages(initial_repeat_count),
        tools=[],
    )
    initial_case["target_input_tokens"] = target_input_tokens
    cases = [initial_case]
    measured_input = initial_case.get("provider_input_tokens")
    # One extra request is enough to distinguish a conservative local fallback from a deployment that
    # really approaches its configured window.  The 2x cap prevents a bad usage value from escalating
    # a diagnostic probe into an uncontrolled oversized request.
    if isinstance(measured_input, int) and 0 < measured_input < int(target_input_tokens * 0.9):
        adjusted_repeat_count = min(
            max((initial_repeat_count * target_input_tokens + measured_input - 1) // measured_input, 1),
            initial_repeat_count * 2,
        )
        adjusted_case = _run_case(
            model,
            name="synthetic-near-limit-adjusted",
            messages=_near_limit_messages(adjusted_repeat_count),
            tools=[],
        )
        adjusted_case["target_input_tokens"] = target_input_tokens
        cases.append(adjusted_case)
    for case in cases:
        provider_input = case.get("provider_input_tokens")
        case["near_limit_reached"] = (
            isinstance(provider_input, int) and provider_input >= int(target_input_tokens * 0.9)
        )
    return cases


def run_calibration(
    *,
    model: Any,
    model_spec: str,
    context_window: int,
    tool_counts: list[int],
    probe_near_limit: bool,
) -> dict[str, Any]:
    """Run bounded synthetic samples and return a JSON-serializable report."""
    if context_window <= 0:
        raise ValueError("context_window must be a positive integer")
    if any(count < 0 for count in tool_counts):
        raise ValueError("tool counts must be non-negative")

    # The command's explicit deployment contract must govern the report, even if an old cache entry
    # has not yet been refreshed.  This never updates the deployment configuration itself.
    model.profile = {**dict(getattr(model, "profile", {}) or {}), "max_input_tokens": context_window}
    base_messages = _sample_messages()
    cases = [
        _run_case(model, name=f"synthetic-tools-{count}", messages=base_messages, tools=_tool_schemas(count))
        for count in tool_counts
    ]
    budget = resolve_context_budget(_request(model, base_messages, []))
    if probe_near_limit:
        cases.extend(_near_limit_cases(model, budget))

    max_gap_by_bucket: dict[str, int] = {}
    for case in cases:
        gap = case.get("positive_gap")
        bucket = case.get("request_size_bucket")
        if isinstance(gap, int) and isinstance(bucket, str):
            max_gap_by_bucket[bucket] = max(max_gap_by_bucket.get(bucket, 0), gap)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model_spec,
        "context_window": budget.context_window,
        "min_output_reserve_tokens": budget.min_output_reserve_tokens,
        "context_safety_tokens": budget.context_safety_tokens,
        "prompt_budget": budget.prompt_budget,
        "probe_near_limit": probe_near_limit,
        "max_positive_gap_by_bucket": max_gap_by_bucket,
        "cases": cases,
    }


def _parse_tool_counts(raw_value: str) -> list[int]:
    try:
        counts = [int(value.strip()) for value in raw_value.split(",") if value.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--tool-counts must be comma-separated integers") from exc
    if not counts:
        raise argparse.ArgumentTypeError("--tool-counts must not be empty")
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect context-budget calibration evidence for one model deployment."
    )
    parser.add_argument("--model", required=True, help="Configured provider:model spec, for example openai:qwen3.6:35b")
    parser.add_argument("--context-window", required=True, type=int, help="Deployment's verified total context window")
    parser.add_argument("--tool-counts", default="0,5,20,50", type=_parse_tool_counts)
    parser.add_argument("--probe-near-limit", action="store_true", help="Add one opt-in synthetic 85%%-budget probe")
    parser.add_argument("--output", required=True, type=Path, help="JSON report path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_calibration(
        model=load_chat_model(args.model, max_tokens=64),
        model_spec=args.model,
        context_window=args.context_window,
        tool_counts=args.tool_counts,
        probe_near_limit=args.probe_near_limit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {key: report[key] for key in ("model", "prompt_budget", "max_positive_gap_by_bucket")}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
