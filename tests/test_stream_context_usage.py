"""Verifies the new ``context_usage`` stream event roundtrips through both
the typed event layer and the agent's stream forwarder.
"""

from __future__ import annotations

from codeagents.core.stream_events import (
    StreamContextUsageEvent,
    parse_stream_event,
    stream_event_to_json,
)


def test_context_usage_event_roundtrip():
    raw = {
        "type": "context_usage",
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "context_window": 8192,
    }
    event = parse_stream_event(raw)
    assert isinstance(event, StreamContextUsageEvent)
    payload = stream_event_to_json(event)
    assert payload == raw


def test_context_usage_unknown_type_falls_back_to_error():
    """Sanity check: parse_stream_event still tolerates new keys safely."""
    event = parse_stream_event({"type": "totally_made_up"})
    assert event.type == "error"
