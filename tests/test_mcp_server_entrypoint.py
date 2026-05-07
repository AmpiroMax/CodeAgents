from __future__ import annotations

import codeagents.surfaces.mcp.server as mcp_server


def test_mcp_server_main_is_callable() -> None:
    assert callable(mcp_server.main)
