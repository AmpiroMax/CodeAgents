"""Unit tests for :class:`codeagents.lsp.LspManager`.

These tests use a tiny stub LSP server (a Python script that speaks the
JSON-RPC framing on stdio) so the manager can be exercised end-to-end
without depending on pyright or rust-analyzer being installed.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from codeagents.lsp import LspManager
from codeagents.lsp.config import LspServerEntry


_FAKE_SERVER = textwrap.dedent(
    """
    import json
    import sys

    def read_message():
        headers = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            if line in (b"\\r\\n", b"\\n"):
                break
            if b":" in line:
                k, _, v = line.partition(b":")
                headers[k.decode().strip().lower()] = v.decode().strip()
        length = int(headers.get("content-length", "0"))
        return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))

    def write_message(payload):
        body = json.dumps(payload).encode("utf-8")
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii"))
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

    while True:
        msg = read_message()
        if msg is None:
            break
        method = msg.get("method")
        if "id" in msg:
            if method == "initialize":
                write_message({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}})
            elif method == "shutdown":
                write_message({"jsonrpc": "2.0", "id": msg["id"], "result": None})
            elif method == "textDocument/definition":
                write_message({"jsonrpc": "2.0", "id": msg["id"], "result": []})
            elif method == "workspace/symbol":
                write_message({"jsonrpc": "2.0", "id": msg["id"], "result": []})
            else:
                write_message({"jsonrpc": "2.0", "id": msg["id"], "result": None})
        else:
            if method == "textDocument/didOpen":
                params = msg.get("params") or {}
                uri = (params.get("textDocument") or {}).get("uri")
                write_message({
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {
                        "uri": uri,
                        "diagnostics": [{
                            "range": {"start": {"line": 0, "character": 0},
                                      "end": {"line": 0, "character": 5}},
                            "severity": 1,
                            "message": "fake error",
                            "source": "fake",
                        }],
                    },
                })
            if method == "exit":
                break
    """
).strip()


def _make_entry(tmp_path: Path, name: str = "fake") -> LspServerEntry:
    script = tmp_path / "fake_lsp.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    return LspServerEntry(
        name=name,
        enabled=True,
        command=sys.executable,
        args=[str(script)],
        languages=("python",),
        root_markers=(),
        idle_timeout_seconds=0,
    )


def test_for_path_routes_by_language(tmp_path: Path) -> None:
    entry = _make_entry(tmp_path)
    mgr = LspManager(tmp_path, [entry])
    try:
        py_file = tmp_path / "x.py"
        py_file.write_text("x = 1\n", encoding="utf-8")
        rs_file = tmp_path / "x.rs"
        rs_file.write_text("fn main() {}\n", encoding="utf-8")
        assert mgr.for_path(py_file) is not None
        assert mgr.for_path(rs_file) is None
    finally:
        mgr.shutdown_all()


def test_session_is_reused(tmp_path: Path) -> None:
    entry = _make_entry(tmp_path)
    mgr = LspManager(tmp_path, [entry])
    try:
        f1 = tmp_path / "a.py"
        f1.write_text("a = 1\n", encoding="utf-8")
        f2 = tmp_path / "b.py"
        f2.write_text("b = 2\n", encoding="utf-8")
        h1 = mgr.for_path(f1)
        h2 = mgr.for_path(f2)
        assert h1 is h2  # same handle, same subprocess
    finally:
        mgr.shutdown_all()


def test_diagnostics_collected(tmp_path: Path) -> None:
    entry = _make_entry(tmp_path)
    mgr = LspManager(tmp_path, [entry])
    try:
        f = tmp_path / "x.py"
        f.write_text("x = 1\n", encoding="utf-8")
        diags = mgr.diagnostics(f, timeout=2.0)
        assert diags, "expected at least one diagnostic from fake server"
        d = diags[0]
        assert d["severity"] == "error"
        assert d["line"] == 1  # 1-based
        assert d["message"] == "fake error"
    finally:
        mgr.shutdown_all()


def test_command_not_on_path(tmp_path: Path) -> None:
    entry = LspServerEntry(
        name="missing",
        enabled=True,
        command="this-binary-does-not-exist-zzz",
        args=[],
        languages=("python",),
    )
    mgr = LspManager(tmp_path, [entry])
    try:
        f = tmp_path / "x.py"
        f.write_text("y = 2\n", encoding="utf-8")
        assert mgr.for_path(f) is None
        assert mgr.diagnostics(f) == []
    finally:
        mgr.shutdown_all()


def test_root_markers_resolution(tmp_path: Path) -> None:
    nested = tmp_path / "pkg" / "src"
    nested.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    entry = LspServerEntry(
        name="rooted",
        enabled=True,
        command=sys.executable,
        args=["-c", "print('x')"],
        languages=("python",),
        root_markers=("pyproject.toml",),
    )
    mgr = LspManager(nested, [entry])
    try:
        assert mgr._resolve_root(entry) == tmp_path
    finally:
        mgr.shutdown_all()


@pytest.mark.parametrize("ext", [".py"])
def test_diagnostics_no_error_for_missing_file(tmp_path: Path, ext: str) -> None:
    entry = _make_entry(tmp_path)
    mgr = LspManager(tmp_path, [entry])
    try:
        ghost = tmp_path / f"missing{ext}"
        assert mgr.diagnostics(ghost) == []
    finally:
        mgr.shutdown_all()
