from __future__ import annotations

import json
import signal
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from codeagents.agent import AgentCore
from codeagents.chat_store import ChatStore
from codeagents.config import load_app_config
from codeagents.indexer import build_index, index_summary, search_index
from codeagents.inference_log import InferenceLogger
from codeagents.model_service import LocalModelService, RegisteredModel
from codeagents.request_log import ServiceRequestLogger, ServiceRequestLogEntry
from codeagents.runtime import RuntimeErrorWithHint
from codeagents.schemas import BatchInferenceRequest, Chat, InferenceRequest


class AgentRequestHandler(BaseHTTPRequestHandler):
    # Disable output buffering so NDJSON events flush immediately.
    wbufsize = 0

    agent: AgentCore
    model_service: LocalModelService
    chat_store: ChatStore
    request_logger: ServiceRequestLogger

    def do_GET(self) -> None:
        started = time.perf_counter()
        if self.path == "/health":
            self._send_json({"ok": True})
            self._log_request("GET", {}, 200, started)
            return
        if self.path == "/models":
            config = load_app_config()
            self._send_json(
                {
                    "models": [
                        {
                            "key": model.key,
                            "name": model.name,
                            "role": model.role,
                            "context_tokens": model.context_tokens,
                        }
                        for model in config.models.values()
                    ]
                }
            )
            self._log_request("GET", {}, 200, started)
            return
        if self.path == "/tools":
            self._send_json(
                {
                    "tools": [
                        {
                            "name": tool.name,
                            "kind": tool.kind,
                            "permission": tool.permission.value,
                            "enabled": tool.enabled,
                            "description": tool.description,
                        }
                        for tool in self.agent.tools.list(include_disabled=True)
                    ]
                }
            )
            self._log_request("GET", {}, 200, started)
            return
        if self.path == "/inference/models":
            registry_models = [
                {
                    "key": item.key,
                    "display_name": item.display_name,
                    "backend": item.backend,
                    "runtime_model": item.runtime_model,
                    "profile": item.profile,
                    "weights_path": item.weights_path,
                    "source": item.source,
                    "notes": item.notes,
                }
                for item in self.model_service.list_models()
            ]
            registry_runtime_names = {
                m["runtime_model"] for m in registry_models
            }
            ollama_models = []
            try:
                from codeagents.model_params import ensure_for_models
                installed = list(self.model_service.runtime.list_models())
                # Auto-create per-model param config files (won't overwrite existing).
                try:
                    ensure_for_models(installed + [m["runtime_model"] for m in registry_models])
                except Exception:
                    pass
                for name in installed:
                    if name not in registry_runtime_names:
                        ollama_models.append({
                            "key": name,
                            "display_name": name,
                            "backend": "ollama",
                            "runtime_model": name,
                            "profile": name,
                            "source": f"ollama:{name}",
                            "notes": "Installed in Ollama",
                        })
            except Exception:
                pass
            self._send_json({"models": registry_models + ollama_models})
            self._log_request("GET", {}, 200, started)
            return
        if self.path == "/inference/logs":
            self._send_json({"logs": InferenceLogger().tail()})
            self._log_request("GET", {}, 200, started)
            return
        if self.path == "/service/logs":
            self._send_json({"logs": self.request_logger.tail()})
            self._log_request("GET", {}, 200, started)
            return
        if self.path == "/chats":
            self._send_json(
                {
                    "chats": [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in self.chat_store.list()
                    ]
                }
            )
            self._log_request("GET", {}, 200, started)
            return
        if self.path.startswith("/chats/"):
            chat_id = self.path.removeprefix("/chats/").strip("/")
            chat = self.chat_store.load(chat_id)
            self._send_json({"chat": chat.model_dump(mode="json", exclude_none=True)})
            self._log_request("GET", {}, 200, started)
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
        self._log_request("GET", {}, 404, started, error="not_found")

    def do_POST(self) -> None:
        started = time.perf_counter()
        payload: dict[str, Any] = {}
        try:
            payload = self._read_json()
            if self.path == "/chat":
                if "chat" in payload:
                    chat = Chat.model_validate(payload["chat"])
                    task = payload.get("task", "general")
                    workspace = payload.get("workspace")
                    agent = self.agent
                    if workspace:
                        agent = AgentCore.from_workspace(Path(str(workspace)))
                    answer = agent.complete_chat(chat, task=str(task))
                    self.chat_store.save_assistant_reply(chat, answer)
                    self._send_json({"answer": answer, "chat_id": chat.id})
                    self._log_request("POST", payload, 200, started)
                    return
                prompt = _require_str(payload, "prompt")
                task = payload.get("task", "general")
                workspace = payload.get("workspace")
                agent = self.agent
                if workspace:
                    agent = AgentCore.from_workspace(Path(str(workspace)))
                answer = agent.chat(prompt, task=str(task))
                self._send_json({"answer": answer})
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/chats":
                title = str(payload.get("title", "New chat"))
                meta = payload.get("meta", {})
                if not isinstance(meta, dict):
                    raise ValueError("meta must be an object")
                chat = self.chat_store.create(title=title, meta=meta)
                self._send_json({"chat": chat.model_dump(mode="json", exclude_none=True)})
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/chats/save":
                chat = Chat.model_validate(payload["chat"])
                self.chat_store.save(chat)
                self._send_json({"chat": chat.model_dump(mode="json", exclude_none=True)})
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/inference/chat":
                request = InferenceRequest.model_validate(payload)
                response = self.model_service.infer(request)
                self._send_json(response.model_dump(mode="json", exclude_none=True))
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/inference/batch":
                request = BatchInferenceRequest.model_validate(payload)
                response = self.model_service.batch(request)
                self._send_json(response.model_dump(mode="json", exclude_none=True))
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/inference/start":
                key = _require_str(payload, "model")
                self._send_json(self.model_service.start(key))
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/inference/download":
                key = _require_str(payload, "model")
                output_dir = Path(str(payload["output_dir"])) if payload.get("output_dir") else None
                self._send_json(self.model_service.download(key, output_dir=output_dir))
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/inference/register":
                model = RegisteredModel.from_raw(_require_str(payload, "key"), payload)
                registered = self.model_service.register_model(model)
                self._send_json(registered.__dict__)
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/chat/stream":
                if "chat" not in payload:
                    raise ValueError("Missing 'chat' field")
                chat = Chat.model_validate(payload["chat"])
                task = payload.get("task", "general")
                workspace = payload.get("workspace")
                agent = self.agent
                if workspace:
                    agent = AgentCore.from_workspace(Path(str(workspace)))
                self._stream_ndjson(agent, chat, str(task))
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/chat/confirm":
                from codeagents.agent import submit_confirmation
                decision_id = _require_str(payload, "decision_id")
                approved = bool(payload.get("approved", False))
                remember = bool(payload.get("remember", False))
                delivered = submit_confirmation(decision_id, approved, remember=remember)
                self._send_json({"delivered": delivered})
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/tool":
                name = _require_str(payload, "name")
                arguments = payload.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                result = self.agent.call_tool(name, arguments)
                self._send_json(
                    {
                        "tool": result.tool_name,
                        "confirmation_required": result.confirmation_required,
                        "result": result.result,
                    }
                )
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/index":
                path = Path(str(payload.get("path", self.agent.workspace.root)))
                embeddings = bool(payload.get("embeddings", False))
                config = load_app_config()
                embedding_error = None
                try:
                    index = build_index(
                        path,
                        embeddings=embeddings,
                        embedding_client=self.agent.runtime if embeddings else None,
                        embedding_model=config.runtime.embedding_model if embeddings else None,
                    )
                except RuntimeErrorWithHint as exc:
                    if not embeddings:
                        raise
                    embedding_error = str(exc)
                    index = build_index(path)
                response = json.loads(index.to_json())
                response["summary"] = index_summary(Path(index.root))
                if embedding_error:
                    response["embedding_error"] = embedding_error
                self._send_json(response)
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/search":
                query = _require_str(payload, "query")
                workspace = Path(str(payload.get("workspace", self.agent.workspace.root)))
                semantic = bool(payload.get("semantic", False))
                limit = int(payload.get("limit", 10))
                config = load_app_config()
                semantic_error = None
                try:
                    results = search_index(
                        workspace,
                        query,
                        semantic=semantic,
                        embedding_client=self.agent.runtime if semantic else None,
                        embedding_model=config.runtime.embedding_model if semantic else None,
                        limit=limit,
                    )
                except RuntimeErrorWithHint as exc:
                    if not semantic:
                        raise
                    semantic_error = str(exc)
                    results = search_index(workspace, query, semantic=False, limit=limit)
                response = {"results": [result.__dict__ for result in results]}
                if semantic_error:
                    response["semantic_error"] = semantic_error
                self._send_json(response)
                self._log_request("POST", payload, 200, started)
                return
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            self._log_request("POST", payload, 404, started, error="not_found")
        except RuntimeErrorWithHint as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            self._log_request("POST", payload, 502, started, error=str(exc))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            self._log_request("POST", payload, 400, started, error=str(exc))

    def log_message(self, format: str, *args: Any) -> None:
        # Keep stdout useful for API responses and explicit logs.
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        value = json.loads(raw or "{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def _stream_ndjson(self, agent: AgentCore, chat: Chat, task: str) -> None:
        self.protocol_version = "HTTP/1.1"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        events: list[dict[str, Any]] = []
        # Persist the chat incrementally during long agent turns so users
        # don't lose work if the client disconnects, the daemon crashes, or
        # the agent times out. We rewrite the chat JSON file in two cases:
        #   1. After a "boundary" event (tool_result, tool_call, notice, done)
        #      — these mark the end of a logical block, so the snapshot is
        #      stable and worth flushing.
        #   2. Every CHAT_FLUSH_INTERVAL seconds during a delta/thinking
        #      stream, so even a single very long generation is durable.
        import time as _time

        CHAT_FLUSH_INTERVAL = 1.5  # seconds
        BOUNDARY_TYPES = {"tool_result", "tool_call", "notice", "done", "error"}
        last_flush = _time.monotonic()

        def _try_persist() -> None:
            try:
                self._persist_chat_from_events(chat, events)
            except Exception:
                # Persistence must never break the live stream.
                pass

        try:
            for event in agent.complete_chat_stream(chat, task=task):
                events.append(event)
                line = json.dumps(event, ensure_ascii=False) + "\n"
                try:
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Client went away — keep consuming so the agent finishes
                    # its tool calls cleanly, and persist what we have.
                    _try_persist()
                    return

                etype = event.get("type")
                now = _time.monotonic()
                if etype in BOUNDARY_TYPES or (now - last_flush) >= CHAT_FLUSH_INTERVAL:
                    _try_persist()
                    last_flush = now
        except Exception as exc:
            err = json.dumps({"type": "error", "message": str(exc)}) + "\n"
            try:
                self.wfile.write(err.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
        _try_persist()

    def _persist_chat_from_events(
        self, chat: Chat, events: list[dict[str, Any]]
    ) -> None:
        """Build Pydantic messages from stream events and save the full chat.

        Consecutive delta tokens are merged into a single TextContent block.
        Consecutive thinking tokens are merged into a single ThinkingContent block.
        """
        from codeagents.schemas import (
            AssistantMessage,
            FunctionCall,
            FunctionCallContent,
            FunctionContent,
            FunctionMessage,
            TextContent,
            ThinkingContent,
        )

        new_messages: list[Any] = []
        current_content: list[Any] = []
        buf_thinking = ""
        buf_text = ""
        current_model: str | None = None

        def _flush_buffers() -> None:
            nonlocal buf_thinking, buf_text
            if buf_thinking.strip():
                current_content.append(ThinkingContent(thinking=buf_thinking))
            buf_thinking = ""
            if buf_text:
                current_content.append(TextContent(text=buf_text))
            buf_text = ""

        def _flush_assistant() -> None:
            _flush_buffers()
            if current_content:
                new_messages.append(AssistantMessage(
                    index=len(chat.messages) + len(new_messages),
                    content=list(current_content),
                    model=current_model,
                ))
                current_content.clear()

        for ev in events:
            etype = ev.get("type")
            if etype == "model_info":
                current_model = ev.get("model")
            elif etype == "thinking":
                if buf_text:
                    current_content.append(TextContent(text=buf_text))
                    buf_text = ""
                buf_thinking += ev.get("content", "")
            elif etype == "delta":
                if buf_thinking.strip():
                    current_content.append(ThinkingContent(thinking=buf_thinking))
                buf_thinking = ""
                buf_text += ev.get("content", "")
            elif etype == "tool_call":
                _flush_buffers()
                name = ev.get("name", "")
                try:
                    args = json.loads(ev.get("arguments", "{}"))
                except (ValueError, TypeError):
                    args = {}
                call_id = ev.get("_id", "")
                current_content.append(FunctionCallContent(
                    function_call=FunctionCall(id=call_id, name=name, arguments=args),
                ))
            elif etype == "tool_result":
                _flush_assistant()
                new_messages.append(FunctionMessage(
                    index=len(chat.messages) + len(new_messages),
                    content=[FunctionContent(function=ev.get("result", ""))],
                    name=ev.get("name"),
                    function_call_id=ev.get("_id", ""),
                ))
            elif etype == "done":
                if not current_model:
                    current_model = ev.get("model")

        _flush_assistant()

        meta = dict(chat.meta or {})
        if current_model:
            meta["last_model"] = current_model

        if new_messages:
            saved = Chat(
                id=chat.id,
                messages=[*chat.messages, *new_messages],
                meta=meta,
                functions=chat.functions,
            )
            self.chat_store.save(saved)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log_request(
        self,
        method: str,
        payload: dict[str, Any],
        status: int,
        started: float,
        *,
        error: str | None = None,
    ) -> None:
        self.request_logger.record(
            ServiceRequestLogEntry(
                service="codeagents-api",
                path=self.path,
                method=method,
                payload=payload,
                status=status,
                error=error,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        )


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(*, host: str, port: int, workspace: Path) -> None:
    agent = AgentCore.from_workspace(workspace)

    class Handler(AgentRequestHandler):
        pass

    Handler.agent = agent
    Handler.model_service = LocalModelService()
    Handler.chat_store = ChatStore(agent.workspace.root)
    Handler.request_logger = ServiceRequestLogger()

    try:
        from codeagents.model_params import ensure_for_models, PARAMS_DIR

        registry_names = [m.runtime_model for m in Handler.model_service.list_models()]
        ollama_names: list[str] = []
        try:
            ollama_names = list(Handler.model_service.runtime.list_models())
        except Exception:
            pass
        ensure_for_models(registry_names + ollama_names)
        print(f"Model params dir: {PARAMS_DIR}")
    except Exception as exc:
        print(f"Warning: failed to ensure model param files: {exc}")
    server = ReusableThreadingHTTPServer((host, port), Handler)
    print(f"CodeAgents API listening on http://{host}:{port}")
    print(f"Workspace: {agent.workspace.root}")

    def stop(signum: int, _frame: Any) -> None:
        print(f"\nReceived signal {signum}; shutting down CodeAgents API...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    previous_sigint = signal.signal(signal.SIGINT, stop)
    previous_sigterm = signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        print("CodeAgents API stopped.")


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required string field: {key}")
    return value
