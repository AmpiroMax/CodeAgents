"""HTTP /budget/preview endpoint (Phase 2.A.5)."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

from codeagents.core.orchestrator import AgentCore
from codeagents.stores.chat import ChatStore
from codeagents.core.runtime.service import LocalModelService
from codeagents.observability.request_log import ServiceRequestLogger
from codeagents.surfaces.http.server import AgentRequestHandler, ReusableThreadingHTTPServer


def _start(tmp_path: Path) -> tuple[ReusableThreadingHTTPServer, AgentCore]:
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
    return server, agent


def _get(server: ReusableThreadingHTTPServer, path: str) -> dict:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    assert r.status == 200, body
    return json.loads(body)


def test_budget_preview_zero_state(tmp_path: Path) -> None:
    server, _ = _start(tmp_path)
    try:
        data = _get(server, "/budget/preview")
        # No turn has run yet -> all counters zero, no warn.
        assert data["last_prompt_tokens"] == 0
        assert data["estimated_next"] == 0
        assert data["warn"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_budget_preview_after_simulated_turn(tmp_path: Path) -> None:
    server, agent = _start(tmp_path)
    try:
        # Simulate the agent having just run a turn.
        agent._last_prompt_tokens = 1843
        agent._last_estimate = 3200
        agent._last_context_window = 8192
        agent._last_model = "qwen3:30b"
        data = _get(server, "/budget/preview")
        assert data["last_prompt_tokens"] == 1843
        assert data["estimated_next"] == 3200
        assert data["context_window"] == 8192
        assert data["model"] == "qwen3:30b"
        assert data["warn"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_budget_preview_warn_when_over_85_percent(tmp_path: Path) -> None:
    server, agent = _start(tmp_path)
    try:
        agent._last_prompt_tokens = 0
        agent._last_estimate = 7500
        agent._last_context_window = 8192
        agent._last_model = "qwen3:30b"
        data = _get(server, "/budget/preview")
        assert data["warn"] is True
    finally:
        server.shutdown()
        server.server_close()
