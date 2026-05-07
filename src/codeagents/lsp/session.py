"""Synchronous LSP JSON-RPC over stdio with a background reader thread.

The reader thread classifies incoming frames into:

* responses keyed by request ``id`` — picked up by :meth:`LspSession.request`,
* server→client requests (we always reply with ``null`` so servers like
  pyright/rust-analyzer can finish initialization),
* unsolicited notifications, kept in a per-method buffer and exposed via
  :meth:`LspSession.wait_for_notification` / :meth:`LspSession.drain_notifications`.

This buffering is what enables :class:`LspManager.diagnostics` to wait for
``textDocument/publishDiagnostics`` after a ``did_open`` / ``did_change``.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


def _suffix_lang(suffix: str) -> str:
    return {
        ".py": "python",
        ".rs": "rust",
        ".ts": "typescript",
        ".tsx": "typescriptreact",
        ".js": "javascript",
        ".jsx": "javascriptreact",
        ".go": "go",
        ".toml": "toml",
        ".md": "markdown",
    }.get(suffix.lower(), "plaintext")


class LspSession:
    """One subprocess per session; caller should call :meth:`shutdown`."""

    def __init__(
        self,
        command: str,
        args: list[str],
        *,
        cwd: Path | None = None,
    ) -> None:
        self._proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            bufsize=0,
        )
        self._stdin = self._proc.stdin
        self._stdout = self._proc.stdout
        assert self._stdin is not None and self._stdout is not None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cv = threading.Condition(self._state_lock)
        self._next_id = 1
        self._responses: dict[int, dict[str, Any]] = {}
        self._notifications: dict[str, list[dict[str, Any]]] = {}
        self._stopped = False
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

    # ── reader thread ────────────────────────────────────────────────
    def _reader_loop(self) -> None:
        try:
            while True:
                msg = self._read_message_blocking()
                if msg is None:
                    break
                self._dispatch(msg)
        finally:
            with self._cv:
                self._stopped = True
                self._cv.notify_all()

    def _read_message_blocking(self) -> dict[str, Any] | None:
        assert self._stdout is not None
        headers: dict[str, str] = {}
        while True:
            line = self._stdout.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            if b":" in line:
                key, _, value = line.partition(b":")
                headers[key.decode("ascii").strip().lower()] = value.decode(
                    "ascii", errors="replace"
                ).strip()
        length = int(headers.get("content-length", "0"))
        data = self._stdout.read(length) if length else b""
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            return {}

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            try:
                req_id = int(msg["id"])
            except (TypeError, ValueError):
                return
            with self._cv:
                self._responses[req_id] = msg
                self._cv.notify_all()
            return
        method = msg.get("method")
        if not isinstance(method, str):
            return
        if "id" in msg:
            # Server→client request: reply with null result so initialization
            # handshakes (workspace/configuration etc.) can complete.
            try:
                self._write_message(
                    {"jsonrpc": "2.0", "id": msg["id"], "result": None}
                )
            except Exception:
                pass
            return
        with self._cv:
            self._notifications.setdefault(method, []).append(msg.get("params") or {})
            self._cv.notify_all()

    # ── writer ───────────────────────────────────────────────────────
    def _write_message(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            assert self._stdin is not None
            self._stdin.write(header + body)
            self._stdin.flush()

    # ── public API ───────────────────────────────────────────────────
    def is_alive(self) -> bool:
        return self._proc.poll() is None and not self._stopped

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                if self._stdin is not None:
                    self._stdin.close()
            except Exception:
                pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def request(self, method: str, params: Any, *, timeout: float = 10.0) -> Any:
        with self._state_lock:
            req_id = self._next_id
            self._next_id += 1
        self._write_message(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
        deadline = time.monotonic() + timeout
        with self._cv:
            while req_id not in self._responses and not self._stopped:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"LSP {method} timed out after {timeout}s")
                self._cv.wait(timeout=remaining)
            if self._stopped and req_id not in self._responses:
                raise RuntimeError("LSP session stopped")
            msg = self._responses.pop(req_id)
        if "error" in msg:
            raise RuntimeError(str(msg["error"]))
        return msg.get("result")

    def request_dict(self, method: str, params: Any) -> dict[str, Any]:
        result = self.request(method, params)
        if isinstance(result, dict):
            return result
        return {"value": result}

    def notify(self, method: str, params: Any) -> None:
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def wait_for_notification(
        self, method: str, *, timeout: float = 3.0
    ) -> list[dict[str, Any]]:
        """Drain any notifications received for ``method`` within ``timeout``.

        Returns whatever has accumulated so far (possibly empty if the
        server didn't emit anything). Useful for collecting
        ``textDocument/publishDiagnostics`` after a ``did_open``.
        """
        deadline = time.monotonic() + timeout
        with self._cv:
            while not self._notifications.get(method) and not self._stopped:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cv.wait(timeout=remaining)
            return self._notifications.pop(method, [])

    def drain_notifications(self, method: str) -> list[dict[str, Any]]:
        with self._cv:
            return self._notifications.pop(method, [])

    def initialize(self, workspace_root: Path) -> Any:
        root_uri = workspace_root.resolve().as_uri()
        result = self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": root_uri,
                "capabilities": {
                    "workspace": {"symbol": {"dynamicRegistration": False}},
                    "textDocument": {
                        "publishDiagnostics": {"relatedInformation": True},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                    },
                },
                "workspaceFolders": [{"uri": root_uri, "name": workspace_root.name}],
            },
            timeout=30.0,
        )
        self.notify("initialized", {})
        return result

    def shutdown(self) -> None:
        try:
            self.request("shutdown", None, timeout=3.0)
        except Exception:
            pass
        try:
            self.notify("exit", {})
        except Exception:
            pass
        self.close()

    def did_open(self, file_path: Path, text: str) -> None:
        uri = file_path.resolve().as_uri()
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": _suffix_lang(file_path.suffix),
                    "version": 1,
                    "text": text,
                }
            },
        )

    def did_change(self, file_path: Path, text: str, version: int) -> None:
        uri = file_path.resolve().as_uri()
        self.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    def did_close(self, file_path: Path) -> None:
        uri = file_path.resolve().as_uri()
        self.notify(
            "textDocument/didClose",
            {"textDocument": {"uri": uri}},
        )

    def document_symbols(self, file_path: Path) -> Any:
        uri = file_path.resolve().as_uri()
        return self.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})

    def workspace_symbol(self, query: str) -> Any:
        return self.request("workspace/symbol", {"query": query})

    def definition(self, file_path: Path, line: int, character: int) -> Any:
        uri = file_path.resolve().as_uri()
        return self.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )

    def references(
        self,
        file_path: Path,
        line: int,
        character: int,
        *,
        include_declaration: bool = False,
    ) -> Any:
        uri = file_path.resolve().as_uri()
        return self.request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": bool(include_declaration)},
            },
        )

    def hover(self, file_path: Path, line: int, character: int) -> Any:
        uri = file_path.resolve().as_uri()
        return self.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
