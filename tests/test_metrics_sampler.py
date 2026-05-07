from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from codeagents.observability import metrics_sampler as ms


@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch: Any) -> None:
    monkeypatch.setattr(ms, "_GLOBAL_SAMPLER", None)
    monkeypatch.setattr(ms, "SAMPLE_INTERVAL_S", 0.05)
    monkeypatch.setattr(ms, "GPU_SAMPLE_EVERY", 1000)
    monkeypatch.setattr(ms, "nvidia_gpu_summary", lambda: {"ok": False, "gpus": []})
    monkeypatch.setattr(ms, "ollama_ps_models", lambda origin: {"ok": True, "models": []})


def test_sampler_collects_history_and_persists_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "metrics.jsonl"
    sampler = ms.MetricsSampler(jsonl_path=path)
    sampler.start()
    try:
        time.sleep(0.25)
    finally:
        sampler.stop()

    history = sampler.history()
    assert len(history) >= 2
    assert all("t" in s for s in history)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["ollama_ps"]["ok"] is True


def test_stream_snapshots_pushes_new_samples(tmp_path: Path) -> None:
    sampler = ms.MetricsSampler(jsonl_path=tmp_path / "m.jsonl")
    sampler.start()
    try:
        stop = threading.Event()
        gen = ms.stream_snapshots(sampler, stop=stop)
        # Should receive at least one snapshot within ~250ms.
        deadline = time.monotonic() + 0.5
        seen: list[dict[str, Any]] = []
        for snap in gen:
            seen.append(snap)
            if len(seen) >= 1 or time.monotonic() > deadline:
                stop.set()
                break
        assert seen
    finally:
        sampler.stop()
