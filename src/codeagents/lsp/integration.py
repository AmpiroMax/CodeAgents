from __future__ import annotations

from typing import Any

from codeagents.config import PROJECT_ROOT
from codeagents.lsp.config import (
    LspServerEntry,
    load_lsp_servers,
    load_lsp_servers_for_project,
)
from codeagents.lsp.session import LspSession
from codeagents.permissions import Permission
from codeagents.tools import ParamSpec, ToolRegistry, ToolSpec
from codeagents.workspace import Workspace


def _lsp_query_handler(
    workspace: Workspace, servers: list[LspServerEntry], args: dict[str, Any]
) -> dict[str, Any]:
    action = args.get("action")
    if not isinstance(action, str) or not action:
        return {"error": "missing_action", "message": "Set action to document_symbols or workspace_symbol."}
    entry = servers[0]
    session = LspSession(entry.command, entry.args, cwd=workspace.root)
    try:
        session.initialize(workspace.root)
        if action == "document_symbols":
            path_raw = args.get("path")
            if not isinstance(path_raw, str) or not path_raw.strip():
                return {"error": "path_required"}
            path = workspace.resolve_inside(path_raw.strip())
            if not path.is_file():
                return {"error": "not_a_file", "path": str(path)}
            text = path.read_text(encoding="utf-8")
            session.did_open(path, text)
            symbols = session.document_symbols(path)
            return {"action": action, "path": str(path), "symbols": symbols}
        if action == "workspace_symbol":
            query = args.get("query")
            q = query if isinstance(query, str) else ""
            symbols = session.workspace_symbol(q)
            return {"action": action, "query": q, "symbols": symbols}
        return {"error": "unknown_action", "action": action}
    except Exception as exc:
        return {"error": "lsp_failed", "message": str(exc)}
    finally:
        session.shutdown()


def register_lsp_tools_optional(registry: ToolRegistry, workspace: Workspace) -> None:
    servers = [s for s in load_lsp_servers_for_project(PROJECT_ROOT) if s.enabled]
    if not servers:
        return
    registry.register(
        ToolSpec(
            name="lsp_query",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Query a configured language server (see config/lsp.toml). "
                "Actions: document_symbols (needs path), workspace_symbol (optional query). "
                "Example: lsp_query {\"action\":\"document_symbols\",\"path\":\"src/lib.rs\"}"
            ),
            params=(
                ParamSpec(
                    name="action",
                    type="string",
                    description="document_symbols | workspace_symbol",
                    required=True,
                    enum=("document_symbols", "workspace_symbol"),
                ),
                ParamSpec(
                    name="path",
                    type="string",
                    description="Workspace-relative file for document_symbols",
                    required=False,
                ),
                ParamSpec(
                    name="query",
                    type="string",
                    description="Filter string for workspace_symbol",
                    required=False,
                ),
            ),
        ),
        handler=lambda args: _lsp_query_handler(workspace, servers, args),
    )
