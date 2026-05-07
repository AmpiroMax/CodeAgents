"""MCP surface (Stage 1 re-export shim).

The MCP server entry point lives at :mod:`codeagents.mcp_server`; the
client adapter lives at :mod:`codeagents.mcp`. Stage 4+ will move them
under this package.
"""

from codeagents import mcp_server  # noqa: F401  (re-export for new path)
from codeagents.mcp import adapter, bridge  # noqa: F401

__all__ = ["adapter", "bridge", "mcp_server"]
