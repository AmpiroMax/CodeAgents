"""Language Server Protocol client used by the agent.

* :class:`LspManager` — long-lived per-language pool used by the agent.
* :class:`LspSession` — low-level JSON-RPC client (used by the manager).
* :class:`Diagnostic` — normalized diagnostic shape returned to the model.
* :class:`LspServerEntry` / :func:`load_lsp_servers_for_project` — config
  loader for ``registry/lsp.toml``.
"""

from codeagents.lsp.config import (
    LspServerEntry,
    load_lsp_servers,
    load_lsp_servers_for_project,
)
from codeagents.lsp.diagnostics import Diagnostic
from codeagents.lsp.manager import LspManager

__all__ = [
    "Diagnostic",
    "LspManager",
    "LspServerEntry",
    "load_lsp_servers",
    "load_lsp_servers_for_project",
]
