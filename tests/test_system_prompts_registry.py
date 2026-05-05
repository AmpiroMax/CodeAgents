"""Tests for the per-model, per-mode system-prompt registry."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeagents import system_prompts as sp


def _patch_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
) -> Path:
    path = tmp_path / "system_prompts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("CODEAGENTS_SYSTEM_PROMPTS", str(path))
    sp.reload_system_prompts()
    return path


def test_exact_model_exact_mode_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(
        tmp_path,
        monkeypatch,
        {
            "defaults": {"agent": "DEF-A"},
            "models": {
                "qwen3.6:27b-coding-nvfp4": {"agent": "EXACT-A", "ask": "EXACT-Q"},
                "qwen3.6": {"agent": "FAM-A"},
            },
        },
    )
    assert sp.system_prompt_addendum("qwen3.6:27b-coding-nvfp4", "agent") == "EXACT-A"
    assert sp.system_prompt_addendum("qwen3.6:27b-coding-nvfp4", "ask") == "EXACT-Q"


def test_family_fallback_then_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(
        tmp_path,
        monkeypatch,
        {
            "defaults": {"plan": "DEF-PLAN"},
            "models": {"qwen3.6": {"agent": "FAM-A"}},
        },
    )
    # No exact entry → falls back to family agent string.
    assert (
        sp.system_prompt_addendum("qwen3.6:27b-coding-nvfp4", "agent") == "FAM-A"
    )
    # No family entry for plan → falls back to defaults.
    assert (
        sp.system_prompt_addendum("qwen3.6:27b-coding-nvfp4", "plan") == "DEF-PLAN"
    )


def test_unknown_model_falls_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry(
        tmp_path,
        monkeypatch,
        {"defaults": {"agent": "ONLY-DEF"}, "models": {}},
    )
    assert sp.system_prompt_addendum("unheard-model:7b", "agent") == "ONLY-DEF"


def test_missing_registry_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEAGENTS_SYSTEM_PROMPTS", str(tmp_path / "nope.json"))
    sp.reload_system_prompts()
    assert sp.system_prompt_addendum("anything", "agent") == ""


def test_default_key_inside_model_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry(
        tmp_path,
        monkeypatch,
        {
            "defaults": {},
            "models": {"phi": {"default": "PHI-DEFAULT"}},
        },
    )
    assert sp.system_prompt_addendum("phi:3-medium", "ask") == "PHI-DEFAULT"
    assert sp.system_prompt_addendum("phi:3-medium", "agent") == "PHI-DEFAULT"


def test_real_registry_has_known_families() -> None:
    """Smoke-check that the shipped JSON loads and has prompts for models we ship."""
    sp.reload_system_prompts()
    for fam in ("qwen3.6", "qwen2.5-coder", "gpt-oss", "gemma4", "granite4"):
        assert sp.system_prompt_addendum(f"{fam}:27b", "agent")
        assert sp.system_prompt_addendum(f"{fam}:27b", "plan")
        assert sp.system_prompt_addendum(f"{fam}:27b", "ask")
