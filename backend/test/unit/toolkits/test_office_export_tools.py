from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.prebuilt.tool_node import ToolRuntime

from yuxi.agents.artifacts import ARTIFACT_DELIVERY_SCHEMA
from yuxi.agents.backends.sandbox import ensure_thread_dirs, sandbox_outputs_dir
from yuxi.agents.toolkits.office_export import tools
from yuxi.agents.toolkits.office_export import export_office_file
from yuxi.agents.toolkits.registry import get_extra_metadata


def _runtime() -> ToolRuntime:
    return ToolRuntime(
        state={},
        tool_call_id="call-1",
        config={"configurable": {}},
        context=SimpleNamespace(uid="user-1", thread_id="thread-1"),
        store=None,
        stream_writer=lambda _: None,
    )


def test_office_export_tool_is_skill_gated_and_hides_runtime() -> None:
    metadata = get_extra_metadata(export_office_file.name)

    assert metadata is not None
    assert metadata.category == "document"
    assert "runtime" not in export_office_file.tool_call_schema.model_fields


def test_office_export_tool_schema_accepts_only_supported_formats() -> None:
    parsed = export_office_file.tool_call_schema.model_validate(
        {
            "definition_path": "/home/gem/user-data/outputs/report.json",
            "output_format": "docx",
            "output_name": "检查报告",
        }
    )

    assert parsed.output_format == "docx"


@pytest.mark.asyncio
async def test_office_export_tool_passes_runtime_scope_to_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = tmp_path / "report.json"
    definition.write_text('{"kind":"document","blocks":[]}', encoding="utf-8")
    captured: dict = {}

    def fake_source_resolver(thread_id: str, uid: str):
        assert (thread_id, uid) == ("thread-1", "user-1")
        return lambda *_: definition

    async def fake_export(**kwargs):
        captured.update(kwargs)
        ensure_thread_dirs("thread-1", "user-1")
        sandbox_outputs_dir("thread-1").joinpath("report.docx").write_bytes(b"docx")
        return {"artifact_path": "/home/gem/user-data/outputs/report.docx"}

    monkeypatch.setattr(tools, "_source_resolver", fake_source_resolver)
    monkeypatch.setattr(tools, "run_office_export", fake_export)
    result = await export_office_file.coroutine(
        definition_path="/home/gem/user-data/outputs/report.json",
        output_format="docx",
        output_name="检查报告",
        tool_call_id="call-1",
        runtime=_runtime(),
    )

    tool_message = result.update["messages"][0]
    assert tool_message.artifact == {
        "schema": ARTIFACT_DELIVERY_SCHEMA,
        "paths": ["/home/gem/user-data/outputs/report.docx"],
    }
    assert captured["definition_path"] == definition
    assert captured["output_directory"] == sandbox_outputs_dir("thread-1").resolve()
