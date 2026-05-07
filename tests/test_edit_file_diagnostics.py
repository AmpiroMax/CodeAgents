"""Verify ``edit_file``/``write_file`` attach LSP diagnostics when available."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codeagents.permissions import Permission
from codeagents.tools import ToolRegistry
from codeagents.tools._native_specs import NATIVE_TOOL_SPECS
from codeagents.tools._registry import register_native_specs
from codeagents.tools.native_code import register_code_tools
from codeagents.workspace import Workspace


class _StubLsp:
    def __init__(self, payload: list[dict[str, Any]] | Exception | None) -> None:
        self.payload = payload
        self.calls: list[Path] = []

    def diagnostics(self, path: Path):
        self.calls.append(path)
        if isinstance(self.payload, Exception):
            raise self.payload
        return list(self.payload or [])


def _build_registry(workspace: Workspace, lsp) -> ToolRegistry:
    reg = ToolRegistry()
    register_native_specs(reg, NATIVE_TOOL_SPECS)
    register_code_tools(reg, workspace, lsp=lsp)
    return reg


def test_write_file_attaches_diagnostics(tmp_path: Path) -> None:
    ws = Workspace.from_path(tmp_path)
    lsp = _StubLsp(
        [
            {
                "severity": "error",
                "line": 1,
                "character": 1,
                "end_line": 1,
                "end_character": 5,
                "message": "boom",
                "source": "stub",
                "code": "E1",
            }
        ]
    )
    reg = _build_registry(ws, lsp)
    out = reg.handler("write_file")({"path": "x.py", "content": "x = 1\n"})
    assert "diagnostics" in out
    assert out["diagnostics"][0]["message"] == "boom"
    assert lsp.calls and lsp.calls[0].name == "x.py"


def test_edit_file_no_diagnostics_when_none(tmp_path: Path) -> None:
    ws = Workspace.from_path(tmp_path)
    (tmp_path / "x.py").write_text("a = 1\n", encoding="utf-8")
    lsp = _StubLsp([])  # no diagnostics
    reg = _build_registry(ws, lsp)
    out = reg.handler("edit_file")(
        {"path": "x.py", "old_text": "a = 1", "new_text": "a = 2"}
    )
    assert "diagnostics" not in out
    assert lsp.calls  # we still asked


def test_diagnostics_silent_when_lsp_missing(tmp_path: Path) -> None:
    ws = Workspace.from_path(tmp_path)
    reg = _build_registry(ws, lsp=None)
    out = reg.handler("write_file")({"path": "x.py", "content": "x = 1\n"})
    assert "diagnostics" not in out


def test_diagnostics_silent_on_lsp_error(tmp_path: Path) -> None:
    ws = Workspace.from_path(tmp_path)
    lsp = _StubLsp(RuntimeError("server died"))
    reg = _build_registry(ws, lsp)
    out = reg.handler("write_file")({"path": "x.py", "content": "x = 1\n"})
    assert "diagnostics" not in out
    # Result still indicates success (write_file produced its normal payload).
    assert "error" not in out


def test_no_diagnostics_for_failed_edit(tmp_path: Path) -> None:
    ws = Workspace.from_path(tmp_path)
    (tmp_path / "x.py").write_text("a = 1\n", encoding="utf-8")
    lsp = _StubLsp([{"severity": "error", "line": 1, "character": 1, "message": "x", "source": "", "code": "", "end_line": 1, "end_character": 1}])
    reg = _build_registry(ws, lsp)
    out = reg.handler("edit_file")(
        {"path": "x.py", "old_text": "no-such-string", "new_text": "z"}
    )
    assert "error" in out
    assert "diagnostics" not in out
    assert lsp.calls == []
