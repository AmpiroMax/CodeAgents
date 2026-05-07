"""Typed NDJSON stream events for the agent loop (Pydantic discriminated union)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class StreamModelInfoEvent(BaseModel):
    type: Literal["model_info"] = "model_info"
    model: str


class StreamDeltaEvent(BaseModel):
    type: Literal["delta"] = "delta"
    content: str = ""


class StreamThinkingEvent(BaseModel):
    type: Literal["thinking"] = "thinking"
    content: str = ""


class StreamToolCallStartEvent(BaseModel):
    type: Literal["tool_call_start"] = "tool_call_start"
    index: int
    name: str = ""


class StreamToolCallDeltaEvent(BaseModel):
    type: Literal["tool_call_delta"] = "tool_call_delta"
    index: int
    delta: str = ""
    name: str = ""


class StreamToolCallEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: str
    tool_call_id: str = Field(
        default="",
        serialization_alias="_id",
        validation_alias=AliasChoices("_id", "tool_call_id"),
    )


class StreamToolResultEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["tool_result"] = "tool_result"
    name: str
    result: str
    tool_call_id: str = Field(
        default="",
        serialization_alias="_id",
        validation_alias=AliasChoices("_id", "tool_call_id"),
    )


class StreamToolPendingEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["tool_pending"] = "tool_pending"
    decision_id: str
    name: str
    arguments: str
    remember_supported: bool = False
    warning: str = ""
    tool_call_id: str = Field(
        default="",
        serialization_alias="_id",
        validation_alias=AliasChoices("_id", "tool_call_id"),
    )


class StreamNoticeEvent(BaseModel):
    type: Literal["notice"] = "notice"
    level: Literal["info", "warn", "error"] = "info"
    message: str


class StreamErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


class StreamDoneEvent(BaseModel):
    type: Literal["done"] = "done"
    model: str = ""
    stop_reason: Literal["completed", "empty_turns", "max_turns"] | str = "completed"


class StreamTerminalOutputEvent(BaseModel):
    """Reserved for future PTY streaming; additive API."""

    type: Literal["terminal_output"] = "terminal_output"
    session_id: str = ""
    chunk: str = ""


class StreamResearchProgressEvent(BaseModel):
    """Side-channel update from deep-research tools (Phase 2.B.3-4).

    Emitted whenever a research tool finishes successfully, so the GUI's
    ResearchViewer can render live progress (clarify questions ready, plan
    drafted, section drafted, report assembled, ...) without parsing tool
    JSON results in two places.
    """

    type: Literal["research_progress"] = "research_progress"
    chat_id: str = ""
    report_id: str = ""
    stage: str = ""  # clarify_ready | plan_ready | section_drafted | assembled | ...
    section_idx: int | None = None
    detail: dict[str, Any] = {}


class StreamContextUsageEvent(BaseModel):
    """Reports prompt/total token usage and the model context window.

    Emitted at most once per agent turn after the underlying LLM stream finishes
    (when the runtime has reported a usage block). Purely informational; older
    clients (TUI etc.) ignore unknown event types via ``parse_stream_event``.
    """

    type: Literal["context_usage"] = "context_usage"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    context_window: int = 0


AgentStreamEvent = (
    StreamModelInfoEvent
    | StreamDeltaEvent
    | StreamThinkingEvent
    | StreamToolCallStartEvent
    | StreamToolCallDeltaEvent
    | StreamToolCallEvent
    | StreamToolResultEvent
    | StreamToolPendingEvent
    | StreamNoticeEvent
    | StreamErrorEvent
    | StreamDoneEvent
    | StreamTerminalOutputEvent
    | StreamContextUsageEvent
    | StreamResearchProgressEvent
)


def stream_event_to_json(event: AgentStreamEvent) -> dict[str, Any]:
    return event.model_dump(mode="json", exclude_none=True, by_alias=True)


def parse_stream_event(data: dict[str, Any]) -> AgentStreamEvent:
    """Parse a stream event dict (e.g. from NDJSON) into a typed model."""
    t = data.get("type")
    mapping: dict[str, type[BaseModel]] = {
        "model_info": StreamModelInfoEvent,
        "delta": StreamDeltaEvent,
        "thinking": StreamThinkingEvent,
        "tool_call_start": StreamToolCallStartEvent,
        "tool_call_delta": StreamToolCallDeltaEvent,
        "tool_call": StreamToolCallEvent,
        "tool_result": StreamToolResultEvent,
        "tool_pending": StreamToolPendingEvent,
        "notice": StreamNoticeEvent,
        "error": StreamErrorEvent,
        "done": StreamDoneEvent,
        "terminal_output": StreamTerminalOutputEvent,
        "context_usage": StreamContextUsageEvent,
        "research_progress": StreamResearchProgressEvent,
    }
    cls = mapping.get(str(t))
    if cls is None:
        return StreamErrorEvent(message=f"unknown stream event type: {t!r}")
    return cls.model_validate(data)  # type: ignore[return-value]


def runtime_dict_to_stream_event(row: dict[str, Any]) -> AgentStreamEvent:
    """Map runtime.py chat_stream dict events to typed stream events."""
    et = row.get("type")
    if et == "thinking":
        return StreamThinkingEvent(content=str(row.get("content", "")))
    if et == "delta":
        return StreamDeltaEvent(content=str(row.get("content", "")))
    if et == "tool_call_start":
        return StreamToolCallStartEvent(
            index=int(row.get("index", 0)),
            name=str(row.get("name", "")),
        )
    if et == "tool_call_delta":
        return StreamToolCallDeltaEvent(
            index=int(row.get("index", 0)),
            delta=str(row.get("delta", "")),
            name=str(row.get("name", "")),
        )
    if et == "tool_call":
        return StreamToolCallEvent(
            name=str(row.get("name", "")),
            arguments=str(row.get("arguments", "")),
            tool_call_id=str(row.get("_id", "")),
        )
    if et == "error":
        return StreamErrorEvent(message=str(row.get("message", "")))
    if et == "done":
        return StreamDoneEvent(model=str(row.get("model", "")))
    return StreamErrorEvent(message=f"unknown_runtime_event:{et}")
