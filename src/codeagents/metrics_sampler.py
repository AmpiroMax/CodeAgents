"""Realtime resource metrics sampler.

Collects CPU%, RSS, GPU and Ollama loaded-model state at ~1Hz and exposes:

* a ring-buffer (last ``RING_SIZE`` snapshots) for ``GET /metrics/history``;
* an NDJSON stream subscribers can iterate to receive new snapshots
  (used by ``GET /metrics/stream``);
* JSONL rotation to ``<workspace>/.codeagents/metrics.jsonl`` (rotated at
  ``ROTATE_AT_BYTES``).

Intentionally cheap: each snapshot is a flat dict with primitives only so we
can JSON-serialise without thinking. Heavy collectors (``nvidia-smi``,
``du``) are still in :mod:`codeagents.resource_metrics` and are NOT called
from the 1Hz loop — only Ollama ``/api/ps`` (very cheap) and OS-level CPU/RAM
hooks are sampled on the hot path.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterator

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - psutil is in pyproject but be defensive
    psutil = None  # type: ignore

from codeagents.resource_metrics import (
    _ollama_origin_from_runtime_base,
    nvidia_gpu_summary,
    ollama_ps_models,
)
from codeagents.config import load_app_config


RING_SIZE = 600  # 10 minutes at 1Hz
SAMPLE_INTERVAL_S = 1.0
ROTATE_AT_BYTES = 5 * 1024 * 1024  # 5 MB
GPU_SAMPLE_EVERY = 5  # nvidia-smi every N samples (it's slow)


class MetricsSampler:
    """Background thread that pushes a snapshot per second.

    Multiple subscribers can listen via :meth:`subscribe`; each gets its own
    bounded queue so a slow consumer can't stall the producer.
    """

    def __init__(self, *, jsonl_path: Path, runtime_base_url: str | None = None) -> None:
        self.jsonl_path = jsonl_path
        self._origin = _ollama_origin_from_runtime_base(
            runtime_base_url or load_app_config().runtime.base_url
        )
        self._buffer: deque[dict[str, Any]] = deque(maxlen=RING_SIZE)
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[dict[str, Any]]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu_cache: dict[str, Any] = {"ok": False, "gpus": []}
        self._gpu_tick = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="codeagents-metrics", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    # --- API ---------------------------------------------------------

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer)

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        with self._lock:
            self._subs.append(q)
            for snap in self._buffer:
                try:
                    q.put_nowait(snap)
                except queue.Full:
                    break
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    # --- loop --------------------------------------------------------

    def _loop(self) -> None:
        # Prime psutil's CPU% so the first reading isn't always 0.0.
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass
        while not self._stop.is_set():
            try:
                snap = self._sample()
            except Exception as exc:  # never let the sampler die
                snap = {"t": time.time(), "error": str(exc)}
            self._publish(snap)
            self._stop.wait(SAMPLE_INTERVAL_S)

    def _sample(self) -> dict[str, Any]:
        now = time.time()
        cpu = 0.0
        rss_mb = 0.0
        ram_total_mb = 0.0
        ram_used_pct = 0.0
        if psutil is not None:
            try:
                cpu = float(psutil.cpu_percent(interval=None))
                vm = psutil.virtual_memory()
                ram_total_mb = vm.total / (1024 * 1024)
                ram_used_pct = float(vm.percent)
                proc = psutil.Process(os.getpid())
                rss_mb = proc.memory_info().rss / (1024 * 1024)
            except Exception:
                pass

        # nvidia-smi is slow (~50–200ms) so sample at lower cadence.
        self._gpu_tick = (self._gpu_tick + 1) % GPU_SAMPLE_EVERY
        if self._gpu_tick == 0:
            try:
                self._gpu_cache = nvidia_gpu_summary()
            except Exception:
                self._gpu_cache = {"ok": False, "gpus": []}

        try:
            ps = ollama_ps_models(self._origin)
        except Exception:
            ps = {"ok": False, "models": []}

        return {
            "t": now,
            "cpu_percent": cpu,
            "rss_mb": rss_mb,
            "ram_total_mb": ram_total_mb,
            "ram_used_percent": ram_used_pct,
            "gpu": self._gpu_cache,
            "ollama_ps": ps,
        }

    def _publish(self, snap: dict[str, Any]) -> None:
        with self._lock:
            self._buffer.append(snap)
            for q in list(self._subs):
                try:
                    q.put_nowait(snap)
                except queue.Full:
                    # Drop the oldest queued item to keep up with realtime.
                    try:
                        q.get_nowait()
                        q.put_nowait(snap)
                    except (queue.Empty, queue.Full):
                        pass
        self._append_jsonl(snap)

    def _append_jsonl(self, snap: dict[str, Any]) -> None:
        try:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            if (
                self.jsonl_path.exists()
                and self.jsonl_path.stat().st_size >= ROTATE_AT_BYTES
            ):
                self.jsonl_path.replace(self.jsonl_path.with_suffix(".jsonl.1"))
            with self.jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
        except Exception:
            pass


_GLOBAL_SAMPLER: MetricsSampler | None = None
_GLOBAL_LOCK = threading.Lock()


def get_global_sampler(*, jsonl_path: Path | None = None) -> MetricsSampler:
    """Return a process-wide singleton sampler, starting it on first use."""

    global _GLOBAL_SAMPLER
    with _GLOBAL_LOCK:
        if _GLOBAL_SAMPLER is None:
            path = jsonl_path or Path(".codeagents/metrics.jsonl").resolve()
            _GLOBAL_SAMPLER = MetricsSampler(jsonl_path=path)
            _GLOBAL_SAMPLER.start()
        return _GLOBAL_SAMPLER


def stream_snapshots(sampler: MetricsSampler, *, stop: threading.Event) -> Iterator[dict[str, Any]]:
    """Yield snapshots until ``stop`` is set or the consumer goes away."""

    q = sampler.subscribe()
    try:
        while not stop.is_set():
            try:
                snap = q.get(timeout=1.0)
            except queue.Empty:
                continue
            yield snap
    finally:
        sampler.unsubscribe(q)
