from __future__ import annotations

from pathlib import Path

import pytest

from codeagents.tools.native_code import (
    cat,
    cd,
    change_workspace,
    pwd,
    register_code_tools,
    write_file,
)
from codeagents.tools import ToolRegistry
from codeagents.core.workspace import Workspace, WorkspaceError


def _ws(path: Path) -> Workspace:
    return Workspace.from_path(path)


def test_cd_inside_workspace_keeps_full_access(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.txt").write_text("hi", encoding="utf-8")
    ws = _ws(tmp_path)

    result = cd(ws, {"path": "sub"})
    assert result["inside_workspace"] is True
    assert result["read_only"] is False
    assert ws.cwd == sub.resolve()

    # relative reads now resolve against cwd
    assert cat(ws, {"path": "a.txt"})["content"] == "hi"
    # writes still allowed inside workspace
    res = write_file(ws, {"path": "b.txt", "content": "x"})
    assert "error" not in res
    assert (sub / "b.txt").exists()


def test_cd_outside_workspace_enters_read_only(tmp_path: Path) -> None:
    inside = tmp_path / "ws"
    inside.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "note.txt").write_text("hello", encoding="utf-8")

    ws = _ws(inside)
    result = cd(ws, {"path": str(outside)})
    assert result["inside_workspace"] is False
    assert result["read_only"] is True
    assert ws.read_only is True

    # reads outside the workspace work in read-only mode
    assert cat(ws, {"path": "note.txt"})["content"] == "hello"

    # writes refuse because resolve_inside still enforces the boundary
    with pytest.raises(WorkspaceError):
        write_file(ws, {"path": "evil.txt", "content": "no"})
    assert not (outside / "evil.txt").exists()


def test_change_workspace_switches_root_and_creates_skeleton(tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    old_root.mkdir()
    new_root = tmp_path / "new_ws"

    ws = _ws(old_root)
    triggered: list[Path] = []
    ws.on_root_change.append(lambda w: triggered.append(w.root))

    res = change_workspace(ws, {"path": str(new_root)})
    assert "error" not in res
    assert ws.root == new_root.resolve()
    assert ws.cwd == new_root.resolve()
    assert (new_root / ".codeagents").is_dir()
    assert triggered and triggered[-1] == new_root.resolve()

    info = pwd(ws, {})
    assert info["workspace_root"] == str(new_root.resolve())
    assert info["read_only"] is False


def test_cd_to_missing_path_returns_error(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    res = cd(ws, {"path": "nope/never"})
    assert "error" in res


def test_resolve_for_read_allows_outside_paths(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    other = tmp_path.parent  # outside root
    resolved = ws.resolve_for_read(other)
    assert resolved == other.resolve()


def test_web_search_registered_as_read_only(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    registry = ToolRegistry()
    register_code_tools(registry, ws)
    from codeagents.core.permissions import Permission

    assert registry.get("web_search").permission == Permission.READ_ONLY
    assert registry.get("docs_search").permission == Permission.READ_ONLY
