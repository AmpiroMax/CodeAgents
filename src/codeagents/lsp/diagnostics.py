"""Normalized diagnostic shape returned to the agent.

LSP servers report 0-based line/character; the model expects 1-based
positions (matching `read_file` output and editor UIs). All conversions
happen here so callers don't have to remember.
"""

from __future__ import annotations

from typing import Any, TypedDict


_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}


class Diagnostic(TypedDict, total=False):
    severity: str
    line: int
    character: int
    end_line: int
    end_character: int
    message: str
    source: str
    code: str


def from_lsp(raw: dict[str, Any]) -> Diagnostic:
    """Convert a raw LSP `Diagnostic` dict into the normalized shape."""
    rng = raw.get("range") or {}
    start = rng.get("start") or {}
    end = rng.get("end") or {}
    sev_raw = raw.get("severity")
    severity = _SEVERITY.get(int(sev_raw), "info") if isinstance(sev_raw, int) else "info"
    code = raw.get("code")
    if isinstance(code, (int, float)):
        code_str = str(code)
    elif isinstance(code, str):
        code_str = code
    else:
        code_str = ""
    out: Diagnostic = {
        "severity": severity,
        "line": int(start.get("line", 0)) + 1,
        "character": int(start.get("character", 0)) + 1,
        "end_line": int(end.get("line", 0)) + 1,
        "end_character": int(end.get("character", 0)) + 1,
        "message": str(raw.get("message", "")),
        "source": str(raw.get("source", "")),
        "code": code_str,
    }
    return out


__all__ = ["Diagnostic", "from_lsp"]
