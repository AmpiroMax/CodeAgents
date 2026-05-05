"""CORS for browser GUI clients; CLI/TUI do not send ``Origin``."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from codeagents.agent import AgentCore
from codeagents.chat_store import ChatStore
from codeagents.model_service import LocalModelService
from codeagents.request_log import ServiceRequestLogger
from codeagents.server import AgentRequestHandler, ReusableThreadingHTTPServer, cors_origins_from_env


def test_cors_origins_from_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEAGENTS_CORS_ORIGINS", "")
    assert cors_origins_from_env() == frozenset()


def test_cors_origins_from_env_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CODEAGENTS_CORS_ORIGINS",
        "http://a.example, http://b.example ",
    )
    assert cors_origins_from_env() == frozenset(
        {"http://a.example", "http://b.example"}
    )


def _start_test_server(tmp_path: Path, *, allowed: frozenset[str]) -> ReusableThreadingHTTPServer:
    agent = AgentCore.from_workspace(tmp_path)

    class H(AgentRequestHandler):
        pass

    H.agent = agent
    H.model_service = LocalModelService()
    H.chat_store = ChatStore(agent.workspace.root)
    H.request_logger = ServiceRequestLogger()
    H.allowed_cors_origins = allowed

    server = ReusableThreadingHTTPServer(("127.0.0.1", 0), H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    return server


def test_options_preflight_sets_cors_headers(tmp_path: Path) -> None:
    origin = "http://localhost:5173"
    server = _start_test_server(tmp_path, allowed=frozenset({origin}))
    try:
        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "OPTIONS",
            "/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            },
        )
        r = conn.getresponse()
        assert r.status == 204
        assert r.getheader("Access-Control-Allow-Origin") == origin
        assert "GET" in (r.getheader("Access-Control-Allow-Methods") or "")
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_get_health_reflects_origin_only_when_allowed(tmp_path: Path) -> None:
    allowed = "http://localhost:5173"
    server = _start_test_server(tmp_path, allowed=frozenset({allowed}))
    try:
        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/health", headers={"Origin": allowed})
        r = conn.getresponse()
        body = r.read()
        assert r.status == 200
        assert json.loads(body)["ok"] is True
        assert r.getheader("Access-Control-Allow-Origin") == allowed
        conn.close()

        conn2 = http.client.HTTPConnection(host, port, timeout=5)
        conn2.request("GET", "/health", headers={"Origin": "http://evil.test"})
        r2 = conn2.getresponse()
        r2.read()
        assert r2.status == 200
        assert r2.getheader("Access-Control-Allow-Origin") is None
        conn2.close()
    finally:
        server.shutdown()
        server.server_close()
