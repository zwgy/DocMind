from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from yuxi.agents.middlewares.context_projection import (
    ToolProtocolError,
    group_messages_by_api_round,
    projectable_rounds,
)


def _tool_round(index: int) -> list:
    call_id = f"call-{index}"
    return [
        AIMessage(content="", id=f"assistant-{index}", tool_calls=[{"id": call_id, "name": "read_file", "args": {}}]),
        ToolMessage(content="result", id=f"tool-{index}", name="read_file", tool_call_id=call_id),
    ]


@pytest.mark.unit
def test_groups_serial_and_parallel_tool_calls_at_api_boundaries() -> None:
    messages = [
        HumanMessage(content="start", id="user"),
        AIMessage(
            content="",
            id="assistant-1",
            tool_calls=[
                {"id": "call-a", "name": "read_file", "args": {}},
                {"id": "call-b", "name": "read_file", "args": {}},
            ],
        ),
        ToolMessage(content="a", tool_call_id="call-a", name="read_file"),
        ToolMessage(content="b", tool_call_id="call-b", name="read_file"),
        *_tool_round(2),
        AIMessage(content="done", id="assistant-3"),
    ]

    rounds = group_messages_by_api_round(messages)

    assert [(round_.start, round_.end, round_.tool_call_ids) for round_ in rounds] == [
        (0, 4, ("call-a", "call-b")),
        (4, 6, ("call-2",)),
        (6, 7, ()),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "messages",
    [
        [AIMessage(content="", tool_calls=[{"id": "call-a", "name": "read_file", "args": {}}])],
        [
            AIMessage(content="", tool_calls=[{"id": "call-a", "name": "read_file", "args": {}}]),
            ToolMessage(content="a", tool_call_id="call-a", name="read_file"),
            ToolMessage(content="a again", tool_call_id="call-a", name="read_file"),
        ],
        [ToolMessage(content="unknown", tool_call_id="call-missing", name="read_file")],
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call-a", "name": "read_file", "args": {}},
                    {"id": "call-a", "name": "read_file", "args": {}},
                ],
            ),
            ToolMessage(content="a", tool_call_id="call-a", name="read_file"),
        ],
    ],
)
def test_rejects_incomplete_duplicate_and_unknown_tool_protocol(messages: list) -> None:
    with pytest.raises(ToolProtocolError):
        group_messages_by_api_round(messages)


@pytest.mark.unit
def test_rejects_a_tool_call_id_reused_by_a_later_api_round() -> None:
    messages = [
        AIMessage(content="", id="assistant-1", tool_calls=[{"id": "call-a", "name": "read_file", "args": {}}]),
        ToolMessage(content="first", tool_call_id="call-a", name="read_file"),
        AIMessage(content="", id="assistant-2", tool_calls=[{"id": "call-a", "name": "read_file", "args": {}}]),
        ToolMessage(content="second", tool_call_id="call-a", name="read_file"),
    ]

    with pytest.raises(ToolProtocolError, match="reused across API rounds"):
        group_messages_by_api_round(messages)


@pytest.mark.unit
def test_projectable_rounds_excludes_latest_human_turn_and_confirmation_round() -> None:
    messages = [
        HumanMessage(content="old", id="old-user"),
        *_tool_round(1),
        AIMessage(
            content="",
            id="assistant-confirm",
            tool_calls=[{"id": "confirm", "name": "ask_user_question", "args": {}}],
        ),
        ToolMessage(content="pending choice", tool_call_id="confirm", name="ask_user_question"),
        HumanMessage(content="current", id="current-user"),
        *_tool_round(3),
    ]

    assert [round_.tool_call_ids for round_ in projectable_rounds(messages, scope_end=6)] == [("call-1",)]


@pytest.mark.unit
def test_projectable_rounds_protects_recent_closed_rounds_inside_one_human_turn() -> None:
    messages = [HumanMessage(content="current", id="current-user")]
    for index in range(30):
        messages.extend(_tool_round(index))

    rounds = projectable_rounds(messages, scope_end=len(messages), protected_tail_rounds=2)

    assert len(rounds) == 28
    assert rounds[0].tool_call_ids == ("call-0",)
    assert rounds[-1].tool_call_ids == ("call-27",)
