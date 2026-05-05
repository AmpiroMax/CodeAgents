from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from codeagents.permissions import Permission

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ParamSpec:
    """Schema for a single tool parameter."""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    enum: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str
    permission: Permission
    description: str
    enabled: bool = True
    params: tuple[ParamSpec, ...] = ()
    # When set (MCP tools), used to build OpenAI tool JSON instead of params.
    mcp_input_schema: dict[str, Any] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler | None = None) -> None:
        existing = self._tools.get(spec.name)
        if existing:
            spec = ToolSpec(
                name=spec.name,
                kind=spec.kind,
                permission=spec.permission,
                description=existing.description or spec.description,
                enabled=spec.enabled,
                params=existing.params if not spec.params and existing.params else spec.params,
                mcp_input_schema=spec.mcp_input_schema or existing.mcp_input_schema,
            )
        self._tools[spec.name] = spec
        if handler is not None:
            self._handlers[spec.name] = handler

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._handlers.pop(name, None)

    def list(self, *, include_disabled: bool = False) -> list[ToolSpec]:
        tools = self._tools.values()
        if include_disabled:
            return sorted(tools, key=lambda item: item.name)
        return sorted((tool for tool in tools if tool.enabled), key=lambda item: item.name)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValueError(f"Unknown tool '{name}'") from exc

    def handler(self, name: str) -> ToolHandler:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise ValueError(f"Tool '{name}' has no local handler") from exc


def _parse_params(raw_params: dict[str, Any]) -> tuple[ParamSpec, ...]:
    """Parse a [tools.X.params] table into a tuple of ParamSpec."""
    specs: list[ParamSpec] = []
    for pname, pval in raw_params.items():
        if not isinstance(pval, dict):
            continue
        enum_raw = pval.get("enum")
        specs.append(ParamSpec(
            name=pname,
            type=pval.get("type", "string"),
            description=pval.get("description", ""),
            required=bool(pval.get("required", True)),
            enum=tuple(enum_raw) if enum_raw else None,
        ))
    return tuple(specs)


def load_tool_registry(path: Path) -> ToolRegistry:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    registry = ToolRegistry()

    for name, value in raw.get("tools", {}).items():
        params = _parse_params(value.get("params", {}))
        registry.register(
            ToolSpec(
                name=name,
                kind=value.get("kind", "native"),
                permission=Permission(value.get("permission", Permission.READ_ONLY)),
                description=value.get("description", ""),
                enabled=bool(value.get("enabled", True)),
                params=params,
            )
        )

    # [mcp.*] entries are server configs; tools are registered at runtime by
    # codeagents.mcp.bridge.register_mcp_tools (see AgentCore).

    return registry
