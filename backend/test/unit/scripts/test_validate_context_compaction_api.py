from __future__ import annotations

import json

import pytest

from scripts import validate_context_compaction_api as scenario_api


def test_load_scenario_requires_threads_and_queries(tmp_path):
    scenario_path = tmp_path / "invalid.json"
    scenario_path.write_text(
        json.dumps({"name": "bad", "threads": [{"name": "thread", "turns": [{}]}]}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="非空 query"):
        scenario_api._load_scenario(scenario_path)


def test_render_scenario_value_only_replaces_declared_placeholders():
    rendered = scenario_api._render_scenario_value(
        {"query": '标记 {tag}，JSON 保持 {"value": 1}', "items": ["{thread_name}"]},
        {"tag": "T-1", "thread_name": "continuity"},
    )

    assert rendered == {"query": '标记 T-1，JSON 保持 {"value": 1}', "items": ["continuity"]}


@pytest.mark.asyncio
async def test_validate_scenario_turn_checks_response_tools_compaction_and_files(monkeypatch):
    async def fake_read_thread_file(*args, **kwargs):
        assert kwargs["thread_id"] == "thread-1"
        assert kwargs["path"] == "/home/gem/user-data/outputs/result.md"
        return "CONTRACT-T-1\nDONE-T-1"

    monkeypatch.setattr(scenario_api, "_read_thread_file", fake_read_thread_file)
    evidence = scenario_api.RunEvidence(
        run_id="run-1",
        status="completed",
        tool_names={"read_file", "edit_file"},
        compaction_events=[{"status": "finished", "level": "L5", "rounds_removed": 3}],
    )

    failures = await scenario_api._validate_scenario_turn(
        client=None,
        headers={},
        thread_id="thread-1",
        assistant_text="CONTRACT-T-1 已恢复，DONE-T-1 已完成。",
        evidence=evidence,
        expect={
            "assistant_contains": ["CONTRACT-T-1", "DONE-T-1"],
            "assistant_matches": [r"DONE-T-\d+"],
            "tools_include": ["read_file"],
            "tools_exclude": ["write_file"],
            "compaction": {"status": "finished", "level": "L5"},
            "files": [
                {
                    "path": "/home/gem/user-data/outputs/result.md",
                    "contains": ["CONTRACT-T-1", "DONE-T-1"],
                }
            ],
        },
    )

    assert failures == []


@pytest.mark.asyncio
async def test_validate_scenario_turn_reports_deterministic_failures():
    evidence = scenario_api.RunEvidence(run_id="run-1", status="completed", tool_names={"read_file"})

    failures = await scenario_api._validate_scenario_turn(
        client=None,
        headers={},
        thread_id="thread-1",
        assistant_text="actual response",
        evidence=evidence,
        expect={
            "assistant_contains": ["expected response"],
            "tools_include": ["write_file"],
            "tools_exclude": ["read_file"],
        },
    )

    assert failures == [
        "回复缺少文本: 'expected response'",
        "缺少工具调用: ['write_file']",
        "出现禁止工具调用: ['read_file']",
    ]
