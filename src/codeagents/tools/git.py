"""Git read-only tools (``git_diff``, ``git_status``).

Both are hidden from the model (``enabled=False`` in the registry); the
agent uses ``bash 'git ...'`` instead. Kept here so direct
``agent.call_tool("git_diff", {...})`` paths keep working.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from codeagents.core.workspace import Workspace


def _run(argv: list[str], *, cwd: Path, timeout: int = 60) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv, cwd=cwd, text=True, capture_output=True, timeout=timeout
        )
    except FileNotFoundError:
        return {
            "argv": argv,
            "exit_code": 127,
            "stdout": "",
            "stderr": f"Executable not found: {argv[0]}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "exit_code": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"Command timed out after {timeout}s",
        }
    return {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def git_diff(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    staged = bool(args.get("staged", False))
    argv = ["git", "diff", "--staged"] if staged else ["git", "diff"]
    return _run(argv, cwd=workspace.root)


def git_status(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    return _run(["git", "status", "--short", "--branch"], cwd=workspace.root)


__all__ = ["git_diff", "git_status"]
