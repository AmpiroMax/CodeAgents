"""Umbrella ``code_context`` tool: LSP precision + RAG breadth in one call.

When the agent is about to change something it usually wants:

* the **definition** of the target (LSP),
* its **references** so it doesn't break callers (LSP),
* a **hover** snippet for the type/docstring (LSP),
* current **diagnostics** for the file (LSP),
* semantically related code from elsewhere — **rag_neighbors** (gemma),
* the closest test files — **nearest_tests** (gemma + path heuristic).

This tool stitches all of that into one compact JSON payload. Each
field is hard-capped (refs ≤ 10, rag_neighbors ≤ ``k``, snippets ≤ 30
lines) to keep the prompt budget under control.

``target`` accepts three shapes:

* ``"path:line:col"`` — point in code (preferred for changes).
* ``"path"`` — whole-file overview (definition/refs are skipped).
* bare symbol name — used for ``workspace/symbol`` lookup.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from codeagents.lsp import LspManager
from codeagents.tools._registry import ToolRegistry
from codeagents.tools.lsp import (
    lsp_definition,
    lsp_diagnostics,
    lsp_hover,
    lsp_references,
    lsp_workspace_symbol,
)
from codeagents.workspace import Workspace


_TARGET_POSITION_RE = re.compile(r"^(.+?):(\d+)(?::(\d+))?$")
_MAX_SNIPPET_LINES = 30


def _trim_snippet(text: str, max_lines: int = _MAX_SNIPPET_LINES) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"


def _parse_target(workspace: Workspace, target: str) -> dict[str, Any]:
    """Classify ``target`` into ``{kind, path?, line?, character?, symbol?}``."""
    target = target.strip()
    if not target:
        return {"kind": "invalid", "error": "empty_target"}
    match = _TARGET_POSITION_RE.match(target)
    if match:
        path = match.group(1)
        line = int(match.group(2))
        char = int(match.group(3)) if match.group(3) else 1
        try:
            workspace.resolve_inside(path)
        except Exception:
            return {"kind": "symbol", "symbol": target}
        return {"kind": "point", "path": path, "line": line, "character": char}
    if "/" in target or target.endswith((".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".go", ".md")):
        try:
            workspace.resolve_inside(target)
        except Exception:
            return {"kind": "symbol", "symbol": target}
        return {"kind": "file", "path": target}
    return {"kind": "symbol", "symbol": target}


def _safe(out: dict[str, Any], key: str) -> Any:
    """Pull ``key`` from a tool result if it didn't error out."""
    if not isinstance(out, dict) or "error" in out:
        return None
    return out.get(key)


def _rag_neighbors(workspace: Workspace, query: str, k: int) -> list[dict[str, Any]]:
    try:
        from codeagents.tools.rag import search_code
    except Exception:
        return []
    try:
        result = search_code(workspace, {"query": query, "k": k})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in (result.get("results") or [])[:k]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "file": item.get("path", ""),
                "score": item.get("score"),
                "kind": item.get("kind"),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "snippet": _trim_snippet(str(item.get("preview") or "")),
            }
        )
    return out


def _nearest_tests(workspace: Workspace, query: str, k: int) -> list[dict[str, Any]]:
    try:
        from codeagents.tools.rag import search_code
    except Exception:
        return []
    try:
        # Pull a wider net then filter by path; the indexer doesn't support
        # path-prefix filtering directly, so this is the cheapest option.
        result = search_code(workspace, {"query": query, "k": max(k * 4, 12)})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if "test" not in path.lower():
            continue
        out.append(
            {
                "file": path,
                "score": item.get("score"),
                "start_line": item.get("start_line"),
                "snippet": _trim_snippet(str(item.get("preview") or "")),
            }
        )
        if len(out) >= k:
            break
    return out


def code_context(
    workspace: Workspace, lsp: LspManager, args: dict[str, Any]
) -> dict[str, Any]:
    target_raw = args.get("target")
    if not isinstance(target_raw, str):
        return {"error": "target_required", "message": "Pass a string target."}
    include_rag = args.get("include_rag")
    include_rag = True if include_rag is None else bool(include_rag)
    include_tests = args.get("include_tests")
    include_tests = True if include_tests is None else bool(include_tests)
    try:
        k = max(1, min(int(args.get("k", 5)), 20))
    except (TypeError, ValueError):
        k = 5

    parsed = _parse_target(workspace, target_raw)
    payload: dict[str, Any] = {"target": parsed}

    if parsed["kind"] == "invalid":
        return {**payload, "error": parsed.get("error", "invalid_target")}

    if parsed["kind"] == "point":
        path = parsed["path"]
        line = parsed["line"]
        char = parsed["character"]
        # Definition.
        defn = lsp_definition(workspace, lsp, {"path": path, "line": line, "character": char})
        locs = _safe(defn, "locations") or []
        if locs:
            first = locs[0]
            payload["definition"] = {
                "file": first.get("file"),
                "line": first.get("line"),
                "snippet": _trim_snippet(str(first.get("snippet") or "")),
            }
        # References (capped at 10).
        refs_out = lsp_references(
            workspace, lsp, {"path": path, "line": line, "character": char, "limit": 10}
        )
        refs = _safe(refs_out, "references") or []
        payload["references"] = [
            {"file": r.get("file"), "line": r.get("line"), "snippet": r.get("snippet")}
            for r in refs[:10]
        ]
        # Hover.
        hov = lsp_hover(workspace, lsp, {"path": path, "line": line, "character": char})
        hover_text = _safe(hov, "hover")
        if hover_text:
            payload["hover"] = _trim_snippet(str(hover_text))
        # Diagnostics for the whole file.
        diag = lsp_diagnostics(workspace, lsp, {"path": path})
        diags = _safe(diag, "diagnostics")
        if diags is not None:
            payload["diagnostics"] = diags

    elif parsed["kind"] == "file":
        path = parsed["path"]
        diag = lsp_diagnostics(workspace, lsp, {"path": path})
        diags = _safe(diag, "diagnostics")
        if diags is not None:
            payload["diagnostics"] = diags

    elif parsed["kind"] == "symbol":
        sym_out = lsp_workspace_symbol(
            workspace, lsp, {"query": parsed["symbol"], "limit": 10}
        )
        symbols = _safe(sym_out, "symbols") or []
        payload["symbols"] = symbols[:10]

    # RAG breadth — works for all target kinds.
    rag_query = parsed.get("symbol") or parsed.get("path") or target_raw
    if include_rag and rag_query:
        payload["rag_neighbors"] = _rag_neighbors(workspace, str(rag_query), k)
    if include_tests and rag_query:
        payload["nearest_tests"] = _nearest_tests(workspace, str(rag_query), k)

    return payload


def register_code_context_tool(
    registry: ToolRegistry, workspace: Workspace, lsp: LspManager
) -> None:
    from codeagents.permissions import Permission
    from codeagents.tools._registry import ToolSpec

    registry.register(
        ToolSpec(name="code_context", kind="native", permission=Permission.READ_ONLY, description=""),
        handler=lambda args: code_context(workspace, lsp, args),
    )


__all__ = ["code_context", "register_code_context_tool"]
