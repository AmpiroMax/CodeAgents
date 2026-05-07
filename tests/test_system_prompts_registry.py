"""Tests for the per-mode system-prompt registry.

Each mode owns its own JSON file at ``registry/prompts/modes/<mode>.json``
with a ``default`` string and a ``models`` map. ``resolve_prompt(mode, model)``
returns the FULL system message; per-model entries fully replace the
default for that model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeagents.core.modes.prompts import reload_prompts, resolve_prompt


def _write_mode(dir_: Path, mode: str, default: str, models: dict[str, str]) -> None:
    (dir_ / f"{mode}.json").write_text(
        json.dumps({"default": default, "models": models}),
        encoding="utf-8",
    )


def _patch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODEAGENTS_PROMPTS_DIR", str(tmp_path))
    reload_prompts()
    return tmp_path


def test_exact_model_wins_over_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dir(tmp_path, monkeypatch)
    _write_mode(
        tmp_path,
        "agent",
        default="DEF-AGENT",
        models={
            "qwen3.6:27b-coding-nvfp4": "EXACT-AGENT",
            "qwen3.6": "FAM-AGENT",
        },
    )
    assert resolve_prompt("agent", "qwen3.6:27b-coding-nvfp4") == "EXACT-AGENT"


def test_family_fallback_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dir(tmp_path, monkeypatch)
    _write_mode(
        tmp_path, "agent",
        default="DEF-AGENT",
        models={"qwen3.6": "FAM-AGENT"},
    )
    _write_mode(tmp_path, "plan", default="DEF-PLAN", models={})

    assert resolve_prompt("agent", "qwen3.6:27b-coding-nvfp4") == "FAM-AGENT"
    assert resolve_prompt("plan", "qwen3.6:27b-coding-nvfp4") == "DEF-PLAN"


def test_unknown_model_falls_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dir(tmp_path, monkeypatch)
    _write_mode(tmp_path, "agent", default="ONLY-DEF", models={})
    assert resolve_prompt("agent", "unheard-model:7b") == "ONLY-DEF"


def test_missing_dir_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEAGENTS_PROMPTS_DIR", str(tmp_path / "nope"))
    reload_prompts()
    assert resolve_prompt("agent", "anything") == ""


def test_unknown_mode_falls_back_to_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dir(tmp_path, monkeypatch)
    _write_mode(tmp_path, "agent", default="AGENT-DEF", models={})
    assert resolve_prompt("totally-bogus", "anything") == "AGENT-DEF"


def test_real_registry_has_default_for_every_mode() -> None:
    """The shipped per-mode JSONs must each ship a non-empty ``default``."""
    reload_prompts()
    for mode in ("agent", "plan", "ask", "research"):
        prompt = resolve_prompt(mode, "totally-unknown-model:1b")
        assert prompt and isinstance(prompt, str), f"{mode}: empty default"


def test_real_registry_has_known_model_entries() -> None:
    """Per-model entries shipped in agent.json/plan.json/ask.json
    fully replace the default — they must therefore be non-empty."""
    reload_prompts()
    for mode in ("agent", "plan", "ask"):
        for fam in ("qwen3.6", "qwen2.5-coder", "gpt-oss", "gemma4", "granite4"):
            prompt = resolve_prompt(mode, f"{fam}:27b")
            assert prompt, f"{fam} {mode}: empty"
