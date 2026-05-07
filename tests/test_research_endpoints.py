"""HTTP endpoints for deep-research reports (Phase 2.B.4)."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from codeagents.core.orchestrator import AgentCore
from codeagents.stores.chat import ChatStore
from codeagents.core.runtime.service import LocalModelService
from codeagents.observability.request_log import ServiceRequestLogger
from codeagents.stores.research import ResearchStore
from codeagents.surfaces.http.server import AgentRequestHandler, ReusableThreadingHTTPServer


def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    chats_root = tmp_path / "chats"
    chats_root.mkdir()
    monkeypatch.setattr(
        "codeagents.stores.chat.default_chats_dir", lambda: chats_root
    )
    agent = AgentCore.from_workspace(tmp_path)

    class H(AgentRequestHandler):
        pass

    H.agent = agent
    H.model_service = LocalModelService()
    H.chat_store = ChatStore(agent.workspace.root)
    H.request_logger = ServiceRequestLogger()
    H.allowed_cors_origins = frozenset()

    server = ReusableThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.05)
    return server, chats_root


def _request(server, method: str, path: str) -> tuple[int, dict]:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(method, path, body=b"" if method == "POST" else None)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    if not body:
        return r.status, {}
    try:
        return r.status, json.loads(body)
    except Exception:
        return r.status, {"_raw": body.decode("utf-8", "replace")}


def test_list_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server, _ = _start(tmp_path, monkeypatch)
    try:
        status, data = _request(server, "GET", "/research/chat-1")
        assert status == 200
        assert data == {"chat_id": "chat-1", "reports": []}
    finally:
        server.shutdown()
        server.server_close()


def test_list_and_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server, chats_root = _start(tmp_path, monkeypatch)
    try:
        store = ResearchStore(chats_root)
        rep = store.create(chat_id="chat-1", query="something")
        store.write_markdown("chat-1", rep.id, "# hello")

        status, listing = _request(server, "GET", "/research/chat-1")
        assert status == 200
        assert len(listing["reports"]) == 1
        assert listing["reports"][0]["id"] == rep.id

        status, full = _request(server, "GET", f"/research/chat-1/{rep.id}")
        assert status == 200
        assert full["report"]["query"] == "something"
        assert full["markdown"] == "# hello"
    finally:
        server.shutdown()
        server.server_close()


def test_load_missing_returns_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server, _ = _start(tmp_path, monkeypatch)
    try:
        status, _ = _request(server, "GET", "/research/chat-1/nope")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_cancel_flips_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server, chats_root = _start(tmp_path, monkeypatch)
    try:
        store = ResearchStore(chats_root)
        rep = store.create(chat_id="chat-1", query="x")
        rep.status = "researching"
        store.save(rep)

        status, payload = _request(
            server, "POST", f"/research/chat-1/{rep.id}/cancel"
        )
        assert status == 200, payload
        assert payload["report"]["status"] == "cancelled"
    finally:
        server.shutdown()
        server.server_close()
