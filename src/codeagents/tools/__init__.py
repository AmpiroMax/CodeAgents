"""Tool registry + native tool implementations.

This is the Stage-2 home of what used to be ``codeagents.tools`` (a flat
module) plus ``codeagents.tools_native`` (a sibling package). The flat
module is gone, but ``from codeagents.tools import ToolRegistry`` keeps
working because we re-export the registry primitives here.

The package layout (will grow in later stages):

* :mod:`codeagents.tools._registry` — ``ToolSpec``/``ParamSpec``/``ToolRegistry``.
* :mod:`codeagents.tools._native_specs` — single source of truth for native
  tool descriptions and parameter schemas.
* :mod:`codeagents.tools.native_code` — handlers for the bulk of read/write/
  shell/web tools (will be split into ``filesystem.py`` / ``shell.py`` /
  ``web/`` etc. in subsequent passes).
* :mod:`codeagents.tools.research`, ``kg``, ``pdf``, ``rag`` — feature tools.

Top-level :func:`register_all` registers everything that depends only on a
``Workspace`` (i.e. all native tools). MCP tools are registered separately
from :mod:`codeagents.surfaces.mcp` / :mod:`codeagents.mcp.bridge` because
they need to talk to subprocesses.
"""

from __future__ import annotations

from codeagents.tools._native_specs import NATIVE_TOOL_SPECS
from codeagents.tools._registry import (
    ParamSpec,
    ToolHandler,
    ToolRegistry,
    ToolSpec,
    register_native_specs,
)


def register_all_native_tools(registry: ToolRegistry, workspace, *, lsp=None) -> None:
    """Attach handlers for every native tool the registry knows about.

    Registers:
    * code/filesystem/shell/web/plan/recall/search_code (``native_code.py``);
    * PDF reader (``pdf.py``);
    * research tools (``research.py``);
    * LSP lookup tools and ``code_context`` (``lsp.py`` / ``code_context.py``);
    * KG tools (``kg.py``) when its optional native deps import cleanly.

    ``lsp`` is an optional :class:`codeagents.lsp.LspManager`. When provided,
    ``edit_file``/``write_file`` results gain a ``diagnostics`` field and the
    LSP lookup tools are wired to the manager.
    """
    from codeagents.tools.native_code import register_code_tools
    from codeagents.tools.pdf import register_pdf_tools
    from codeagents.tools.research import register_research_tools

    register_code_tools(registry, workspace, lsp=lsp)
    try:
        register_pdf_tools(registry, workspace)
    except Exception:  # pragma: no cover - optional dep failure path
        pass
    register_research_tools(registry, workspace)
    if lsp is not None:
        try:
            from codeagents.tools.lsp import register_lsp_lookup_tools

            register_lsp_lookup_tools(registry, workspace, lsp)
        except Exception:  # pragma: no cover - optional dep failure path
            pass
        try:
            from codeagents.tools.code_context import register_code_context_tool

            register_code_context_tool(registry, workspace, lsp)
        except Exception:  # pragma: no cover
            pass
    try:
        from codeagents.tools.kg import register_kg_tools

        register_kg_tools(registry, workspace)
    except Exception:  # pragma: no cover - optional dep failure path
        pass


def build_native_registry(workspace, *, lsp=None) -> ToolRegistry:
    """Construct a :class:`ToolRegistry`, seed native specs, attach handlers.

    Replaces the old ``load_tool_registry(...) + register_code_tools(...)``
    pair. No TOML is read — descriptions and parameter schemas come from
    :mod:`codeagents.tools._native_specs`.
    """
    registry = ToolRegistry()
    register_native_specs(registry, NATIVE_TOOL_SPECS)
    register_all_native_tools(registry, workspace, lsp=lsp)
    return registry


__all__ = [
    "NATIVE_TOOL_SPECS",
    "ParamSpec",
    "ToolHandler",
    "ToolRegistry",
    "ToolSpec",
    "build_native_registry",
    "register_all_native_tools",
    "register_native_specs",
]
