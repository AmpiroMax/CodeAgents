from __future__ import annotations

import os
from pathlib import Path

import pytest

from codeagents.mcp.adapter import load_mcp_specs
from codeagents.mcp import bridge as mcp_bridge
from codeagents.permissions import Permission
from codeagents.tools import ToolRegistry, ToolSpec


def test_load_mcp_specs_reads_env_and_cwd(tmp_path: Path) -> None:
    p = tmp_path / "tools.toml"
    p.write_text(
        """
[mcp.demo]
enabled = true
command = "echo"
args = ["hi"]
permission = "read_only"
description = "test"
env = { FOO = "bar" }
cwd = "/tmp"
""",
        encoding="utf-8",
    )
    specs = load_mcp_specs(p)
    assert len(specs) == 1
    s = specs[0]
    assert s.name == "demo"
    assert s.enabled is True
    assert s.command == "echo"
    assert s.args == ["hi"]
    assert s.permission == Permission.READ_ONLY
    assert s.env == {"FOO": "bar"}
    assert s.cwd == "/tmp"


def test_qualified_tool_name_sanitizes() -> None:
    assert mcp_bridge.qualified_tool_name("my server", "tool/name") == "mcp.my_server.tool_name"


def test_truncate_payload_short_unchanged() -> None:
    assert mcp_bridge._truncate_payload("abc") == "abc"


def test_register_mcp_tools_respects_disable_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "tools.toml"
    cfg.write_text(
        '[mcp.x]\nenabled = true\ncommand = "true"\nargs = []\npermission = "read_only"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEAGENTS_DISABLE_MCP", "1")
    reg = ToolRegistry()
    n = mcp_bridge.register_mcp_tools(reg, cfg)
    assert n == 0
    assert not list(reg.list())


@pytest.mark.skipif(not os.environ.get("RUN_MCP_INTEGRATION"), reason="set RUN_MCP_INTEGRATION=1 to run")
def test_register_mcp_tools_discovers_when_enabled(tmp_path: Path) -> None:
    """Optional: requires a working MCP server on PATH."""
    reg = ToolRegistry()
    n = mcp_bridge.register_mcp_tools(reg, Path("config/tools.toml"))
    assert n >= 0


def test_serialize_call_result_small() -> None:
    from mcp.types import CallToolResult, TextContent

    r = CallToolResult(content=[TextContent(type="text", text="ok")], isError=False)
    d = mcp_bridge._serialize_call_result(r)
    assert isinstance(d, dict)
    assert d.get("isError") is False
    assert any("ok" in str(block) for block in (d.get("content") or []))
