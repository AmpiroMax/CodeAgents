from __future__ import annotations

from pathlib import Path

import pytest

from codeagents.lsp import integration as lsp_integration
from codeagents.lsp.config import load_lsp_servers
from codeagents.lsp.config import LspServerEntry
from codeagents.lsp.integration import _lsp_query_handler
from codeagents.permissions import Permission
from codeagents.tools import ToolRegistry, ToolSpec
from codeagents.workspace import Workspace


def test_load_lsp_servers_missing_file(tmp_path: Path) -> None:
    assert load_lsp_servers(tmp_path / "nope.toml") == []


def test_load_lsp_servers_parses_rows(tmp_path: Path) -> None:
    p = tmp_path / "lsp.toml"
    p.write_text(
        """
[servers.a]
enabled = true
command = "rust-analyzer"
args = []

[servers.b]
enabled = false
command = "pylsp"
args = ["-v"]
""",
        encoding="utf-8",
    )
    rows = load_lsp_servers(p)
    assert len(rows) == 2
    assert rows[0].name == "a"
    assert rows[0].enabled is True
    assert rows[1].enabled is False


def test_lsp_query_handler_missing_action(tmp_path: Path) -> None:
    ws = Workspace.from_path(tmp_path)
    entry = LspServerEntry(name="x", enabled=True, command="true", args=[])
    out = _lsp_query_handler(ws, [entry], {})
    assert out.get("error") == "missing_action"


def test_register_lsp_tools_optional_no_enabled_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "lsp.toml").write_text(
        '[servers.z]\nenabled = false\ncommand = "true"\nargs = []\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(lsp_integration, "PROJECT_ROOT", tmp_path)
    reg = ToolRegistry()
    reg.register(
        ToolSpec(name="dummy", kind="native", permission=Permission.READ_ONLY, description="d"),
        handler=lambda _a: {},
    )
    lsp_integration.register_lsp_tools_optional(reg, Workspace.from_path(tmp_path))
    names = {t.name for t in reg.list()}
    assert "lsp_query" not in names
    assert "dummy" in names
