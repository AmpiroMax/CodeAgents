"""Static web UI mounted at ``/ui/`` when ``--gui-dir`` is set."""

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


def _start_with_gui(tmp_path: Path, gui: Path) -> ReusableThreadingHTTPServer:
    agent = AgentCore.from_workspace(tmp_path)

    class H(AgentRequestHandler):
        pass

    H.agent = agent
    H.model_service = LocalModelService()
    H.chat_store = ChatStore(agent.workspace.root)
    H.request_logger = ServiceRequestLogger()
    H.allowed_cors_origins = frozenset()
    H.gui_static_dir = gui.resolve()

    server = ReusableThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.05)
    return server


def test_ui_index_and_health(tmp_path: Path) -> None:
    gui = tmp_path / "g"
    gui.mkdir()
    (gui / "index.html").write_text("<!doctype html><html><body>ok</body></html>", encoding="utf-8")
    server = _start_with_gui(tmp_path, gui)
    try:
        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/ui/")
        r = conn.getresponse()
        body = r.read().decode()
        assert r.status == 200
        assert "ok" in body
        conn.close()

        conn2 = http.client.HTTPConnection(host, port, timeout=5)
        conn2.request("GET", "/health")
        r2 = conn2.getresponse()
        hbody = r2.read()
        assert r2.status == 200
        assert json.loads(hbody)["ok"] is True
        conn2.close()
    finally:
        server.shutdown()
        server.server_close()


def test_ui_redirect_from_slash_ui(tmp_path: Path) -> None:
    gui = tmp_path / "g"
    gui.mkdir()
    (gui / "index.html").write_text("<html/>", encoding="utf-8")
    server = _start_with_gui(tmp_path, gui)
    try:
        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/ui", headers={"Host": f"{host}:{port}"})
        r = conn.getresponse()
        _ = r.read()
        assert r.status == 301
        assert r.getheader("Location") == "/ui/"
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
