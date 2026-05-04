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


def load_mcp_specs(path: Path) -> list[MCPServerSpec]:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    specs: list[MCPServerSpec] = []
    for name, value in raw.get("mcp", {}).items():
        specs.append(
            MCPServerSpec(
                name=name,
                enabled=bool(value.get("enabled", False)),
                command=value.get("command", ""),
                args=list(value.get("args", [])),
                permission=Permission(value.get("permission", Permission.READ_ONLY)),
                description=value.get("description", ""),
            )
        )
    return specs
