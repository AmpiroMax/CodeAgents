"""Long-lived per-language LSP server pool.

The manager keeps at most one running :class:`LspSession` per configured
``LspServerEntry``. Sessions are started lazily on the first request that
needs them, and an idle watcher thread shuts them down after
``idle_timeout_seconds`` of inactivity.

Routing: :meth:`for_path` picks the first enabled server whose
``languages`` list contains the language id derived from the file's
extension (see :func:`codeagents.lsp.session._suffix_lang`).
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Iterable

from codeagents.lsp.config import LspServerEntry, filter_enabled
from codeagents.lsp.diagnostics import Diagnostic, from_lsp
from codeagents.lsp.session import LspSession, _suffix_lang

logger = logging.getLogger(__name__)


class _ServerHandle:
    __slots__ = ("entry", "session", "last_used", "opened", "version", "root")

    def __init__(self, entry: LspServerEntry, session: LspSession, root: Path) -> None:
        self.entry = entry
        self.session = session
        self.last_used = time.monotonic()
        self.opened: dict[Path, int] = {}
        self.version = 1
        self.root = root


class LspManager:
    """Pool of language servers keyed by entry name."""

    def __init__(
        self,
        workspace_root: Path,
        entries: Iterable[LspServerEntry],
        *,
        idle_check_interval: float = 30.0,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._entries = filter_enabled(entries)
        self._lock = threading.RLock()
        self._handles: dict[str, _ServerHandle] = {}
        self._stopped = False
        self._watcher = threading.Thread(
            target=self._idle_loop, args=(idle_check_interval,), daemon=True
        )
        self._watcher.start()

    # ── public API ───────────────────────────────────────────────────
    @property
    def entries(self) -> list[LspServerEntry]:
        return list(self._entries)

    def for_path(self, path: Path) -> _ServerHandle | None:
        """Return (lazily started) handle covering ``path``, or ``None``."""
        lang = _suffix_lang(Path(path).suffix)
        if lang == "plaintext":
            return None
        with self._lock:
            if self._stopped:
                return None
            for entry in self._entries:
                if lang not in entry.languages:
                    continue
                handle = self._handles.get(entry.name)
                if handle is None:
                    handle = self._start(entry)
                    if handle is None:
                        continue
                    self._handles[entry.name] = handle
                handle.last_used = time.monotonic()
                return handle
        return None

    def for_query(self) -> _ServerHandle | None:
        """Return any running/startable handle for `workspace/symbol` calls."""
        with self._lock:
            if self._stopped:
                return None
            for entry in self._entries:
                handle = self._handles.get(entry.name)
                if handle is None:
                    handle = self._start(entry)
                    if handle is None:
                        continue
                    self._handles[entry.name] = handle
                handle.last_used = time.monotonic()
                return handle
        return None

    def diagnostics(self, path: Path, *, timeout: float = 3.0) -> list[Diagnostic]:
        """Open (or refresh) ``path`` and collect publishDiagnostics."""
        path = Path(path)
        if not path.exists() or not path.is_file():
            return []
        handle = self.for_path(path)
        if handle is None:
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        with self._lock:
            handle.session.drain_notifications("textDocument/publishDiagnostics")
            if path in handle.opened:
                handle.version += 1
                handle.opened[path] = handle.version
                handle.session.did_change(path, text, handle.version)
            else:
                handle.opened[path] = 1
                handle.session.did_open(path, text)
            handle.last_used = time.monotonic()
        target_uri = path.resolve().as_uri()
        deadline = time.monotonic() + timeout
        collected: list[dict] = []
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            batch = handle.session.wait_for_notification(
                "textDocument/publishDiagnostics", timeout=remaining
            )
            if not batch:
                break
            for params in batch:
                if params.get("uri") == target_uri:
                    collected = list(params.get("diagnostics") or [])
        return [from_lsp(d) for d in collected if isinstance(d, dict)]

    def ensure_open(self, handle: _ServerHandle, path: Path) -> None:
        """Make sure ``path`` is open in ``handle``'s server (idempotent)."""
        path = Path(path)
        with self._lock:
            if path in handle.opened:
                return
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return
            handle.opened[path] = 1
            handle.session.did_open(path, text)
            handle.last_used = time.monotonic()

    def shutdown_all(self) -> None:
        with self._lock:
            self._stopped = True
            handles = list(self._handles.values())
            self._handles.clear()
        for handle in handles:
            try:
                handle.session.shutdown()
            except Exception:
                logger.debug("LSP shutdown failed for %s", handle.entry.name, exc_info=True)

    # ── internals ────────────────────────────────────────────────────
    def _start(self, entry: LspServerEntry) -> _ServerHandle | None:
        if not shutil.which(entry.command):
            logger.info("LSP server %s not on PATH (%s); skipping", entry.name, entry.command)
            return None
        root = self._resolve_root(entry)
        try:
            session = LspSession(entry.command, list(entry.args), cwd=root)
            session.initialize(root)
        except Exception as exc:
            logger.warning("LSP server %s failed to start: %s", entry.name, exc)
            return None
        return _ServerHandle(entry, session, root)

    def _resolve_root(self, entry: LspServerEntry) -> Path:
        if not entry.root_markers:
            return self._workspace_root
        current = self._workspace_root.resolve()
        candidates = [current, *current.parents]
        for marker in entry.root_markers:
            for parent in candidates:
                if (parent / marker).exists():
                    return parent
        return self._workspace_root

    def _idle_loop(self, interval: float) -> None:
        while True:
            time.sleep(interval)
            with self._lock:
                if self._stopped:
                    return
                now = time.monotonic()
                expired: list[str] = []
                for name, handle in self._handles.items():
                    timeout = handle.entry.idle_timeout_seconds
                    if timeout > 0 and (now - handle.last_used) > timeout:
                        expired.append(name)
                victims = [self._handles.pop(n) for n in expired]
            for handle in victims:
                try:
                    handle.session.shutdown()
                except Exception:
                    pass


__all__ = ["LspManager"]
