from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode
from langgraph.graph import END, START, StateGraph

from yuxi.agents.artifacts import (
    ARTIFACT_DELIVERY_SCHEMA,
    delivered_artifact_paths,
    normalize_artifact_path,
)
from yuxi.agents.backends.sandbox import (
    VIRTUAL_PATH_PREFIX,
    ensure_thread_dirs,
    sandbox_outputs_dir,
    sandbox_uploads_dir,
)
from yuxi.agents.buildin.chatbot.state import merge_subagent_runs
from yuxi.agents.state import BaseState, merge_artifacts
from yuxi.agents.toolkits.buildin.tools import present_artifacts
from yuxi.services.chat_service import extract_agent_state
from yuxi.utils.paths import CONVERSATION_HISTORY_DIR_NAME, LARGE_TOOL_RESULTS_DIR_NAME


def _runtime_with_thread(thread_id: str, uid: str = "user-1"):
    context = type("RuntimeContext", (), {"thread_id": thread_id, "uid": uid})()
    return type("RuntimeStub", (), {"context": context})()


@dataclass
class _ArtifactRuntimeContext:
    thread_id: str
    uid: str


def test_merge_artifacts_deduplicates_and_preserves_order():
    assert merge_artifacts(
        ["/home/gem/user-data/outputs/a.md"],
        ["/home/gem/user-data/outputs/a.md", "/home/gem/user-data/outputs/b.md"],
    ) == [
        "/home/gem/user-data/outputs/a.md",
        "/home/gem/user-data/outputs/b.md",
    ]


def test_merge_subagent_runs_updates_existing_run_by_id():
    assert merge_subagent_runs(
        [{"id": "run-1", "status": "completed", "result_preview": "old"}],
        [
            {"id": "run-1", "status": "failed", "error": "boom"},
            {"id": "run-2", "status": "completed"},
        ],
    ) == [
        {"id": "run-1", "status": "failed", "result_preview": "old", "error": "boom"},
        {"id": "run-2", "status": "completed"},
    ]


def test_normalize_presented_artifact_path_accepts_host_path():
    thread_id = "artifacts-host-path"
    ensure_thread_dirs(thread_id, "user-1")
    output_file = sandbox_outputs_dir(thread_id) / "report.md"
    output_file.write_text("# demo", encoding="utf-8")

    normalized = normalize_artifact_path(str(output_file), _runtime_with_thread(thread_id))

    assert normalized == f"{VIRTUAL_PATH_PREFIX}/outputs/report.md"


def test_normalize_presented_artifact_path_accepts_virtual_path():
    thread_id = "artifacts-virtual-path"
    ensure_thread_dirs(thread_id, "user-1")
    output_file = sandbox_outputs_dir(thread_id) / "summary.txt"
    output_file.write_text("demo", encoding="utf-8")

    normalized = normalize_artifact_path(
        f"{VIRTUAL_PATH_PREFIX}/outputs/summary.txt",
        _runtime_with_thread(thread_id),
    )

    assert normalized == f"{VIRTUAL_PATH_PREFIX}/outputs/summary.txt"


def test_normalize_presented_artifact_path_rejects_non_outputs_path():
    thread_id = "artifacts-reject-path"
    ensure_thread_dirs(thread_id, "user-1")
    upload_file = sandbox_uploads_dir(thread_id) / "note.txt"
    upload_file.write_text("demo", encoding="utf-8")

    try:
        normalize_artifact_path(str(upload_file), _runtime_with_thread(thread_id))
    except ValueError as exc:
        assert f"{VIRTUAL_PATH_PREFIX}/outputs/" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-outputs file")


def test_normalize_presented_artifact_path_rejects_internal_output_files():
    thread_id = "artifacts-reject-internal"
    ensure_thread_dirs(thread_id, "user-1")

    for dir_name in [LARGE_TOOL_RESULTS_DIR_NAME, CONVERSATION_HISTORY_DIR_NAME, "large_tool_history"]:
        output_file = sandbox_outputs_dir(thread_id) / dir_name / "stage.txt"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("internal", encoding="utf-8")

        try:
            normalize_artifact_path(str(output_file), _runtime_with_thread(thread_id))
        except ValueError as exc:
            assert "工具调用阶段文件" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for internal output file under {dir_name}")


def test_present_artifacts_records_paths_on_successful_tool_message():
    thread_id = "artifacts-present"
    ensure_thread_dirs(thread_id, "user-1")
    output_file = sandbox_outputs_dir(thread_id) / "report.md"
    output_file.write_text("# demo", encoding="utf-8")

    command = present_artifacts.func(
        [f"{VIRTUAL_PATH_PREFIX}/outputs/report.md"],
        _runtime_with_thread(thread_id),
        "call-present",
    )
    tool_message = command.update["messages"][0]

    expected = [f"{VIRTUAL_PATH_PREFIX}/outputs/report.md"]
    assert command.update["artifacts"] == expected
    assert tool_message.artifact == {
        "schema": ARTIFACT_DELIVERY_SCHEMA,
        "paths": expected,
    }
    assert delivered_artifact_paths(tool_message.model_dump()) == expected


@pytest.mark.asyncio
async def test_tool_node_preserves_artifact_delivery_command() -> None:
    thread_id = "artifacts-tool-node"
    ensure_thread_dirs(thread_id, "user-1")
    output_file = sandbox_outputs_dir(thread_id) / "report.md"
    output_file.write_text("# demo", encoding="utf-8")
    builder = StateGraph(BaseState, context_schema=_ArtifactRuntimeContext)
    builder.add_node("tools", ToolNode([present_artifacts]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()

    result = await graph.ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "present_artifacts",
                            "args": {"filepaths": [f"{VIRTUAL_PATH_PREFIX}/outputs/report.md"]},
                            "id": "call-tool-node",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        },
        context=_ArtifactRuntimeContext(thread_id=thread_id, uid="user-1"),
    )

    assert result["artifacts"] == [f"{VIRTUAL_PATH_PREFIX}/outputs/report.md"]
    assert delivered_artifact_paths(result["messages"][-1].model_dump()) == [f"{VIRTUAL_PATH_PREFIX}/outputs/report.md"]


def test_delivered_artifact_paths_rejects_unmarked_or_failed_tool_results():
    assert (
        delivered_artifact_paths(
            {
                "type": "tool",
                "status": "success",
                "artifact": {"paths": ["/home/gem/user-data/outputs/unmarked.md"]},
            }
        )
        == []
    )
    assert (
        delivered_artifact_paths(
            {
                "type": "tool",
                "status": "error",
                "artifact": {
                    "schema": ARTIFACT_DELIVERY_SCHEMA,
                    "paths": ["/home/gem/user-data/outputs/failed.md"],
                },
            }
        )
        == []
    )


def test_extract_agent_state_includes_artifacts():
    state = extract_agent_state(
        {
            "todos": [{"content": "done", "status": "completed"}],
            "files": {"/tmp/demo.txt": {"content": ["x"]}},
            "artifacts": ["/home/gem/user-data/outputs/demo.txt"],
            "subagent_runs": [{"id": "tool-1", "status": "completed"}],
            "token_usage": {"llm_input_tokens": 42},
        }
    )

    assert state["todos"] == [{"content": "done", "status": "completed"}]
    assert state["files"] == {"/tmp/demo.txt": {"content": ["x"]}}
    assert state["artifacts"] == ["/home/gem/user-data/outputs/demo.txt"]
    assert state["subagent_runs"] == [{"id": "tool-1", "status": "completed"}]
    assert state["token_usage"] == {"llm_input_tokens": 42}
