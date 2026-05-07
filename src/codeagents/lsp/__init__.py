"""Language Server Protocol client helpers.

* :class:`LspManager` — long-lived per-language pool used by the agent.
* :func:`register_lsp_tools_optional` — back-compat single-shot ``lsp_query``.
"""

from codeagents.lsp.config import (
    LspServerEntry,
    load_lsp_servers,
    load_lsp_servers_for_project,
)
from codeagents.lsp.diagnostics import Diagnostic
from codeagents.lsp.integration import register_lsp_tools_optional
from codeagents.lsp.manager import LspManager

__all__ = [
    "Diagnostic",
    "LspManager",
    "LspServerEntry",
    "load_lsp_servers",
    "load_lsp_servers_for_project",
    "register_lsp_tools_optional",
]
