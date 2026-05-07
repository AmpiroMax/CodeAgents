"""System prompt resolver.

Each mode owns one JSON file at ``registry/prompts/modes/<mode>.json``::

    {
      "default": "<full system prompt for this mode>",
      "models": {
        "qwen3-coder":   "<full prompt with model-specific tweaks>",
        "qwen3-coder:480b": "<even more specific>"
      }
    }

Lookup order in :func:`resolve_prompt` (first non-empty wins):

  1. ``models[<exact name with tag>]``
  2. ``models[<family>]`` — family is the part of the name before ``':'``
  3. ``default``

There is no separate "base" prompt; the resolved string IS the system
message. Per-model entries are full replacements (not appendices), so
adding a model overrides the default in its entirety.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from codeagents.config import PROJECT_ROOT

_PROMPTS_DIR = PROJECT_ROOT / "registry" / "prompts" / "modes"


def _prompts_dir() -> Path:
    raw = os.environ.get("CODEAGENTS_PROMPTS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _PROMPTS_DIR


@lru_cache(maxsize=8)
def _load_mode_file(mode: str) -> dict[str, Any]:
    path = _prompts_dir() / f"{mode}.json"
    if not path.exists():
        return {"default": "", "models": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"default": "", "models": {}}
    if not isinstance(raw, dict):
        return {"default": "", "models": {}}
    raw.setdefault("default", "")
    raw.setdefault("models", {})
    if not isinstance(raw["models"], dict):
        raw["models"] = {}
    if not isinstance(raw["default"], str):
        raw["default"] = ""
    return raw


def reload_prompts() -> None:
    """Drop the cache so the next lookup re-reads the JSON files."""
    _load_mode_file.cache_clear()


def _family(model_name: str) -> str:
    """``qwen3-coder:480b`` → ``qwen3-coder``."""
    return (model_name or "").split(":", 1)[0].strip().lower()


def resolve_prompt(mode: str, model: str | None) -> str:
    """Return the full system prompt for ``mode`` and ``model``.

    ``mode`` defaults to ``'agent'`` if empty/unknown.
    """
    mode_key = (mode or "agent").lower()
    if mode_key not in {"agent", "plan", "ask", "research"}:
        mode_key = "agent"
    data = _load_mode_file(mode_key)
    name = (model or "").strip()
    models = data.get("models") or {}

    if name and name in models:
        v = models[name]
        if isinstance(v, str) and v.strip():
            return v
    fam = _family(name)
    if fam and fam in models:
        v = models[fam]
        if isinstance(v, str) and v.strip():
            return v
    default = data.get("default") or ""
    return default if isinstance(default, str) else ""


__all__ = ["resolve_prompt", "reload_prompts"]
