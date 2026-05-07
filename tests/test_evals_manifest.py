from __future__ import annotations

import tomllib
from pathlib import Path


def test_evals_manifest_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "evals" / "manifest.toml"
    raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    benches = raw.get("benchmarks", {})
    assert "ms_marco_tiny" in benches
    assert "local_dir" in benches["ms_marco_tiny"]
