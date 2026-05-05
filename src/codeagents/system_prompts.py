"""Per-model, per-mode system prompt overrides.

The mapping lives in ``registry/system_prompts.json`` (single source of
truth). It is intentionally a flat JSON file so users can edit it without
touching Python code — every entry is a free-form string the agent
appends to the base ``SYSTEM_PROMPT`` for the selected model and mode.

Lookup precedence (first non-empty wins):

  1. ``models[<exact model name>][<mode>]``
  2. ``models[<exact model name>]['default']``
  3. ``models[<family>][<mode>]``       — family = name up to ':'
  4. ``models[<family>]['default']``
  5. ``defaults[<mode>]``
  6. ``defaults['default']`` (usually empty)

"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from codeagents.config import PROJECT_ROOT

REGISTRY_PATH = PROJECT_ROOT / "registry" / "system_prompts.json"


def _registry_path() -> Path:
    raw = os.environ.get("CODEAGENTS_SYSTEM_PROMPTS", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return REGISTRY_PATH


@lru_cache(maxsize=1)
def _registry() -> dict[str, Any]:
    """Cache the parsed JSON. Bust by calling ``reload_system_prompts()``."""
    path = _registry_path()
    if not path.exists():
        return {"defaults": {}, "models": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"defaults": {}, "models": {}}
    if not isinstance(raw, dict):
        return {"defaults": {}, "models": {}}
    raw.setdefault("defaults", {})
    raw.setdefault("models", {})
    if not isinstance(raw["defaults"], dict):
        raw["defaults"] = {}
    if not isinstance(raw["models"], dict):
        raw["models"] = {}
    return raw


def reload_system_prompts() -> None:
    """Drop the cache so the next lookup re-reads the JSON. Used by tests
    and by hot-reload scenarios where the user just edited the file."""
    _registry.cache_clear()


def _family(model_name: str) -> str:
    """``qwen3.6:27b-coding-nvfp4`` → ``qwen3.6``."""
    return (model_name or "").split(":", 1)[0].strip().lower()


def _pick(d: dict[str, Any], mode: str) -> str:
    """Return ``d[mode]`` (or ``d['default']``) if it's a non-empty str."""
    for key in (mode, "default"):
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def system_prompt_addendum(model_name: str | None, mode: str) -> str:
    """Resolve the model-specific prompt override.

    ``mode`` is one of ``'agent' | 'plan' | 'ask'`` (other values are accepted
    and routed through the same lookup so callers can plug in additional
    modes without touching this module).
    """
    if not mode:
        mode = "agent"
    reg = _registry()
    models = reg.get("models", {})
    name = (model_name or "").strip()

    if name and name in models:
        v = _pick(models[name], mode)
        if v:
            return v
    fam = _family(name)
    if fam and fam in models:
        v = _pick(models[fam], mode)
        if v:
            return v
    defaults = reg.get("defaults", {})
    return _pick(defaults, mode)


__all__ = ["system_prompt_addendum", "reload_system_prompts", "REGISTRY_PATH"]
