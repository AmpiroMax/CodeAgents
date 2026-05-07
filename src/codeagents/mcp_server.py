"""CodeAgents MCP server (stdio): expose core workspace tools to external MCP clients."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from codeagents.tools import build_native_registry
from codeagents.workspace import Workspace


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("The 'mcp' package is required: pip install mcp", file=sys.stderr)
        raise SystemExit(1) from None

    root = Path(os.environ.get("CODEAGENTS_WORKSPACE", ".")).resolve()
    workspace = Workspace.from_path(root)
    registry = build_native_registry(workspace)

    mcp = FastMCP(
        "codeagents",
        instructions=(
            "CodeAgents workspace tools: read/search/edit within CODEAGENTS_WORKSPACE. "
            "Respect the server permission policy from registry/permissions.toml."
        ),
    )

    @mcp.tool()
    def ca_read_file(path: str, offset: int = 1, limit: int = 200) -> str:
        """Read a UTF-8 file under the workspace (numbered lines)."""
        out = registry.handler("read_file")(
            {"path": path, "offset": offset, "limit": limit}
        )
        return json.dumps(out, ensure_ascii=False)

    @mcp.tool()
    def ca_search(query: str, limit: int = 10) -> str:
        """Keyword / index search in the workspace."""
        out = registry.handler("search")({"query": query, "limit": limit})
        return json.dumps(out, ensure_ascii=False)

    @mcp.tool()
    def ca_list_directory(path: str = ".", max_results: int = 200) -> str:
        """List files in a workspace directory."""
        out = registry.handler("ls")({"path": path, "max_results": max_results})
        return json.dumps(out, ensure_ascii=False)

    @mcp.tool()
    def ca_run_tool(name: str, arguments_json: str = "{}") -> str:
        """Invoke any native CodeAgents tool by name with a JSON object string."""
        try:
            args = json.loads(arguments_json) if arguments_json.strip() else {}
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"invalid JSON: {exc}"})
        if not isinstance(args, dict):
            return json.dumps({"error": "arguments_json must decode to an object"})
        try:
            out = registry.handler(name)(args)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(out, ensure_ascii=False)

    @mcp.resource("workspace://root")
    def workspace_root() -> str:
        return str(workspace.root)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
