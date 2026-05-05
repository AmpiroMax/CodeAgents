from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LspServerEntry:
    name: str
    enabled: bool
    command: str
    args: list[str]


def load_lsp_servers(path: Path) -> list[LspServerEntry]:
    if not path.exists():
        return []
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    out: list[LspServerEntry] = []
    for name, value in raw.get("servers", {}).items():
        if not isinstance(value, dict):
            continue
        if not value.get("command"):
            continue
        out.append(
            LspServerEntry(
                name=str(name),
                enabled=bool(value.get("enabled", False)),
                command=str(value["command"]),
                args=list(value.get("args") or []),
            )
        )
    return out
