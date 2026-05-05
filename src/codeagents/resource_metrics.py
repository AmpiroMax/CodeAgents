from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from codeagents.config import PROJECT_ROOT, load_app_config


def _ollama_origin_from_runtime_base(base_url: str) -> str:
    u = base_url.rstrip("/")
    if u.endswith("/v1"):
        return u[:-3]
    return u


def disk_usage_bytes(path: Path) -> dict[str, Any]:
    """Return byte size for a directory using `du` when available (faster than pure Python walk)."""
    if not path.exists():
        return {"path": str(path), "exists": False, "bytes": None}
    try:
        completed = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            return {"path": str(path), "exists": True, "bytes": None, "error": "du_failed"}
        kb = int(completed.stdout.split()[0])
        return {"path": str(path), "exists": True, "bytes": kb * 1024}
    except (FileNotFoundError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        return {"path": str(path), "exists": True, "bytes": None, "error": str(exc)}


def ollama_ps_models(origin: str) -> dict[str, Any]:
    url = origin.rstrip("/") + "/api/ps"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        return {"ok": True, "models": raw.get("models", [])}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "models": []}


def nvidia_gpu_summary() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            return {"ok": False, "gpus": [], "error": "nvidia-smi_failed"}
        gpus: list[dict[str, Any]] = []
        for line in completed.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            gpus.append(
                {
                    "name": parts[0],
                    "memory_used_mb": float(parts[1]),
                    "memory_total_mb": float(parts[2]),
                    "utilization_percent": float(parts[3]),
                }
            )
        return {"ok": True, "gpus": gpus}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "gpus": [], "error": str(exc)}


def collect_resource_snapshot(
    *,
    workspace_root: Path | None = None,
    runtime_base_url: str | None = None,
) -> dict[str, Any]:
    """Snapshot of local disk use (models, agent data) and runtime GPU/process hints."""
    cfg = load_app_config()
    base = runtime_base_url or cfg.runtime.base_url
    origin = _ollama_origin_from_runtime_base(base)
    home = Path.home()
    ollama_home = home / ".ollama"
    ws = workspace_root or PROJECT_ROOT
    agent_data = ws / ".codeagents"

    return {
        "ollama_origin": origin,
        "disk": {
            "ollama_home": disk_usage_bytes(ollama_home),
            "ollama_models": disk_usage_bytes(ollama_home / "models"),
            "workspace_codeagents": disk_usage_bytes(agent_data),
        },
        "ollama_ps": ollama_ps_models(origin),
        "nvidia": nvidia_gpu_summary(),
        "notes": [
            "Apple Silicon uses unified memory: prefer `ollama_ps.models[].size` and Activity Monitor for VRAM-style accounting.",
            "Ollama cloud web search uses https://docs.ollama.com/capabilities/web-search — separate from local GGUF disk use.",
        ],
    }
