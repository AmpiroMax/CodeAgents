"""LSP server configuration loader.

Reads ``[servers.<name>]`` tables from ``registry/lsp.toml`` (preferred)
or the legacy ``config/lsp.toml``. Each entry now describes:

* ``command``/``args`` — how to start the server.
* ``languages`` — list of language ids the server should handle (matched
  against :func:`codeagents.lsp.session._suffix_lang`).
* ``root_markers`` — files that mark the project root for that language;
  the manager prefers the deepest ancestor of the workspace that
  contains one of these markers, falling back to the workspace root.
* ``idle_timeout_seconds`` — shutdown the subprocess after this many
  seconds without requests (0 disables the watcher).

The legacy single-field shape (``command`` only) keeps working through
:data:`_LEGACY_LANGUAGES` heuristics so existing ``config/lsp.toml``
files don't break on upgrade.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Heuristic mapping for legacy config files that only specify ``command``.
# Pyright/pylsp/jedi-language-server are all Python; rust-analyzer is Rust;
# typescript-language-server / tsserver / vscode-* speak TS/JS/JSX/TSX.
_LEGACY_LANGUAGES: dict[str, tuple[str, ...]] = {
    "pyright": ("python",),
    "pyright-langserver": ("python",),
    "pylsp": ("python",),
    "jedi-language-server": ("python",),
    "rust-analyzer": ("rust",),
    "typescript-language-server": (
        "typescript",
        "typescriptreact",
        "javascript",
        "javascriptreact",
    ),
}


@dataclass(frozen=True)
class LspServerEntry:
    name: str
    enabled: bool
    command: str
    args: list[str]
    languages: tuple[str, ...] = ()
    root_markers: tuple[str, ...] = ()
    idle_timeout_seconds: int = 600


def _legacy_languages_for(command: str) -> tuple[str, ...]:
    """Best-effort language guess for legacy ``config/lsp.toml`` rows."""
    base = Path(command).name
    return _LEGACY_LANGUAGES.get(base, ())


def load_lsp_servers(path: Path) -> list[LspServerEntry]:
    """Parse one TOML file into a list of :class:`LspServerEntry`.

    Returns an empty list if ``path`` does not exist (callers chain
    ``registry/lsp.toml`` and ``config/lsp.toml`` themselves).
    """
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
        languages_raw = value.get("languages") or []
        languages = tuple(str(x) for x in languages_raw if isinstance(x, str))
        if not languages:
            languages = _legacy_languages_for(str(value["command"]))
        markers_raw = value.get("root_markers") or []
        markers = tuple(str(x) for x in markers_raw if isinstance(x, str))
        try:
            idle = int(value.get("idle_timeout_seconds", 600))
        except (TypeError, ValueError):
            idle = 600
        out.append(
            LspServerEntry(
                name=str(name),
                enabled=bool(value.get("enabled", False)),
                command=str(value["command"]),
                args=list(value.get("args") or []),
                languages=languages,
                root_markers=markers,
                idle_timeout_seconds=idle,
            )
        )
    return out


def load_lsp_servers_for_project(project_root: Path) -> list[LspServerEntry]:
    """Merge ``registry/lsp.toml`` (preferred) with legacy ``config/lsp.toml``.

    Entries with the same ``name`` from registry win; legacy entries fill
    in only what registry hasn't defined.
    """
    registry_path = project_root / "registry" / "lsp.toml"
    legacy_path = project_root / "config" / "lsp.toml"
    by_name: dict[str, LspServerEntry] = {}
    for entry in load_lsp_servers(legacy_path):
        by_name[entry.name] = entry
    for entry in load_lsp_servers(registry_path):
        by_name[entry.name] = entry  # registry overrides
    return list(by_name.values())


def filter_enabled(entries: Iterable[LspServerEntry]) -> list[LspServerEntry]:
    return [e for e in entries if e.enabled and e.command.strip()]


__all__ = [
    "LspServerEntry",
    "filter_enabled",
    "load_lsp_servers",
    "load_lsp_servers_for_project",
]
