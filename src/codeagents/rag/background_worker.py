"""Background workspace indexer.

Runs inside the API server process: a single daemon thread loops every
``INTERVAL_S`` seconds and rebuilds the index incrementally (no-op for files
that haven't changed). Embeddings are best-effort — failures from the
runtime are swallowed so indexing keeps working without an embedding model.

We deliberately don't index on every save: a pull-based 30s tick is cheap
on modern SSDs (sha256 + mtime check per file) and avoids the complexity of
inotify/FSEvents. ``POST /index/refresh`` triggers an immediate run.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from codeagents.core.config import load_app_config
from codeagents.rag.workspace_index import build_index
from codeagents.core.runtime.openai_client import OpenAICompatibleRuntime, RuntimeErrorWithHint


INTERVAL_S = 30.0


class WorkspaceIndexWorker:
    def __init__(self, *, workspace: Path) -> None:
        self.workspace = workspace
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_run_ts: float = 0.0
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="codeagents-indexer", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    def request_refresh(self) -> None:
        """Wake the worker so it runs an extra pass before the next tick."""

        self._wake.set()

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "workspace": str(self.workspace),
                "last_run_ts": self._last_run_ts,
                "last_error": self._last_error,
                "interval_s": INTERVAL_S,
            }

    def _loop(self) -> None:
        # First pass starts ~2s after boot so the API can answer health
        # checks without contention from a big workspace scan.
        time.sleep(2.0)
        while not self._stop.is_set():
            self._run_once()
            self._wake.clear()
            self._wake.wait(timeout=INTERVAL_S)

    def _run_once(self) -> None:
        try:
            cfg = load_app_config()
            embedder: OpenAICompatibleRuntime | None
            try:
                embedder = OpenAICompatibleRuntime(cfg.runtime)
            except Exception:
                embedder = None
            try:
                build_index(
                    self.workspace,
                    embeddings=embedder is not None,
                    embedding_client=embedder,
                    embedding_model=cfg.runtime.embedding_model,
                )
            except RuntimeErrorWithHint:
                # Re-run lexical-only when the embedder runs into a missing
                # model — never let an offline embedder block plain indexing.
                build_index(self.workspace)
            with self._lock:
                self._last_run_ts = time.time()
                self._last_error = None
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
