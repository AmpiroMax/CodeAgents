"""Workspace navigation tools (``cd`` and ``change_workspace``).

These mutate :class:`codeagents.core.workspace.Workspace` state and are
the only path the model has to either:

* move the working directory inside the current workspace (``cd``), or
* switch the trust boundary entirely to a new root
  (``change_workspace`` — re-prompts for approvals).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codeagents.core.workspace import Workspace, WorkspaceError


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required string argument: {key}")
    return value


def cd(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    raw = _require_str(args, "path")
    target = Path(raw).expanduser()
    try:
        new_cwd = workspace.change_cwd(target)
    except WorkspaceError as exc:
        return {"error": str(exc)}
    inside = workspace.is_inside_root(new_cwd)
    return {
        "cwd": str(new_cwd),
        "workspace_root": str(workspace.root),
        "inside_workspace": inside,
        "read_only": not inside,
        "notice": (
            "Inside workspace — full permissions in effect."
            if inside
            else "Outside workspace — read-only mode. Use change_workspace to switch trust boundary."
        ),
    }


def change_workspace(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    raw = _require_str(args, "path")
    target = Path(raw).expanduser()
    try:
        new_root = workspace.change_root(target)
    except WorkspaceError as exc:
        return {"error": str(exc)}
    return {
        "workspace_root": str(new_root),
        "cwd": str(workspace.cwd),
        "notice": (
            "Workspace switched. Approvals/permissions are scoped to the new root; "
            "you may need to re-grant write/network access."
        ),
    }


__all__ = ["cd", "change_workspace"]
