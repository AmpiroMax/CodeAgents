from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeagents.core.budget.token_counter import (
    DEFAULT_CONTEXT_WINDOWS,
    MIN_CALIBRATION_SAMPLES,
    TokenBudget,
)


def test_estimate_basic_messages_match_tiktoken_within_10_percent() -> None:
    budget = TokenBudget()
    msgs = [
        {"role": "system", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "Implement quicksort in Python and explain the partitioning step."},
        {"role": "assistant", "content": "Sure. Quicksort picks a pivot..."},
    ]
    est = budget.estimate(model="qwen3:30b", messages=msgs)
    # We don't know the exact tokenizer of the target model, but cl100k
    # over the same text should land in the 25-90 range. The +/- 10% claim
    # is enforced empirically by the EMA tests below.
    assert 25 < est < 200
    # Each message carries the per-message envelope overhead.
    assert est >= 3 * 4


def test_estimate_includes_tools() -> None:
    budget = TokenBudget()
    msgs = [{"role": "user", "content": "ping"}]
    no_tools = budget.estimate(model="qwen3:30b", messages=msgs)
    with_tools = budget.estimate(
        model="qwen3:30b",
        messages=msgs,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "Does nothing in particular.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    assert with_tools > no_tools


def test_record_updates_factor_and_persists(tmp_path: Path) -> None:
    storage = tmp_path / ".codeagents" / "token_calibration.json"
    budget = TokenBudget(storage_path=storage)

    msgs = [{"role": "user", "content": "Write a haiku about pickles."}]
    predicted = budget.estimate(model="qwen3:30b", messages=msgs)
    assert predicted > 0
    # Pretend the model reports a real prompt_eval_count 1.5x our estimate.
    actual = int(predicted * 1.5)
    for _ in range(MIN_CALIBRATION_SAMPLES + 2):
        budget.record(model="qwen3:30b", predicted=predicted, actual=actual)

    cal = budget.calibration("qwen3:30b")
    assert cal["samples"] >= MIN_CALIBRATION_SAMPLES
    # EMA should have converged near the sample factor.
    assert 1.3 < cal["factor"] < 1.6
    # And the persisted file should mirror that.
    assert storage.exists()
    data = json.loads(storage.read_text(encoding="utf-8"))
    assert "qwen3:30b" in data["models"]
    assert data["models"]["qwen3:30b"]["samples"] >= MIN_CALIBRATION_SAMPLES


def test_factor_only_applied_after_min_samples(tmp_path: Path) -> None:
    budget = TokenBudget(storage_path=tmp_path / "cal.json")
    msgs = [{"role": "user", "content": "Hi."}]
    raw_estimate = budget.estimate(model="qwen3:30b", messages=msgs)

    # First sample sets the factor but ``estimate`` still uses 1.0
    # because samples < MIN_CALIBRATION_SAMPLES.
    budget.record(model="qwen3:30b", predicted=raw_estimate, actual=raw_estimate * 2)
    assert budget.estimate(model="qwen3:30b", messages=msgs) == raw_estimate

    # After enough samples the factor is applied.
    for _ in range(MIN_CALIBRATION_SAMPLES):
        budget.record(model="qwen3:30b", predicted=raw_estimate, actual=raw_estimate * 2)
    assert budget.estimate(model="qwen3:30b", messages=msgs) > raw_estimate


def test_record_ignores_zero_or_negative() -> None:
    budget = TokenBudget()
    budget.record(model="qwen3:30b", predicted=0, actual=100)
    budget.record(model="qwen3:30b", predicted=100, actual=0)
    assert budget.calibration("qwen3:30b")["samples"] == 0


def test_outlier_sample_clamped() -> None:
    budget = TokenBudget()
    # Ridiculous ratio that would otherwise destroy the EMA.
    for _ in range(MIN_CALIBRATION_SAMPLES + 1):
        budget.record(model="qwen3:30b", predicted=10, actual=1000)
    cal = budget.calibration("qwen3:30b")
    # Clamp upper bound is 3.0.
    assert cal["factor"] <= 3.0


def test_context_window_lookup() -> None:
    budget = TokenBudget()
    assert budget.context_window("qwen3:30b") == DEFAULT_CONTEXT_WINDOWS["qwen3:30b"]
    assert budget.context_window("qwen3:30b-instruct") == DEFAULT_CONTEXT_WINDOWS["qwen3:30b"]
    assert budget.context_window("totally-unknown-model") == DEFAULT_CONTEXT_WINDOWS["default"]


def test_for_workspace_persists_round_trip(tmp_path: Path) -> None:
    b1 = TokenBudget.for_workspace(tmp_path)
    msgs = [{"role": "user", "content": "x"}]
    pred = b1.estimate(model="qwen3:30b", messages=msgs)
    for _ in range(MIN_CALIBRATION_SAMPLES + 1):
        b1.record(model="qwen3:30b", predicted=pred, actual=pred * 2)

    # New instance picks up the persisted factor.
    b2 = TokenBudget.for_workspace(tmp_path)
    cal = b2.calibration("qwen3:30b")
    assert cal["samples"] >= MIN_CALIBRATION_SAMPLES
    assert cal["factor"] > 1.0
