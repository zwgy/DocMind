"""Tool-protocol-safe API-round grouping for deterministic context projection.

The compaction middleware uses this module for both historical (L2) and
current-turn (L3) projection.  Keeping the protocol check independent from
storage and token estimation makes a malformed tool transcript fail before a
model receives an invalid assistant/tool pairing.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

from yuxi.agents.middlewares.token_usage import ContextBudgetConfigurationError


class ToolProtocolError(ContextBudgetConfigurationError):
    """The persisted message sequence cannot be sent to a tool-calling model."""


_PROTECTED_TOOL_NAMES = frozenset({"ask_user_question"})


@dataclass(frozen=True)
class ApiRound:
    """One complete API response and the tool results that answer its calls."""

    start: int
    end: int
    tool_call_ids: tuple[str, ...]
    tool_result_indexes: tuple[int, ...]
    protected: bool


def _assistant_identity(message: AIMessage, index: int) -> str:
    """Use a per-message fallback because some compatible providers omit IDs."""
    identifier = getattr(message, "id", None)
    return identifier if isinstance(identifier, str) and identifier else f"missing-{index}"


def _call_id(tool_call: object, *, message_index: int) -> str:
    if not isinstance(tool_call, dict):
        raise ToolProtocolError(f"assistant message {message_index} contains a non-object tool call")
    identifier = tool_call.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ToolProtocolError(f"assistant message {message_index} contains a tool call without an ID")
    return identifier


def _round_is_protected(messages: list[AnyMessage], start: int, end: int) -> bool:
    """Do not rewrite confirmations or error branches whose details may drive recovery."""
    for message in messages[start:end]:
        if isinstance(message, ToolMessage) and getattr(message, "status", "success") == "error":
            return True
        if isinstance(message, AIMessage):
            for tool_call in message.tool_calls or []:
                if isinstance(tool_call, dict) and tool_call.get("name") in _PROTECTED_TOOL_NAMES:
                    return True
    return False


def _validate_round(messages: list[AnyMessage], start: int, end: int) -> ApiRound:
    """Validate one API round without attempting to repair persisted history.

    Claude Code can repair its own transient SDK stream just before an API call.
    Yuxi persists completed LangGraph messages instead, so silently repairing an
    unmatched result here would hide a checkpoint corruption and may send an
    invalid tool transcript to a local OpenAI-compatible provider.
    """
    call_ids: list[str] = []
    result_indexes: list[int] = []
    pending: set[str] = set()
    seen_calls: set[str] = set()
    seen_results: set[str] = set()

    for index in range(start, end):
        message = messages[index]
        if isinstance(message, AIMessage):
            for tool_call in message.tool_calls or []:
                call_id = _call_id(tool_call, message_index=index)
                if call_id in seen_calls:
                    raise ToolProtocolError(f"tool call ID {call_id!r} is duplicated in one API round")
                seen_calls.add(call_id)
                pending.add(call_id)
                call_ids.append(call_id)
            continue

        if not isinstance(message, ToolMessage):
            continue
        result_id = getattr(message, "tool_call_id", None)
        if not isinstance(result_id, str) or not result_id.strip():
            raise ToolProtocolError(f"tool result message {index} does not identify its tool call")
        if result_id in seen_results:
            raise ToolProtocolError(f"tool result for call {result_id!r} is duplicated")
        if result_id not in pending:
            raise ToolProtocolError(f"tool result for call {result_id!r} has no matching call in this API round")
        seen_results.add(result_id)
        pending.remove(result_id)
        result_indexes.append(index)

    if pending:
        missing = ", ".join(sorted(pending))
        raise ToolProtocolError(
            f"不存在可安全压缩的完整历史交互段：API round ending at message {end - 1} "
            f"has unresolved tool calls: {missing}"
        )

    return ApiRound(
        start=start,
        end=end,
        tool_call_ids=tuple(call_ids),
        tool_result_indexes=tuple(result_indexes),
        protected=_round_is_protected(messages, start, end),
    )


def group_messages_by_api_round(messages: list[AnyMessage]) -> list[ApiRound]:
    """Split messages at assistant-response boundaries and validate each round.

    This follows Claude Code's API-round boundary instead of grouping only by
    human turns.  Consequently an agent may safely project early completed
    rounds even when a single user request causes dozens of tool calls.
    """
    if not messages:
        return []

    boundaries = [0]
    previous_assistant_id: str | None = None
    for index, message in enumerate(messages):
        if not isinstance(message, AIMessage):
            continue
        assistant_id = _assistant_identity(message, index)
        if previous_assistant_id is not None and assistant_id != previous_assistant_id:
            boundaries.append(index)
        previous_assistant_id = assistant_id
    boundaries.append(len(messages))

    rounds = [_validate_round(messages, start, end) for start, end in zip(boundaries, boundaries[1:])]
    all_call_ids: set[str] = set()
    for round_ in rounds:
        duplicate_ids = all_call_ids.intersection(round_.tool_call_ids)
        if duplicate_ids:
            duplicate = next(iter(sorted(duplicate_ids)))
            raise ToolProtocolError(f"tool call ID {duplicate!r} is reused across API rounds")
        all_call_ids.update(round_.tool_call_ids)
    return rounds


def projectable_rounds(
    messages: list[AnyMessage],
    *,
    scope_end: int,
    protected_tail_rounds: int = 0,
) -> list[ApiRound]:
    """Return closed projectable rounds in chronological order.

    ``scope_end`` is exclusive.  L2 supplies the start of the latest human
    turn, while L3 supplies the end of the current turn and protects its newest
    closed rounds.  A round crossing that boundary is deliberately not used.
    """
    if not 0 <= scope_end <= len(messages):
        raise ValueError("scope_end must be within the message sequence")
    if protected_tail_rounds < 0:
        raise ValueError("protected_tail_rounds must not be negative")

    eligible = [
        round_
        for round_ in group_messages_by_api_round(messages)
        if round_.end <= scope_end and round_.tool_call_ids and not round_.protected
    ]
    if protected_tail_rounds:
        return eligible[:-protected_tail_rounds] if len(eligible) > protected_tail_rounds else []
    return eligible


def compactable_api_rounds(messages: list[AnyMessage], *, protected_tail_rounds: int = 2) -> list[ApiRound]:
    """Return whole rounds that L5 may replace with a semantic checkpoint.

    L5 is allowed to remove complete protocol units, unlike L2/L3.  The latest
    human input and the two most recent current-turn rounds remain real messages
    so the next model call has both the exact request and immediate execution
    context.  A round that straddles the latest human input is also protected.
    """
    if protected_tail_rounds < 0:
        raise ValueError("protected_tail_rounds must not be negative")

    rounds = group_messages_by_api_round(messages)
    latest_human_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if getattr(messages[index], "type", None) == "human"),
        None,
    )
    if latest_human_index is None:
        return []

    current_rounds = [round_ for round_ in rounds if round_.start >= latest_human_index]
    protected_current = set(current_rounds[-protected_tail_rounds:]) if protected_tail_rounds else set()
    candidates: list[ApiRound] = []
    for round_ in rounds:
        if round_.protected or round_ in protected_current:
            continue
        if round_.end <= latest_human_index or round_.start > latest_human_index:
            candidates.append(round_)
        elif round_.start < latest_human_index < round_.end:
            # Claude Code's assistant-ID grouping keeps a final assistant response
            # and the following HumanMessage together.  Its pre-Human prefix is a
            # complete prior API unit and may be checkpointed; the latest request
            # itself must never be split out of the real message sequence.
            candidates.append(
                ApiRound(
                    start=round_.start,
                    end=latest_human_index,
                    tool_call_ids=round_.tool_call_ids,
                    tool_result_indexes=tuple(
                        index for index in round_.tool_result_indexes if index < latest_human_index
                    ),
                    protected=False,
                )
            )
    return candidates
