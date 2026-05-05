from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from codeagents.resource_metrics import (
    collect_resource_snapshot,
    disk_usage_bytes,
    nvidia_gpu_summary,
    ollama_ps_models,
)


def test_disk_usage_bytes_missing():
    out = disk_usage_bytes(Path(gettempdir()) / str(uuid4()))
    assert out["exists"] is False


def test_collect_resource_snapshot_shape():
    snap = collect_resource_snapshot()
    assert "disk" in snap
    assert "ollama_ps" in snap
    assert "nvidia" in snap
    assert "ollama_home" in snap["disk"]


def test_ollama_ps_models_invalid_origin():
    out = ollama_ps_models("http://127.0.0.1:9")
    assert out["ok"] is False
    assert out["models"] == []


def test_nvidia_runs_or_skips():
    out = nvidia_gpu_summary()
    assert "ok" in out
    assert "gpus" in out
