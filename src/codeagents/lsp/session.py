"""Minimal synchronous LSP JSON-RPC over stdio (request/response only)."""

from __future__ import annotations

import json
import subprocess
import threading
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
    """One subprocess per session; caller should call shutdown()."""

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
        self._lock = threading.Lock()
        self._next_id = 1
        assert self._stdin is not None and self._stdout is not None

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self._stdin.close()
            except Exception:
                pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _write_message(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._lock:
            self._stdin.write(header + body)
            self._stdin.flush()

    def _read_message(self) -> dict[str, Any]:
        assert self._stdout is not None
        headers: dict[str, str] = {}
        while True:
            line = self._stdout.readline()
            if not line:
                raise RuntimeError("LSP stdout closed")
            if line in (b"\r\n", b"\n"):
                break
            if b":" in line:
                key, _, value = line.partition(b":")
                headers[key.decode("ascii").strip().lower()] = value.decode(
                    "ascii", errors="replace"
                ).strip()
        length = int(headers.get("content-length", "0"))
        data = self._stdout.read(length)
        return json.loads(data.decode("utf-8"))

    def request(self, method: str, params: Any) -> Any:
        req_id = self._next_id
        self._next_id += 1
        self._write_message({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        for _ in range(10_000):
            msg = self._read_message()
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(str(err))
                return msg.get("result")
        raise RuntimeError("LSP response not received (too many unrelated messages)")

    def request_dict(self, method: str, params: Any) -> dict[str, Any]:
        result = self.request(method, params)
        if isinstance(result, dict):
            return result
        return {"value": result}

    def notify(self, method: str, params: Any) -> None:
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(self, workspace_root: Path) -> Any:
        root_uri = workspace_root.resolve().as_uri()
        result = self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": root_uri,
                "capabilities": {"workspace": {"symbol": {"dynamicRegistration": False}}},
                "workspaceFolders": [{"uri": root_uri, "name": workspace_root.name}],
            },
        )
        self.notify("initialized", {})
        return result

    def shutdown(self) -> None:
        try:
            self.request("shutdown", None)
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

    def document_symbols(self, file_path: Path) -> Any:
        uri = file_path.resolve().as_uri()
        return self.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})

    def workspace_symbol(self, query: str) -> Any:
        return self.request("workspace/symbol", {"query": query})
