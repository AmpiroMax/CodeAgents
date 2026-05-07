"""Discover MCP servers from registry/mcp.toml and register proxied tools.

Reads ``[mcp.<server>]`` tables from the supplied config path (typically
``registry/mcp.toml``), connects to each enabled server over stdio, and
registers each remote tool as ``mcp.<server>.<tool>``. Set the env var
``CODEAGENTS_DISABLE_MCP=1`` to skip discovery entirely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from codeagents.mcp.adapter import MCPServerSpec, load_mcp_specs
from codeagents.tools import ToolRegistry, ToolSpec

logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_CHARS = 200_000


def _sanitize_segment(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)


def qualified_tool_name(server_name: str, tool_name: str) -> str:
    return f"mcp.{_sanitize_segment(server_name)}.{_sanitize_segment(tool_name)}"


def _truncate_payload(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[: MAX_TOOL_OUTPUT_CHARS - 80] + "\n... [truncated by CodeAgents MCP bridge]"


def _serialize_call_result(result: CallToolResult) -> dict[str, Any]:
    raw = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    if len(raw) <= MAX_TOOL_OUTPUT_CHARS:
        return result.model_dump(mode="json")  # type: ignore[return-value]
    texts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            texts.append(block.text)
    compact = "\n".join(texts) or raw
    return {
        "isError": result.isError,
        "content": _truncate_payload(compact),
        "truncated": True,
    }


async def _list_tools(spec: MCPServerSpec) -> list[Any]:
    if not spec.command:
        raise ValueError("MCP server has empty command")
    merged_env = {**os.environ, **spec.env}
    params = StdioServerParameters(
        command=spec.command,
        args=list(spec.args),
        env=merged_env,
        cwd=spec.cwd,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.list_tools()
            return list(res.tools)


async def _call_tool(
    spec: MCPServerSpec, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    merged_env = {**os.environ, **spec.env}
    params = StdioServerParameters(
        command=spec.command,
        args=list(spec.args),
        env=merged_env,
        cwd=spec.cwd,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return _serialize_call_result(result)


def _invoke_tool_sync(spec: MCPServerSpec, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_call_tool(spec, tool_name, arguments))


def register_mcp_tools(registry: ToolRegistry, config_path: Path) -> int:
    """Connect to each enabled MCP server, list tools, register handlers. Returns tool count."""
    if os.environ.get("CODEAGENTS_DISABLE_MCP", "").lower() in {"1", "true", "yes"}:
        return 0
    n = 0
    for spec in load_mcp_specs(config_path):
        if not spec.enabled or not spec.command.strip():
            continue
        try:
            tools = asyncio.run(_list_tools(spec))
        except Exception as exc:
            logger.warning("MCP server %r discovery failed: %s", spec.name, exc)
            continue
        for t in tools:
            orig_name = t.name
            qname = qualified_tool_name(spec.name, orig_name)
            schema = (
                t.inputSchema
                if isinstance(t.inputSchema, dict)
                else {"type": "object", "properties": {}}
            )

            def _handler(
                args: dict[str, Any],
                *,
                _spec: MCPServerSpec = spec,
                _orig: str = orig_name,
            ) -> dict[str, Any]:
                return _invoke_tool_sync(_spec, _orig, args)

            desc = (t.description or spec.description or f"MCP tool {orig_name}").strip()
            registry.register(
                ToolSpec(
                    name=qname,
                    kind="mcp",
                    permission=spec.permission,
                    description=desc,
                    enabled=True,
                    params=(),
                    mcp_input_schema=schema,
                ),
                handler=_handler,
            )
            n += 1
    return n
