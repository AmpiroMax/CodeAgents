from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from codeagents.permissions import Permission


@dataclass(frozen=True)
class MCPServerSpec:
    name: str
    enabled: bool
    command: str
    args: list[str]
    permission: Permission
    description: str
    env: dict[str, str]
    cwd: str | None


def load_mcp_specs(path: Path) -> list[MCPServerSpec]:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    specs: list[MCPServerSpec] = []
    for name, value in raw.get("mcp", {}).items():
        env_raw = value.get("env") or {}
        if not isinstance(env_raw, dict):
            env_raw = {}
        env = {str(k): str(v) for k, v in env_raw.items()}
        cwd = value.get("cwd")
        cwd_s = str(cwd) if cwd else None
        specs.append(
            MCPServerSpec(
                name=name,
                enabled=bool(value.get("enabled", False)),
                command=value.get("command", ""),
                args=list(value.get("args", [])),
                permission=Permission(value.get("permission", Permission.READ_ONLY)),
                description=value.get("description", ""),
                env=env,
                cwd=cwd_s,
            )
        )
    return specs
