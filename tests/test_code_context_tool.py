"""Tests for the umbrella ``code_context`` tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codeagents.tools import code_context as cc_module
from codeagents.tools.code_context import code_context
from codeagents.workspace import Workspace


class _FakeSession:
    def definition(self, path, line, character):
        return [
            {
                "uri": (path.parent / "target.py").as_uri(),
                "range": {
                    "start": {"line": 1, "character": 4},
                    "end": {"line": 1, "character": 12},
                },
            }
        ]

    def references(self, path, line, character, *, include_declaration):
        return [
            {
                "uri": path.as_uri(),
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 4}},
            }
        ] * 25  # exceed cap of 10

    def hover(self, path, line, character):
        return {"contents": "Foo: int"}

    def workspace_symbol(self, query):
        return [
            {
                "name": "AgentCore",
                "kind": 5,
                "location": {
                    "uri": (Path.cwd() / "x.py").as_uri(),
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 9},
                    },
                },
            }
        ]


class _FakeHandle:
    def __init__(self) -> None:
        self.session = _FakeSession()


class _FakeManager:
    def for_path(self, path):
        return _FakeHandle()

    def for_query(self):
        return _FakeHandle()

    def ensure_open(self, handle, path):
        pass

    def diagnostics(self, path):
        return [
            {
                "severity": "warning",
                "line": 1,
                "character": 1,
                "end_line": 1,
                "end_character": 2,
                "message": "stub",
                "source": "fake",
                "code": "W",
            }
        ]


@pytest.fixture()
def workspace(tmp_path: Path) -> Workspace:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "target.py").write_text("# t\ndef target():\n    return 0\n", encoding="utf-8")
    return Workspace.from_path(tmp_path)


@pytest.fixture(autouse=True)
def stub_rag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``search_code`` with a deterministic stub."""

    def fake_search(workspace, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "results": [
                {
                    "path": "src/main.py",
                    "kind": "function",
                    "score": 0.9,
                    "start_line": 1,
                    "end_line": 2,
                    "name": "hello",
                    "preview": "def hello(): pass",
                },
                {
                    "path": "tests/test_main.py",
                    "kind": "function",
                    "score": 0.6,
                    "start_line": 1,
                    "end_line": 2,
                    "name": "test_hello",
                    "preview": "def test_hello(): pass",
                },
            ]
        }

    import codeagents.tools.rag as rag_mod

    monkeypatch.setattr(rag_mod, "search_code", fake_search)


def test_point_target_assembles_all_sections(workspace: Workspace) -> None:
    out = code_context(workspace, _FakeManager(), {"target": "src/main.py:1:5"})  # type: ignore[arg-type]
    assert out["target"]["kind"] == "point"
    assert "definition" in out and out["definition"]["file"] == "src/target.py"
    assert "references" in out
    assert len(out["references"]) <= 10
    assert "hover" in out
    assert out["diagnostics"][0]["severity"] == "warning"
    assert out["rag_neighbors"]
    assert out["nearest_tests"]
    assert out["nearest_tests"][0]["file"].startswith("tests/")


def test_file_target_only_diagnostics_and_rag(workspace: Workspace) -> None:
    out = code_context(workspace, _FakeManager(), {"target": "src/main.py", "include_tests": False})  # type: ignore[arg-type]
    assert out["target"]["kind"] == "file"
    assert "definition" not in out
    assert "references" not in out
    assert "diagnostics" in out
    assert "nearest_tests" not in out
    assert out["rag_neighbors"]


def test_symbol_target_uses_workspace_symbol(workspace: Workspace) -> None:
    out = code_context(workspace, _FakeManager(), {"target": "AgentCore"})  # type: ignore[arg-type]
    assert out["target"]["kind"] == "symbol"
    assert out["symbols"][0]["name"] == "AgentCore"


def test_invalid_target(workspace: Workspace) -> None:
    out = code_context(workspace, _FakeManager(), {"target": ""})  # type: ignore[arg-type]
    assert out.get("error") == "empty_target"


def test_missing_target(workspace: Workspace) -> None:
    out = code_context(workspace, _FakeManager(), {})  # type: ignore[arg-type]
    assert out.get("error") == "target_required"
