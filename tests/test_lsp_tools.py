"""Tests for the five narrow LSP lookup tools.

These tests use a fake :class:`LspManager` (no real subprocess) so they
focus purely on the shape of tool results: 1-based positions, snippet
extraction, URI→relative path conversion, graceful no-server handling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codeagents.tools.lsp import (
    lsp_definition,
    lsp_diagnostics,
    lsp_hover,
    lsp_references,
    lsp_workspace_symbol,
)
from codeagents.core.workspace import Workspace


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def definition(self, path: Path, line: int, character: int) -> Any:
        self.calls.append(("definition", (path, line, character)))
        return [
            {
                "uri": (path.parent / "target.py").as_uri(),
                "range": {
                    "start": {"line": 1, "character": 4},
                    "end": {"line": 1, "character": 12},
                },
            }
        ]

    def references(self, path: Path, line: int, character: int, *, include_declaration: bool) -> Any:
        self.calls.append(("references", (path, line, character, include_declaration)))
        return [
            {
                "uri": path.as_uri(),
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 4},
                },
            },
            {
                "uri": path.as_uri(),
                "range": {
                    "start": {"line": 2, "character": 0},
                    "end": {"line": 2, "character": 4},
                },
            },
        ]

    def hover(self, path: Path, line: int, character: int) -> Any:
        return {"contents": {"kind": "markdown", "value": "**Foo**\n\nint"}}

    def workspace_symbol(self, query: str) -> Any:
        return [
            {
                "name": "AgentCore",
                "kind": 5,
                "location": {
                    "uri": (Path.cwd() / "src" / "x.py").as_uri(),
                    "range": {
                        "start": {"line": 9, "character": 0},
                        "end": {"line": 9, "character": 9},
                    },
                },
                "containerName": "agent",
            }
        ]


class _FakeHandle:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session


class _FakeManager:
    """Minimal stand-in for :class:`codeagents.lsp.LspManager`."""

    def __init__(self, *, has_server: bool = True) -> None:
        self.has_server = has_server
        self.session = _FakeSession()
        self.opened: list[Path] = []
        self.diagnostics_calls: list[Path] = []

    def for_path(self, path: Path):
        return _FakeHandle(self.session) if self.has_server else None

    def for_query(self):
        return _FakeHandle(self.session) if self.has_server else None

    def ensure_open(self, handle, path: Path) -> None:
        self.opened.append(path)

    def diagnostics(self, path: Path):
        self.diagnostics_calls.append(path)
        return [
            {
                "severity": "warning",
                "line": 3,
                "character": 1,
                "end_line": 3,
                "end_character": 5,
                "message": "unused",
                "source": "fake",
                "code": "W001",
            }
        ]


@pytest.fixture()
def workspace(tmp_path: Path) -> Workspace:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def hello():\n    return 'world'\nhello()\n", encoding="utf-8"
    )
    (tmp_path / "src" / "target.py").write_text(
        "# header\ndef target():\n    return 1\n", encoding="utf-8"
    )
    return Workspace.from_path(tmp_path)


def test_lsp_definition_returns_snippet(workspace: Workspace) -> None:
    mgr = _FakeManager()
    out = lsp_definition(workspace, mgr, {"path": "src/main.py", "line": 3, "character": 1})  # type: ignore[arg-type]
    assert "locations" in out
    assert len(out["locations"]) == 1
    loc = out["locations"][0]
    assert loc["file"] == "src/target.py"
    assert loc["line"] == 2  # 1 (0-based) + 1
    assert loc["character"] == 5
    assert "def target" in loc["snippet"]
    # Manager called with 0-based positions.
    method, payload = mgr.session.calls[-1]
    assert method == "definition"
    assert payload[1:] == (2, 0)


def test_lsp_references_one_line_snippets(workspace: Workspace) -> None:
    mgr = _FakeManager()
    out = lsp_references(  # type: ignore[arg-type]
        workspace, mgr, {"path": "src/main.py", "line": 1, "character": 5, "limit": 5}
    )
    assert "references" in out
    assert len(out["references"]) == 2
    assert out["references"][0]["snippet"] == "def hello():"
    assert out["references"][1]["line"] == 3


def test_lsp_hover_extracts_value(workspace: Workspace) -> None:
    out = lsp_hover(workspace, _FakeManager(), {"path": "src/main.py", "line": 1, "character": 5})  # type: ignore[arg-type]
    assert "Foo" in out["hover"]


def test_lsp_workspace_symbol(workspace: Workspace) -> None:
    out = lsp_workspace_symbol(workspace, _FakeManager(), {"query": "Agent", "limit": 5})  # type: ignore[arg-type]
    assert out["symbols"][0]["name"] == "AgentCore"
    assert out["symbols"][0]["kind"] == "class"
    assert out["symbols"][0]["line"] == 10  # 1-based


def test_lsp_diagnostics_passthrough(workspace: Workspace) -> None:
    out = lsp_diagnostics(workspace, _FakeManager(), {"path": "src/main.py"})  # type: ignore[arg-type]
    assert out["diagnostics"][0]["severity"] == "warning"


def test_no_server_graceful(workspace: Workspace) -> None:
    mgr = _FakeManager(has_server=False)
    for fn in (lsp_definition, lsp_references, lsp_hover, lsp_diagnostics):
        out = fn(workspace, mgr, {"path": "src/main.py", "line": 1, "character": 1})  # type: ignore[arg-type]
        assert out.get("error") == "no_lsp_server"
    out = lsp_workspace_symbol(workspace, mgr, {"query": "x"})  # type: ignore[arg-type]
    assert out.get("error") == "no_lsp_server"


def test_missing_path(workspace: Workspace) -> None:
    out = lsp_definition(workspace, _FakeManager(), {"line": 1, "character": 1})  # type: ignore[arg-type]
    assert out.get("error") == "path_required"


def test_missing_line(workspace: Workspace) -> None:
    out = lsp_definition(workspace, _FakeManager(), {"path": "src/main.py"})  # type: ignore[arg-type]
    assert out.get("error") == "line_required"
