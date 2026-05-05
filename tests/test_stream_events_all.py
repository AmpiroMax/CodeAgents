"""Roundtrip and runtime mapping for every stream event type."""

from __future__ import annotations

import pytest

from codeagents.stream_events import (
    StreamDeltaEvent,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamModelInfoEvent,
    StreamNoticeEvent,
    StreamTerminalOutputEvent,
    StreamThinkingEvent,
    StreamToolCallDeltaEvent,
    StreamToolCallEvent,
    StreamToolCallStartEvent,
    StreamToolPendingEvent,
    StreamToolResultEvent,
    parse_stream_event,
    runtime_dict_to_stream_event,
    stream_event_to_json,
)

_CASES: list[tuple[object, dict]] = [
    (StreamModelInfoEvent(model="m"), {"type": "model_info", "model": "m"}),
    (StreamDeltaEvent(content="x"), {"type": "delta", "content": "x"}),
    (StreamThinkingEvent(content="t"), {"type": "thinking", "content": "t"}),
    (
        StreamToolCallStartEvent(index=1, name="n"),
        {"type": "tool_call_start", "index": 1, "name": "n"},
    ),
    (
        StreamToolCallDeltaEvent(index=2, delta="d", name="n"),
        {"type": "tool_call_delta", "index": 2, "delta": "d", "name": "n"},
    ),
    (
        StreamToolCallEvent(name="read_file", arguments="{}", tool_call_id="id1"),
        {"type": "tool_call", "name": "read_file", "arguments": "{}", "_id": "id1"},
    ),
    (
        StreamToolResultEvent(name="read_file", result="{}", tool_call_id="id1"),
        {"type": "tool_result", "name": "read_file", "result": "{}", "_id": "id1"},
    ),
    (
        StreamToolPendingEvent(
            decision_id="d1",
            name="shell",
            arguments="{}",
            remember_supported=False,
            warning="w",
            tool_call_id="id1",
        ),
        {
            "type": "tool_pending",
            "decision_id": "d1",
            "name": "shell",
            "arguments": "{}",
            "remember_supported": False,
            "warning": "w",
            "_id": "id1",
        },
    ),
    (StreamNoticeEvent(level="warn", message="m"), {"type": "notice", "level": "warn", "message": "m"}),
    (StreamErrorEvent(message="e"), {"type": "error", "message": "e"}),
    (
        StreamDoneEvent(model="x", stop_reason="completed"),
        {"type": "done", "model": "x", "stop_reason": "completed"},
    ),
    (
        StreamTerminalOutputEvent(session_id="s1", chunk="out"),
        {"type": "terminal_output", "session_id": "s1", "chunk": "out"},
    ),
]


@pytest.mark.parametrize("model,expected_subset", _CASES, ids=[type(m).__name__ for m, _ in _CASES])
def test_stream_event_dump_matches_type(model: object, expected_subset: dict) -> None:
    d = stream_event_to_json(model)  # type: ignore[arg-type]
    for k, v in expected_subset.items():
        assert d[k] == v


@pytest.mark.parametrize("model,_", _CASES, ids=[type(m).__name__ for m, _ in _CASES])
def test_parse_stream_event_roundtrip(model: object, _: dict) -> None:
    d = stream_event_to_json(model)  # type: ignore[arg-type]
    back = parse_stream_event(d)
    assert stream_event_to_json(back) == d


def test_parse_unknown_type_returns_error_event() -> None:
    ev = parse_stream_event({"type": "nope"})
    assert isinstance(ev, StreamErrorEvent)
    assert "unknown" in ev.message.lower()


@pytest.mark.parametrize(
    "row,expected_type",
    [
        ({"type": "delta", "content": "a"}, StreamDeltaEvent),
        ({"type": "thinking", "content": "b"}, StreamThinkingEvent),
        ({"type": "tool_call_start", "index": 3, "name": "x"}, StreamToolCallStartEvent),
        ({"type": "tool_call_delta", "index": 0, "delta": "{}", "name": "t"}, StreamToolCallDeltaEvent),
        ({"type": "tool_call", "name": "n", "arguments": "{}"}, StreamToolCallEvent),
        ({"type": "error", "message": "m"}, StreamErrorEvent),
        ({"type": "done"}, StreamDoneEvent),
        ({"type": "weird"}, StreamErrorEvent),
    ],
)
def test_runtime_dict_mapping(row: dict, expected_type: type) -> None:
    ev = runtime_dict_to_stream_event(row)
    assert isinstance(ev, expected_type)
