"""HTTP CRUD for chats: PATCH (rename), DELETE, POST title (auto-name).

The auto-name endpoint normally calls the model. We patch ``AgentCore``
``complete_chat`` to a stub so the test stays offline and deterministic.
"""

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
from codeagents.surfaces.http.server import (
    AgentRequestHandler,
    ReusableThreadingHTTPServer,
    _normalize_title,
)


def _start_server(tmp_path: Path) -> ReusableThreadingHTTPServer:
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
    return server


def _request(server: ReusableThreadingHTTPServer, method: str, path: str,
             body: dict | None = None) -> tuple[int, dict]:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    payload = None
    headers = {}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, (json.loads(raw) if raw else {})


def test_patch_chat_renames_without_touching_messages(tmp_path: Path) -> None:
    server = _start_server(tmp_path)
    try:
        status, created = _request(server, "POST", "/chats",
                                   {"title": "old", "meta": {}})
        assert status == 200
        chat_id = created["chat"]["id"]

        status, patched = _request(server, "PATCH", f"/chats/{chat_id}",
                                   {"title": "Brand New Title"})
        assert status == 200
        assert patched["chat"]["meta"]["title"] == "Brand New Title"
        assert patched["chat"]["messages"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_delete_chat_removes_file(tmp_path: Path) -> None:
    server = _start_server(tmp_path)
    try:
        _, created = _request(server, "POST", "/chats", {"title": "x"})
        chat_id = created["chat"]["id"]

        status, body = _request(server, "DELETE", f"/chats/{chat_id}")
        assert status == 200
        assert body["deleted"] == chat_id

        status_again, _ = _request(server, "DELETE", f"/chats/{chat_id}")
        assert status_again == 404

        chat_file = tmp_path / ".codeagents" / "chats" / f"{chat_id}.json"
        assert not chat_file.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_post_title_uses_active_model_and_normalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _start_server(tmp_path)
    try:
        # Capture the model + reasoning_effort that the title endpoint sends
        # to the runtime so we can assert the GUI's chosen model is used.
        captured: dict[str, object] = {}

        from codeagents.core.runtime.openai_client import OpenAICompatibleRuntime

        def fake_chat(self, *, model, chat, reasoning_effort=None):
            captured["model"] = model.name
            captured["reasoning_effort"] = reasoning_effort
            return '"Refactor The Database Layer Today Please."'

        monkeypatch.setattr(OpenAICompatibleRuntime, "chat", fake_chat)

        _, created = _request(server, "POST", "/chats", {"title": "tmp"})
        chat_id = created["chat"]["id"]

        status, body = _request(
            server, "POST", f"/chats/{chat_id}/title",
            {
                "prompt": "Please refactor the database layer to use SQLAlchemy 2.0",
                "model": "qwen3:4b",
            },
        )
        assert status == 200
        assert body["title"] == "Refactor The Database Layer Today"
        assert body["chat"]["meta"]["title"] == "Refactor The Database Layer Today"
        assert captured["model"] == "qwen3:4b"
        assert captured["reasoning_effort"] == "none"
    finally:
        server.shutdown()
        server.server_close()


def test_normalize_title_helper() -> None:
    assert _normalize_title("'Add User Auth.'") == "Add User Auth"
    assert _normalize_title("first line\nsecond line") == "first line"
    assert _normalize_title("a b c d e f g h") == "a b c d e"
    assert _normalize_title("") == ""
