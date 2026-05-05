from __future__ import annotations

from codeagents.schemas import (
    ChatMeta,
    FunctionParameter,
    function_parameters_from_json_schema,
    merge_chat_meta,
)


def test_merge_chat_meta_empty() -> None:
    m = merge_chat_meta(None)
    assert m.mode == "agent"
    assert m.task == "general"


def test_merge_chat_meta_overrides_and_extra() -> None:
    m = merge_chat_meta({"mode": "ask", "task": "code", "custom": 1})
    assert m.mode == "ask"
    assert m.task == "code"
    dumped = m.model_dump(mode="json")
    assert dumped.get("custom") == 1


def test_chat_meta_lsp_folders_roundtrip() -> None:
    raw = ChatMeta(
        mode="plan",
        lsp_workspace_folders=["/a", "/b"],
        terminal_sessions=["pty1"],
    ).model_dump(mode="json")
    m2 = ChatMeta.model_validate(raw)
    assert m2.lsp_workspace_folders == ["/a", "/b"]
    assert m2.terminal_sessions == ["pty1"]


def test_function_parameters_from_json_schema_builds_properties() -> None:
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Rel path"},
            "limit": {"type": "integer"},
        },
        "required": ["path"],
    }
    params = function_parameters_from_json_schema(schema)
    by_name = {p.name: p for p in params}
    assert set(by_name) == {"path", "limit"}
    assert by_name["path"].required is True
    assert by_name["limit"].required is False
    assert by_name["path"].schema_.get("type") == "string"


def test_function_parameters_empty_schema() -> None:
    assert function_parameters_from_json_schema({}) == []
    assert function_parameters_from_json_schema({"properties": "bad"}) == []
