from __future__ import annotations

import json

from codeagents.stream_events import (
    StreamDoneEvent,
    StreamModelInfoEvent,
    StreamToolResultEvent,
    parse_stream_event,
    stream_event_to_json,
)


def test_stream_event_roundtrip_delta():
    raw = {"type": "delta", "content": "hello"}
    ev = parse_stream_event(raw)
    assert ev.type == "delta"
    back = stream_event_to_json(ev)
    assert back["type"] == "delta"
    assert back["content"] == "hello"


def test_stream_event_done_json_shape():
    ev = StreamDoneEvent(model="m1", stop_reason="completed")
    d = stream_event_to_json(ev)
    assert d == {"type": "done", "model": "m1", "stop_reason": "completed"}
    assert json.loads(json.dumps(d)) == d


def test_ndjson_line_matches_tui_contract():
    """Golden shape for Rust AgentNdjsonEvent (tag = type)."""
    events = [
        stream_event_to_json(StreamModelInfoEvent(model="qwen3")),
        stream_event_to_json(StreamToolResultEvent(name="read_file", result="{}", tool_call_id="c1")),
    ]
    lines = [json.dumps(e, ensure_ascii=False) for e in events]
    assert '"type":"model_info"' in lines[0].replace(" ", "")
    assert "tool_result" in lines[1] and '"_id":"c1"' in lines[1].replace(" ", "")
