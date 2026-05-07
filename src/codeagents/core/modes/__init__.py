"""Agent mode registry.

Each mode (``agent``, ``plan``, ``ask``, ``research``) has:

* a tool whitelist (which tools the model sees);
* a permission filter (which permission levels are allowed);
* a UI accent color (consumed by the GUI via ``GET /modes``);
* a system prompt resolver (default + per-model overrides) backed by
  ``registry/prompts/modes/<mode>.json``.

Single source of truth for everything mode-related. Adding a new mode
means appending one entry to :data:`MODE_REGISTRY` and creating a
``registry/prompts/modes/<name>.json`` file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from codeagents.core.permissions import Permission

# Tool whitelists used to live in ``codeagents.mode_tools``. The data is
# now owned here; that module re-exports for back-compat.
_MODE_TOOLS: dict[str, tuple[str, ...]] = {
    "ask": (
        "read_file",
        "read_pdf",
        "ls",
        "pwd",
        "grep",
        "glob_files",
        "search_code",
        "recall_chat",
        "web_search",
        "web_fetch",
        "lsp_definition",
        "lsp_references",
        "lsp_hover",
        "lsp_workspace_symbol",
        "lsp_diagnostics",
        "code_context",
    ),
    "plan": (
        "read_file",
        "read_pdf",
        "ls",
        "pwd",
        "grep",
        "glob_files",
        "search_code",
        "recall_chat",
        "web_search",
        "web_fetch",
        "create_plan",
        "patch_plan",
        "mark_step",
        "list_plans",
        "lsp_definition",
        "lsp_references",
        "lsp_hover",
        "lsp_workspace_symbol",
        "lsp_diagnostics",
        "code_context",
    ),
    "research": (
        "read_file",
        "read_pdf",
        "ls",
        "grep",
        "glob_files",
        "search_code",
        "recall_chat",
        "web_search",
        "web_fetch",
        # Clarify questions are now asked in plain chat, so the
        # clarify_research / submit_clarify_answers tools are intentionally
        # NOT exposed in this whitelist.
        "plan_research",
        "expand_query",
        "extract_facts",
        "draft_section",
        "assemble_report",
        "kg_query",
        "kg_resolve_conflicts",
    ),
    # ``agent`` is open-ended: filter_for_mode returns the input unchanged
    # so newly added tools are exposed without touching this file.
    "agent": (),
}


_MODE_PERMISSIONS: dict[str, frozenset[Permission] | None] = {
    "ask": frozenset({Permission.READ_ONLY}),
    "plan": frozenset({Permission.READ_ONLY, Permission.PROPOSE}),
    "research": frozenset({Permission.READ_ONLY, Permission.PROPOSE}),
    "agent": frozenset(
        {
            Permission.READ_ONLY,
            Permission.WORKSPACE_WRITE,
            Permission.NETWORK,
            Permission.SHELL_SAFE,
            Permission.PROPOSE,
        }
    ),
    # "agent": None,  # all enabled tools
}


# UI accent colors, sourced from ``gui/src/lib/modeColors.ts``. Kept here
# so the GUI can fetch them via ``GET /modes`` instead of hard-coding.
_MODE_COLORS: dict[str, str] = {
    "agent": "#4ea1ff",
    "plan": "#ff9b3d",
    "ask": "#66d685",
    "research": "#ff5fb0",
}


@dataclass(frozen=True)
class ModeSpec:
    """Static description of an agent mode."""

    name: str
    tool_whitelist: tuple[str, ...]
    """Tool names visible to the model. Empty tuple means ``no filtering``
    (all enabled tools are exposed). Anything outside the whitelist is
    dropped from the OpenAI ``tools`` payload but stays *registered* so
    direct ``agent.call_tool(...)`` keeps working."""

    allowed_permissions: frozenset[Permission] | None
    """``None`` means ``no permission filter``."""

    ui_color: str
    """Hex string for the GUI accent. Consumed by the React app."""

    @property
    def is_open(self) -> bool:
        """True for modes without a tool whitelist (e.g. ``agent``)."""
        return not self.tool_whitelist


MODE_REGISTRY: dict[str, ModeSpec] = {
    name: ModeSpec(
        name=name,
        tool_whitelist=_MODE_TOOLS.get(name, ()),
        allowed_permissions=_MODE_PERMISSIONS.get(name),
        ui_color=_MODE_COLORS.get(name, "#888888"),
    )
    for name in ("agent", "plan", "ask", "research")
}


def whitelist_for(mode: str) -> set[str] | None:
    """Return the visible tool names for ``mode`` (``None`` if unrestricted)."""
    spec = MODE_REGISTRY.get((mode or "").lower())
    if spec is None or spec.is_open:
        return None
    return set(spec.tool_whitelist)


def filter_for_mode(mode: str, specs: Iterable[Any]) -> list[Any]:
    """Drop specs whose ``.name`` is not in the whitelist for ``mode``."""
    wl = whitelist_for(mode)
    out = list(specs)
    if wl is None:
        return out
    return [s for s in out if getattr(s, "name", None) in wl]


def allowed_permissions_for(mode: str) -> set[Permission] | None:
    """Return the permission set allowed in ``mode`` (``None`` = unrestricted)."""
    spec = MODE_REGISTRY.get((mode or "").lower())
    if spec is None or spec.allowed_permissions is None:
        return None
    return set(spec.allowed_permissions)


def list_modes() -> list[dict[str, Any]]:
    """Serialisable mode descriptors for the HTTP ``GET /modes`` endpoint."""
    out: list[dict[str, Any]] = []
    for spec in MODE_REGISTRY.values():
        out.append(
            {
                "name": spec.name,
                "tool_whitelist": list(spec.tool_whitelist),
                "allowed_permissions": (
                    sorted(p.value for p in spec.allowed_permissions)
                    if spec.allowed_permissions is not None
                    else None
                ),
                "ui_color": spec.ui_color,
            }
        )
    return out


from codeagents.core.modes.prompts import resolve_prompt  # noqa: E402

# Public alias for legacy ``codeagents.mode_tools.MODE_TOOLS`` callers.
MODE_TOOLS = _MODE_TOOLS

__all__ = [
    "MODE_REGISTRY",
    "MODE_TOOLS",
    "ModeSpec",
    "allowed_permissions_for",
    "filter_for_mode",
    "list_modes",
    "resolve_prompt",
    "whitelist_for",
]
