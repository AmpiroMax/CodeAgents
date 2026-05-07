"""Back-compat shim. Prompt resolution moved to :mod:`codeagents.core.modes.prompts`.

The single ``registry/system_prompts.json`` file is gone; each mode now
owns ``registry/prompts/modes/<mode>.json`` with a ``default`` string and
a ``models`` map. The resolved prompt is the FULL system message — there
is no longer a separate ``SYSTEM_PROMPT`` constant prefixed onto it.

This module keeps :func:`system_prompt_addendum` working for any external
caller that still imports it. Internally it's expressed in terms of the
new resolver: the "addendum" returned here is the per-model entry minus
the default, which is what the legacy callers concatenated onto the base
prompt themselves.
"""

from __future__ import annotations

from codeagents.config import PROJECT_ROOT
from codeagents.core.modes.prompts import (
    _load_mode_file,
    reload_prompts,
    resolve_prompt,
)

REGISTRY_PATH = PROJECT_ROOT / "registry" / "prompts" / "modes"


def reload_system_prompts() -> None:
    """Bust the prompt cache (legacy alias)."""
    reload_prompts()


def system_prompt_addendum(model_name: str | None, mode: str) -> str:
    """Return the per-model override snippet for ``model_name`` and ``mode``.

    Subtracts the mode's ``default`` from the resolved prompt so callers
    that still concatenate ``SYSTEM_PROMPT + addendum`` end up with the
    same message. Returns an empty string if the resolved prompt equals
    the default (no per-model override exists).
    """
    full = resolve_prompt(mode, model_name)
    data = _load_mode_file((mode or "agent").lower())
    default = data.get("default") or ""
    if full and default and full.startswith(default):
        return full[len(default) :].lstrip()
    return ""


__all__ = [
    "REGISTRY_PATH",
    "reload_system_prompts",
    "resolve_prompt",
    "system_prompt_addendum",
]
