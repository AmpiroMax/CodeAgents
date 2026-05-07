from __future__ import annotations

import json
import time
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Permission(StrEnum):
    READ_ONLY = "read_only"
    PROPOSE = "propose"
    WORKSPACE_WRITE = "workspace_write"
    SHELL_SAFE = "shell_safe"
    SHELL_DANGEROUS = "shell_dangerous"
    NETWORK = "network"


class PermissionPolicy:
    def __init__(
        self,
        *,
        allow_workspace_write: bool = True,
        allow_network: bool = True,
        allow_shell_dangerous: bool = False,
    ) -> None:
        self.allow_workspace_write = allow_workspace_write
        self.allow_network = allow_network
        self.allow_shell_dangerous = allow_shell_dangerous

    def requires_confirmation(self, permission: Permission) -> bool:
        if permission in {Permission.READ_ONLY, Permission.PROPOSE, Permission.SHELL_SAFE}:
            return False
        if permission == Permission.WORKSPACE_WRITE:
            return not self.allow_workspace_write
        if permission == Permission.NETWORK:
            return not self.allow_network
        if permission == Permission.SHELL_DANGEROUS:
            return not self.allow_shell_dangerous
        return True


def load_permission_policy(path: Path) -> PermissionPolicy:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    permissions = raw.get("permissions", {})
    return PermissionPolicy(
        allow_workspace_write=bool(permissions.get("allow_workspace_write", True)),
        allow_network=bool(permissions.get("allow_network", True)),
        allow_shell_dangerous=bool(permissions.get("allow_shell_dangerous", False)),
    )


@dataclass(frozen=True)
class ApprovalRecord:
    tool_name: str
    permission: str
    workspace: str
    granted_at: float


class WorkspaceApprovalStore:
    """Workspace-scoped persistent approvals for confirmation-gated tools."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.path = self.workspace_root / ".codeagents" / "approvals.json"

    def is_tool_approved(self, tool_name: str, permission: Permission) -> bool:
        raw = self._load()
        entry = raw.get("tools", {}).get(tool_name)
        if not isinstance(entry, dict):
            return False
        return (
            entry.get("permission") == permission.value
            and entry.get("workspace") == str(self.workspace_root)
        )

    def approve_tool(self, tool_name: str, permission: Permission) -> ApprovalRecord:
        raw = self._load()
        tools = raw.setdefault("tools", {})
        record = ApprovalRecord(
            tool_name=tool_name,
            permission=permission.value,
            workspace=str(self.workspace_root),
            granted_at=time.time(),
        )
        tools[tool_name] = {
            "permission": record.permission,
            "workspace": record.workspace,
            "granted_at": record.granted_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def is_shell_command_approved(self, command_name: str, permission: Permission) -> bool:
        raw = self._load()
        entry = raw.get("shell_commands", {}).get(command_name)
        if not isinstance(entry, dict):
            return False
        return (
            entry.get("permission") == permission.value
            and entry.get("workspace") == str(self.workspace_root)
        )

    def approve_shell_command(self, command_name: str, permission: Permission) -> ApprovalRecord:
        raw = self._load()
        commands = raw.setdefault("shell_commands", {})
        record = ApprovalRecord(
            tool_name=f"shell:{command_name}",
            permission=permission.value,
            workspace=str(self.workspace_root),
            granted_at=time.time(),
        )
        commands[command_name] = {
            "permission": record.permission,
            "workspace": record.workspace,
            "granted_at": record.granted_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "tools": {}, "shell_commands": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"version": 1, "tools": {}, "shell_commands": {}}
        if not isinstance(raw, dict):
            return {"version": 1, "tools": {}, "shell_commands": {}}
        if not isinstance(raw.get("tools"), dict):
            raw["tools"] = {}
        if not isinstance(raw.get("shell_commands"), dict):
            raw["shell_commands"] = {}
        raw.setdefault("version", 1)
        return raw
