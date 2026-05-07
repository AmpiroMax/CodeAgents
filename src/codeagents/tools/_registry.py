"""Tool registry primitives.

This module owns the data classes that describe tools and the in-memory
registry that maps names to handlers. Native tool *specifications* (with full
descriptions and parameter schemas) live in :mod:`codeagents.tools._native_specs`,
which is the single source of truth — see :func:`register_native_specs`.

This is the Stage-2 home of what used to live in ``codeagents.tools`` (a
flat module). The flat module is gone; ``from codeagents.tools import
ToolRegistry`` keeps working because :mod:`codeagents.tools` re-exports
these names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

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


def register_native_specs(registry: ToolRegistry, specs: Iterable[ToolSpec]) -> None:
    """Seed the registry with native tool specifications.

    This is intentionally separate from per-handler registration: handlers
    still call ``registry.register(ToolSpec(name=..., description="", ...),
    handler=...)`` and the merge in :py:meth:`ToolRegistry.register` keeps
    the rich description/params from the seeded spec.
    """
    for spec in specs:
        registry.register(spec)


__all__ = [
    "ParamSpec",
    "ToolHandler",
    "ToolRegistry",
    "ToolSpec",
    "register_native_specs",
]
