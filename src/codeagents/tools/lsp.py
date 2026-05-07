"""Narrow LSP lookup tools: definition / references / hover / symbol / diagnostics.

Handlers are intentionally small wrappers around :class:`LspManager`.
Descriptions and parameter schemas live in
:mod:`codeagents.tools._native_specs` (single source of truth).

All five tools degrade gracefully: when no server is configured for the
file's language they return ``{"error": "no_lsp_server", ...}`` instead
of raising, so the agent can fall back to ``read_file`` / ``search_code``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from codeagents.lsp import LspManager
from codeagents.lsp.diagnostics import from_lsp
from codeagents.tools._registry import ToolRegistry
from codeagents.core.workspace import Workspace


# ── helpers ──────────────────────────────────────────────────────────


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_path(workspace: Workspace, raw: Any) -> Path | dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {"error": "path_required"}
    try:
        return workspace.resolve_inside(raw.strip())
    except Exception as exc:
        return {"error": "bad_path", "message": str(exc)}


def _to_zero_based(line: int, character: int) -> tuple[int, int]:
    """Inputs are 1-based for the model; LSP wants 0-based."""
    return max(0, line - 1), max(0, character - 1)


def _uri_to_relative(workspace: Workspace, uri: str) -> str:
    """Convert ``file://`` URI back to a workspace-relative path string."""
    if not uri:
        return ""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    abs_path = Path(unquote(parsed.path))
    try:
        return str(abs_path.relative_to(workspace.root))
    except ValueError:
        return str(abs_path)


def _snippet(path: Path, line_zero: int, span: int = 5) -> str:
    """Read up to ``span`` lines starting at ``line_zero`` (0-based)."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, UnicodeDecodeError):
        return ""
    start = max(0, line_zero)
    end = min(len(lines), start + span)
    return "".join(lines[start:end]).rstrip("\n")


def _one_line(path: Path, line_zero: int) -> str:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for idx, line in enumerate(fh):
                if idx == line_zero:
                    return line.rstrip("\n")
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def _normalize_locations(raw: Any) -> list[dict[str, Any]]:
    """LSP returns ``Location | Location[] | LocationLink[] | null``."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        rng = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
        if not uri or not isinstance(rng, dict):
            continue
        start = rng.get("start") or {}
        out.append(
            {
                "uri": uri,
                "line": int(start.get("line", 0)),
                "character": int(start.get("character", 0)),
            }
        )
    return out


# ── handlers ─────────────────────────────────────────────────────────


def _no_server(path: Path) -> dict[str, Any]:
    return {
        "error": "no_lsp_server",
        "message": (
            f"No LSP server is configured for {path.suffix}. "
            "Add a server to registry/lsp.toml or fall back to read_file/search_code."
        ),
    }


def lsp_definition(
    workspace: Workspace, lsp: LspManager, args: dict[str, Any]
) -> dict[str, Any]:
    path_or_err = _resolve_path(workspace, args.get("path"))
    if isinstance(path_or_err, dict):
        return path_or_err
    path = path_or_err
    line = _coerce_int(args.get("line"), 0)
    if line < 1:
        return {"error": "line_required", "message": "line must be a 1-based integer >= 1"}
    character = _coerce_int(args.get("character"), 1)
    handle = lsp.for_path(path)
    if handle is None:
        return _no_server(path)
    try:
        lsp.ensure_open(handle, path)
        line_z, char_z = _to_zero_based(line, character)
        raw = handle.session.definition(path, line_z, char_z)
    except Exception as exc:
        return {"error": "lsp_failed", "message": str(exc)}
    locs = _normalize_locations(raw)
    out: list[dict[str, Any]] = []
    for loc in locs[:10]:
        rel = _uri_to_relative(workspace, loc["uri"])
        try:
            target_path = workspace.resolve_inside(rel) if not Path(rel).is_absolute() else Path(rel)
        except Exception:
            target_path = Path(rel)
        out.append(
            {
                "file": rel,
                "line": loc["line"] + 1,
                "character": loc["character"] + 1,
                "snippet": _snippet(target_path, loc["line"], span=5),
            }
        )
    return {"locations": out}


def lsp_references(
    workspace: Workspace, lsp: LspManager, args: dict[str, Any]
) -> dict[str, Any]:
    path_or_err = _resolve_path(workspace, args.get("path"))
    if isinstance(path_or_err, dict):
        return path_or_err
    path = path_or_err
    line = _coerce_int(args.get("line"), 0)
    if line < 1:
        return {"error": "line_required"}
    character = _coerce_int(args.get("character"), 1)
    include_decl = bool(args.get("include_declaration"))
    limit = max(1, min(_coerce_int(args.get("limit"), 50), 200))
    handle = lsp.for_path(path)
    if handle is None:
        return _no_server(path)
    try:
        lsp.ensure_open(handle, path)
        line_z, char_z = _to_zero_based(line, character)
        raw = handle.session.references(
            path, line_z, char_z, include_declaration=include_decl
        )
    except Exception as exc:
        return {"error": "lsp_failed", "message": str(exc)}
    locs = _normalize_locations(raw)[:limit]
    out: list[dict[str, Any]] = []
    for loc in locs:
        rel = _uri_to_relative(workspace, loc["uri"])
        try:
            target_path = workspace.resolve_inside(rel) if not Path(rel).is_absolute() else Path(rel)
        except Exception:
            target_path = Path(rel)
        out.append(
            {
                "file": rel,
                "line": loc["line"] + 1,
                "character": loc["character"] + 1,
                "snippet": _one_line(target_path, loc["line"]),
            }
        )
    return {"references": out}


def _hover_text(raw: Any) -> str:
    """LSP hover.contents is ``MarkedString | MarkedString[] | MarkupContent``."""
    if raw is None:
        return ""
    contents = raw.get("contents") if isinstance(raw, dict) else None
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value", ""))
    if isinstance(contents, list):
        parts: list[str] = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("value", "")))
        return "\n\n".join(p for p in parts if p)
    return ""


def lsp_hover(
    workspace: Workspace, lsp: LspManager, args: dict[str, Any]
) -> dict[str, Any]:
    path_or_err = _resolve_path(workspace, args.get("path"))
    if isinstance(path_or_err, dict):
        return path_or_err
    path = path_or_err
    line = _coerce_int(args.get("line"), 0)
    if line < 1:
        return {"error": "line_required"}
    character = _coerce_int(args.get("character"), 1)
    handle = lsp.for_path(path)
    if handle is None:
        return _no_server(path)
    try:
        lsp.ensure_open(handle, path)
        line_z, char_z = _to_zero_based(line, character)
        raw = handle.session.hover(path, line_z, char_z)
    except Exception as exc:
        return {"error": "lsp_failed", "message": str(exc)}
    return {"hover": _hover_text(raw)}


_LSP_SYMBOL_KIND = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor",
    10: "enum", 11: "interface", 12: "function", 13: "variable",
    14: "constant", 15: "string", 16: "number", 17: "boolean",
    18: "array", 19: "object", 20: "key", 21: "null",
    22: "enum_member", 23: "struct", 24: "event", 25: "operator",
    26: "type_parameter",
}


def lsp_workspace_symbol(
    workspace: Workspace, lsp: LspManager, args: dict[str, Any]
) -> dict[str, Any]:
    query = args.get("query")
    if not isinstance(query, str):
        return {"error": "query_required"}
    limit = max(1, min(_coerce_int(args.get("limit"), 50), 200))
    handle = lsp.for_query()
    if handle is None:
        return {
            "error": "no_lsp_server",
            "message": "No LSP server is enabled. See registry/lsp.toml.",
        }
    try:
        raw = handle.session.workspace_symbol(query)
    except Exception as exc:
        return {"error": "lsp_failed", "message": str(exc)}
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            location = item.get("location") or {}
            uri = location.get("uri") or ""
            rng = location.get("range") or {}
            start = rng.get("start") or {}
            out.append(
                {
                    "name": str(item.get("name", "")),
                    "kind": _LSP_SYMBOL_KIND.get(int(item.get("kind", 0)), "unknown"),
                    "file": _uri_to_relative(workspace, uri),
                    "line": int(start.get("line", 0)) + 1,
                    "character": int(start.get("character", 0)) + 1,
                    "container": str(item.get("containerName") or ""),
                }
            )
    return {"symbols": out}


def lsp_diagnostics(
    workspace: Workspace, lsp: LspManager, args: dict[str, Any]
) -> dict[str, Any]:
    path_or_err = _resolve_path(workspace, args.get("path"))
    if isinstance(path_or_err, dict):
        return path_or_err
    path = path_or_err
    handle = lsp.for_path(path)
    if handle is None:
        return _no_server(path)
    try:
        diags = lsp.diagnostics(path)
    except Exception as exc:
        return {"error": "lsp_failed", "message": str(exc)}
    return {"diagnostics": diags}


def register_lsp_lookup_tools(
    registry: ToolRegistry, workspace: Workspace, lsp: LspManager
) -> None:
    """Wire the five narrow LSP tools to ``lsp``.

    ToolSpecs already exist in ``_native_specs.NATIVE_TOOL_SPECS`` and have
    been registered by ``register_native_specs``; we only attach handlers
    here. We re-register with the existing spec (the registry's merge
    keeps the description/params untouched) and pass the new handler.
    """
    from codeagents.core.permissions import Permission
    from codeagents.tools._registry import ToolSpec

    pairs = [
        ("lsp_definition", lambda a: lsp_definition(workspace, lsp, a)),
        ("lsp_references", lambda a: lsp_references(workspace, lsp, a)),
        ("lsp_hover", lambda a: lsp_hover(workspace, lsp, a)),
        ("lsp_workspace_symbol", lambda a: lsp_workspace_symbol(workspace, lsp, a)),
        ("lsp_diagnostics", lambda a: lsp_diagnostics(workspace, lsp, a)),
    ]
    for name, handler in pairs:
        registry.register(
            ToolSpec(name=name, kind="native", permission=Permission.READ_ONLY, description=""),
            handler=handler,
        )


__all__ = [
    "lsp_definition",
    "lsp_diagnostics",
    "lsp_hover",
    "lsp_references",
    "lsp_workspace_symbol",
    "register_lsp_lookup_tools",
]
