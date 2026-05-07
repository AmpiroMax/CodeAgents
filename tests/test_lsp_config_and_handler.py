"""Config loader tests for the LSP layer.

The legacy single-shot ``lsp_query`` tool was removed in the honest
refactor; only the config parser is left to test here. Manager and tool
behaviour are covered by ``test_lsp_manager.py`` and ``test_lsp_tools.py``.
"""

from __future__ import annotations

from pathlib import Path

from codeagents.lsp.config import load_lsp_servers, load_lsp_servers_for_project


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


def test_load_lsp_servers_for_project_merges(tmp_path: Path) -> None:
    """``registry/lsp.toml`` overrides ``config/lsp.toml`` by entry name."""
    (tmp_path / "config").mkdir()
    (tmp_path / "registry").mkdir()
    (tmp_path / "config" / "lsp.toml").write_text(
        '[servers.shared]\nenabled = false\ncommand = "old"\nargs = []\n',
        encoding="utf-8",
    )
    (tmp_path / "registry" / "lsp.toml").write_text(
        '[servers.shared]\nenabled = true\ncommand = "new"\nargs = ["--stdio"]\n',
        encoding="utf-8",
    )
    rows = load_lsp_servers_for_project(tmp_path)
    by_name = {r.name: r for r in rows}
    assert by_name["shared"].command == "new"
    assert by_name["shared"].enabled is True
