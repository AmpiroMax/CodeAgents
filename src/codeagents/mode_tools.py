"""Back-compat shim. The mode registry moved to :mod:`codeagents.core.modes`.

External callers (tests, scripts) that imported from this module keep
working through the re-exports below. New code should import from
``codeagents.core.modes`` directly.
"""

from __future__ import annotations

from codeagents.core.modes import filter_for_mode, whitelist_for

# Legacy public dict — external scripts/tests may peek at it. Match the
# old shape (lists, not tuples).
from codeagents.core.modes import MODE_REGISTRY as _REGISTRY

MODE_TOOLS: dict[str, list[str]] = {
    name: list(spec.tool_whitelist) for name, spec in _REGISTRY.items()
}

__all__ = ["MODE_TOOLS", "filter_for_mode", "whitelist_for"]
