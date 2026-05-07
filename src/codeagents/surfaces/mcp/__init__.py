"""MCP surface: server entry point + client adapter/bridge.

* :mod:`codeagents.surfaces.mcp.server` — stdio MCP server exposing
  the agent's native tool registry.
* :mod:`codeagents.surfaces.mcp.adapter` — :class:`MCPServerSpec` and
  the loader that reads ``registry/mcp.toml``.
* :mod:`codeagents.surfaces.mcp.bridge` — registers MCP tools into the
  local :class:`ToolRegistry`.
"""

from codeagents.surfaces.mcp.adapter import MCPServerSpec, load_mcp_specs
from codeagents.surfaces.mcp.bridge import register_mcp_tools

__all__ = ["MCPServerSpec", "load_mcp_specs", "register_mcp_tools"]
