from __future__ import annotations

import json
import mimetypes
import os
import signal
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from codeagents.agent import AgentCore
from codeagents.chat_attachments import save_chat_base64_upload
from codeagents.chat_store import ChatStore
from codeagents.plan_store import (
    PlanLimitError,
    PlanNotFoundError,
    PlanStore,
)
from codeagents.config import load_app_config
from codeagents.indexer import build_index, index_summary, search_index
from codeagents.inference_log import InferenceLogger
from codeagents.model_service import LocalModelService, RegisteredModel
from codeagents.request_log import ServiceRequestLogger, ServiceRequestLogEntry
from codeagents.resource_metrics import collect_resource_snapshot
from codeagents.runtime import RuntimeErrorWithHint
from codeagents.stream_events import StreamErrorEvent, stream_event_to_json
from codeagents.schemas import BatchInferenceRequest, Chat, InferenceRequest


def cors_origins_from_env() -> frozenset[str]:
    """Origins allowed for browser clients (Vite dev server, etc.).

    Set ``CODEAGENTS_CORS_ORIGINS`` to a comma-separated list. Empty string
    disables CORS reflection (CLI/TUI clients do not send ``Origin``).
    """
    raw = os.environ.get(
        "CODEAGENTS_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class AgentRequestHandler(BaseHTTPRequestHandler):
    # Disable output buffering so NDJSON events flush immediately.
    wbufsize = 0

    agent: AgentCore
    model_service: LocalModelService
    chat_store: ChatStore
    plan_store: PlanStore
    request_logger: ServiceRequestLogger
    allowed_cors_origins: frozenset[str] = frozenset()
    gui_static_dir: Path | None = None

    def _serve_gui_static(self, started: float) -> bool:
        """Serve the built SPA from ``Handler.gui_static_dir`` under ``/ui/``."""
        base = type(self).gui_static_dir
        if base is None:
            return False
        root = Path(base)
        if not root.is_dir():
            return False
        req_path = self.path.split("?", 1)[0]
        if req_path == "/ui":
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", "/ui/")
            self.end_headers()
            self._log_request("GET", {}, 301, started)
            return True
        if not req_path.startswith("/ui/"):
            return False
        rel = req_path.removeprefix("/ui/").strip()
        if not rel or rel.endswith("/"):
            rel = "index.html"
        root_r = root.resolve()
        try:
            candidate = (root_r / rel).resolve()
            candidate.relative_to(root_r)
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            self._log_request("GET", {}, 404, started, error="gui_bad_path")
            return True
        if candidate.is_file():
            return self._send_static_file(candidate, started)
        index = root_r / "index.html"
        if index.is_file():
            return self._send_static_file(index, started)
        self.send_error(HTTPStatus.NOT_FOUND)
        self._log_request("GET", {}, 404, started, error="gui_missing")
        return True

    def _send_static_file(self, path: Path, started: float) -> bool:
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype = f"{ctype}; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)
        self._log_request("GET", {}, 200, started)
        return True

    def end_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin in self.allowed_cors_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PATCH, DELETE, OPTIONS",
            )
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type",
            )
            self.send_header("Access-Control-Max-Age", "86400")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        started = time.perf_counter()
        self._req_started = started
        if self._serve_gui_static(started):
            return
        from codeagents.surfaces.http.router import dispatch

        if dispatch(self, self._GET_ROUTES, "GET", self.path):
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
        self._log_request("GET", {}, 404, started, error="not_found")

    # ── GET route handlers ─────────────────────────────────────────────
    # The route table at the bottom of this class maps each path to one
    # of these methods. Add a new endpoint by:
    #   1. writing a ``_get_<name>`` method here,
    #   2. registering a ``Route("GET", "_get_<name>", path=..., prefix=...)``
    #      in :data:`_GET_ROUTES`.

    def _get_health(self) -> None:
        from codeagents import __version__ as _v

        self._send_json({"ok": True, "version": _v})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_version(self) -> None:
        from codeagents import __version__ as _v

        self._send_json({"version": _v})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_models(self) -> None:
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
        self._log_request("GET", {}, 200, self._req_started)

    def _get_tools(self) -> None:
        # Returns BOTH:
        #   ``tools``: flat list of every registered tool (legacy
        #             diagnostic shape, includes disabled ones).
        #   ``modes``: per-mode view (agent / plan / ask / research) of the
        #             exact tool surface the model sees, with parameter
        #             schemas. Consumed by the GUI command palette to
        #             render "Available tools" → mode → tool → details.
        from codeagents.agent import _allowed_permissions_for_mode

        payload: dict[str, Any] = {
            "tools": [
                {
                    "name": tool.name,
                    "kind": tool.kind,
                    "permission": tool.permission.value,
                    "enabled": tool.enabled,
                    "description": tool.description,
                }
                for tool in self.agent.tools.list(include_disabled=True)
            ],
            "modes": {},
        }
        for mode_name in ("agent", "plan", "ask", "research"):
            allowed = _allowed_permissions_for_mode(mode_name)
            specs = self.agent._agent_tools_as_specs(
                allowed_permissions=allowed, mode=mode_name
            )
            tools_payload: list[dict[str, Any]] = []
            for spec in specs:
                ts = self.agent.tools.get(spec.name)
                tools_payload.append(
                    {
                        "name": spec.name,
                        "description": spec.description or "",
                        "permission": ts.permission.value,
                        "kind": ts.kind,
                        "parameters": [
                            {
                                "name": p.name,
                                "type": (p.schema_ or {}).get("type", "string"),
                                "description": p.description or "",
                                "required": bool(p.required),
                                "enum": list((p.schema_ or {}).get("enum") or []) or None,
                            }
                            for p in spec.parameters
                        ],
                    }
                )
            payload["modes"][mode_name] = tools_payload
        self._send_json(payload)
        self._log_request("GET", {}, 200, self._req_started)

    def _get_modes(self) -> None:
        # Static descriptors: tool whitelists, allowed permissions and UI
        # accent colours. Consumed by the GUI to replace the hard-coded
        # ``MODES`` / ``MODE_COLORS`` constants.
        from codeagents.core.modes import list_modes

        self._send_json({"modes": list_modes()})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_inference_models(self) -> None:
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
        registry_runtime_names = {m["runtime_model"] for m in registry_models}
        ollama_models = []
        try:
            from codeagents.model_params import ensure_for_models
            installed = list(self.model_service.runtime.list_models())
            try:
                ensure_for_models(
                    installed + [m["runtime_model"] for m in registry_models]
                )
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
        self._log_request("GET", {}, 200, self._req_started)

    def _get_inference_logs(self) -> None:
        self._send_json({"logs": InferenceLogger().tail()})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_service_logs(self) -> None:
        self._send_json({"logs": self.request_logger.tail()})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_metrics_resources(self) -> None:
        self._send_json(
            collect_resource_snapshot(workspace_root=self.agent.workspace.root)
        )
        self._log_request("GET", {}, 200, self._req_started)

    def _get_metrics_history(self) -> None:
        from codeagents.metrics_sampler import get_global_sampler

        sampler = get_global_sampler(
            jsonl_path=self.agent.workspace.root / ".codeagents" / "metrics.jsonl"
        )
        self._send_json({"samples": sampler.history()})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_metrics_stream(self) -> None:
        self._stream_metrics_ndjson(self._req_started)

    def _get_budget_preview(self) -> None:
        # Phase 2.A.5: report last observed prompt_tokens and a quick
        # estimate for the next turn so the GUI can show
        # ``tokens: last X · next ~Y / ctx``.
        last = int(getattr(self.agent, "_last_prompt_tokens", 0) or 0)
        est = int(getattr(self.agent, "_last_estimate", 0) or 0)
        cw = int(getattr(self.agent, "_last_context_window", 0) or 0)
        model = getattr(self.agent, "_last_model", "") or ""
        if cw == 0 and model:
            try:
                cw = self.agent.token_budget.context_window(model)
            except Exception:
                cw = 0
        cal = {}
        if model:
            try:
                cal = self.agent.token_budget.calibration(model)
            except Exception:
                cal = {}
        self._send_json(
            {
                "model": model,
                "last_prompt_tokens": last,
                "estimated_next": est,
                "context_window": cw,
                "warn": bool(cw > 0 and (max(last, est) > cw * 0.85)),
                "calibration": cal,
            }
        )
        self._log_request("GET", {}, 200, self._req_started)

    def _get_research(self) -> None:
        # ``GET /research/<chat_id>`` -> list of reports.
        # ``GET /research/<chat_id>/<rid>`` -> a specific report + markdown.
        tail = self.path.removeprefix("/research/").strip("/").split("/")
        if not (1 <= len(tail) <= 2 and tail[0]):
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            self._log_request("GET", {}, 404, self._req_started, error="not_found")
            return
        chat_id = tail[0]
        from codeagents.chat_store import default_chats_dir
        from codeagents.research_store import ResearchStore

        rstore = ResearchStore(default_chats_dir())
        if len(tail) == 1:
            items = [r.to_dict() for r in rstore.list(chat_id)]
            self._send_json({"chat_id": chat_id, "reports": items})
            self._log_request("GET", {}, 200, self._req_started)
            return
        report_id = tail[1]
        try:
            rep = rstore.load(chat_id, report_id)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "report not found")
            return
        markdown = rstore.read_markdown(chat_id, report_id)
        self._send_json({"report": rep.to_dict(), "markdown": markdown})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_chats_list(self) -> None:
        self._send_json(
            {
                "chats": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in self.chat_store.list()
                ]
            }
        )
        self._log_request("GET", {}, 200, self._req_started)

    def _get_chat_one(self) -> None:
        chat_id = self.path.removeprefix("/chats/").strip("/")
        chat = self.chat_store.load(chat_id)
        self._send_json({"chat": chat.model_dump(mode="json", exclude_none=True)})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_plans_list(self) -> None:
        from urllib.parse import parse_qs, urlsplit

        q = parse_qs(urlsplit(self.path).query)
        status_filter = (q.get("status") or ["all"])[0].lower()
        chat_filter = (q.get("chat_id") or [""])[0]
        plans = self.plan_store.list()
        if status_filter == "active":
            from codeagents.plan_store import ACTIVE_STATUSES
            plans = [p for p in plans if p.status in ACTIVE_STATUSES]
        elif status_filter in {"draft", "building", "completed", "rejected"}:
            plans = [p for p in plans if p.status == status_filter]
        if chat_filter:
            plans = [p for p in plans if p.chat_id == chat_filter]
        self._send_json({"plans": [p.to_dict() for p in plans]})
        self._log_request("GET", {}, 200, self._req_started)

    def _get_plan_one(self) -> None:
        tail = self.path.removeprefix("/plans/").strip("/")
        if tail.endswith("/markdown"):
            plan_id = tail[: -len("/markdown")].strip("/")
            try:
                plan = self.plan_store.load(plan_id)
            except PlanNotFoundError:
                self._send_json({"error": "plan_not_found"}, status=HTTPStatus.NOT_FOUND)
                self._log_request(
                    "GET", {}, 404, self._req_started, error="plan_not_found"
                )
                return
            self._send_json({"id": plan.id, "markdown": plan.to_markdown()})
            self._log_request("GET", {}, 200, self._req_started)
            return
        try:
            plan = self.plan_store.load(tail)
        except PlanNotFoundError:
            self._send_json({"error": "plan_not_found"}, status=HTTPStatus.NOT_FOUND)
            self._log_request(
                "GET", {}, 404, self._req_started, error="plan_not_found"
            )
            return
        self._send_json({"plan": plan.to_dict()})
        self._log_request("GET", {}, 200, self._req_started)

    # ── POST/PATCH/DELETE ──────────────────────────────────────────────
    # These methods still use an inline ``if/elif`` dispatch chain. They
    # mostly route to a single endpoint (``/chat``, ``/chats/<id>``,
    # ``/plans/<id>``) with substantial inline logic; converting them to
    # the same router-table pattern as :meth:`do_GET` is a follow-up
    # pass once those bodies have been extracted into helper methods.

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
            if self.path.startswith("/research/") and self.path.endswith("/cancel"):
                # Phase 2.B.4: POST /research/<chat>/<rid>/cancel.
                trimmed = self.path[len("/research/"):-len("/cancel")].strip("/").split("/")
                if len(trimmed) == 2 and all(trimmed):
                    chat_id, report_id = trimmed
                    from codeagents.chat_store import default_chats_dir
                    from codeagents.research_store import ResearchStore

                    rstore = ResearchStore(default_chats_dir())
                    try:
                        rep = rstore.set_status(chat_id, report_id, "cancelled")
                    except FileNotFoundError:
                        self.send_error(HTTPStatus.NOT_FOUND, "report not found")
                        return
                    self._send_json({"report": rep.to_dict()})
                    self._log_request("POST", payload, 200, started)
                    return
            if self.path.startswith("/plans/") and self.path.endswith("/reject"):
                plan_id = self.path[len("/plans/"):-len("/reject")].strip("/")
                if not plan_id:
                    raise ValueError("Missing plan id in path")
                try:
                    plan = self.plan_store.reject(plan_id)
                except PlanNotFoundError:
                    self._send_json({"error": "plan_not_found"}, status=HTTPStatus.NOT_FOUND)
                    self._log_request("POST", payload, 404, started, error="plan_not_found")
                    return
                self._send_json({"plan": plan.to_dict()})
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/chats/save":
                chat = Chat.model_validate(payload["chat"])
                self.chat_store.save(chat)
                self._send_json({"chat": chat.model_dump(mode="json", exclude_none=True)})
                self._log_request("POST", payload, 200, started)
                return
            if self.path.startswith("/chats/") and self.path.endswith("/title"):
                chat_id = self.path[len("/chats/"):-len("/title")].strip("/")
                if not chat_id:
                    raise ValueError("Missing chat id in path")
                prompt = _require_str(payload, "prompt")
                model_name = payload.get("model")
                title = self._generate_chat_title(
                    prompt,
                    model_name if isinstance(model_name, str) else None,
                )
                patched = self.chat_store.update_meta(chat_id, title=title)
                self._send_json({
                    "title": title,
                    "chat": patched.model_dump(mode="json", exclude_none=True),
                })
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
                if "mode" in payload and isinstance(payload["mode"], str):
                    chat = chat.model_copy(
                        update={"meta": {**chat.meta, "mode": payload["mode"]}}
                    )
                task = payload.get("task", "general")
                workspace = payload.get("workspace")
                # Empty/missing workspace: use ~/CodeAgents (created on demand).
                # We avoid $HOME because the agent would then wander into
                # ~/Documents / ~/Desktop / ~/Downloads, which are TCC-protected
                # on macOS and trigger a wall of permission prompts.
                if not workspace:
                    default_ws = Path.home() / "CodeAgents"
                    default_ws.mkdir(parents=True, exist_ok=True)
                    workspace = str(default_ws)
                agent = AgentCore.from_workspace(Path(str(workspace)))
                self._stream_ndjson(agent, chat, str(task))
                self._log_request("POST", payload, 200, started)
                return
            if self.path == "/chat/upload":
                filename = _require_str(payload, "filename")
                raw_b64 = payload.get("content_base64")
                if not isinstance(raw_b64, str):
                    raise ValueError("content_base64 must be a string")
                sub = str(payload.get("subdir", "uploads"))
                ws = self.agent.workspace.root
                out = save_chat_base64_upload(
                    ws,
                    filename=filename,
                    content_base64=raw_b64,
                    subdir=sub,
                )
                self._send_json(out)
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
            if self.path == "/index/refresh":
                worker = getattr(type(self), "index_worker", None)
                if worker is None:
                    self._send_json({"refreshed": False, "reason": "no_worker"})
                    self._log_request("POST", payload, 200, started)
                    return
                worker.request_refresh()
                self._send_json({"refreshed": True, "status": worker.status()})
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

    def do_PATCH(self) -> None:
        started = time.perf_counter()
        payload: dict[str, Any] = {}
        try:
            payload = self._read_json()
            if self.path.startswith("/chats/"):
                chat_id = self.path.removeprefix("/chats/").strip("/")
                if not chat_id:
                    raise ValueError("Missing chat id in path")
                title = payload.get("title")
                meta = payload.get("meta")
                if title is not None and not isinstance(title, str):
                    raise ValueError("title must be a string")
                if meta is not None and not isinstance(meta, dict):
                    raise ValueError("meta must be an object")
                chat = self.chat_store.update_meta(
                    chat_id,
                    title=title if isinstance(title, str) else None,
                    meta=meta if isinstance(meta, dict) else None,
                )
                self._send_json({"chat": chat.model_dump(mode="json", exclude_none=True)})
                self._log_request("PATCH", payload, 200, started)
                return
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            self._log_request("PATCH", payload, 404, started, error="not_found")
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc) or "chat_not_found"}, status=HTTPStatus.NOT_FOUND)
            self._log_request("PATCH", payload, 404, started, error=str(exc))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            self._log_request("PATCH", payload, 400, started, error=str(exc))

    def do_DELETE(self) -> None:
        started = time.perf_counter()
        try:
            if self.path.startswith("/plans/"):
                plan_id = self.path.removeprefix("/plans/").strip("/")
                if not plan_id:
                    raise ValueError("Missing plan id in path")
                removed = self.plan_store.delete(plan_id)
                if not removed:
                    self._send_json({"error": "plan_not_found"}, status=HTTPStatus.NOT_FOUND)
                    self._log_request("DELETE", {}, 404, started, error="plan_not_found")
                    return
                self._send_json({"deleted": plan_id})
                self._log_request("DELETE", {}, 200, started)
                return
            if self.path.startswith("/chats/"):
                chat_id = self.path.removeprefix("/chats/").strip("/")
                if not chat_id:
                    raise ValueError("Missing chat id in path")
                removed = self.chat_store.delete(chat_id)
                if not removed:
                    self._send_json({"error": "chat_not_found"}, status=HTTPStatus.NOT_FOUND)
                    self._log_request("DELETE", {}, 404, started, error="chat_not_found")
                    return
                self._send_json({"deleted": chat_id})
                self._log_request("DELETE", {}, 200, started)
                return
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            self._log_request("DELETE", {}, 404, started, error="not_found")
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            self._log_request("DELETE", {}, 400, started, error=str(exc))

    def _generate_chat_title(self, prompt: str, model_name: str | None = None) -> str:
        """Ask the chat's active model for a short 3-5 word title.

        Bypasses the agent's tool-loop and turns reasoning off via
        ``reasoning_effort=none`` (Ollama OpenAI-compat) so naming stays
        cheap even on thinking models. If the call fails, falls back to a
        truncated version of the user prompt so the GUI never blocks.
        """
        from codeagents.schemas import (
            Chat as _Chat,
            SystemMessage,
            TextContent,
            UserMessage,
        )

        system_text = (
            "You generate ultra-short chat titles. "
            "Reply with a single line of 3 to 5 words in the user's language. "
            "No quotes, no trailing punctuation, no markdown. Title-case it. "
            "Do not think out loud; output only the title."
        )
        user_text = prompt.strip()[:500]
        title_chat = _Chat(
            messages=[
                SystemMessage(index=0, content=[TextContent(text=system_text)]),
                UserMessage(index=1, content=[TextContent(text=user_text)]),
            ],
            meta={"task": "general", "mode": "ask"},
        )
        answer = ""
        try:
            # Resolve to the model the GUI is currently using; ``for_task``
            # transparently builds an ad-hoc profile for any installed
            # Ollama model name passed as ``task``.
            profile = self.agent.router.for_task(model_name or "general")
            answer = self.agent.runtime.chat(
                model=profile,
                chat=title_chat,
                reasoning_effort="none",
            )
        except Exception:
            answer = ""
        title = _normalize_title(answer) or _normalize_title(user_text) or "New chat"
        return title

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
                row = stream_event_to_json(event)
                events.append(row)
                line = json.dumps(row, ensure_ascii=False) + "\n"
                try:
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Client went away — keep consuming so the agent finishes
                    # its tool calls cleanly, and persist what we have.
                    _try_persist()
                    return

                etype = row.get("type")
                now = _time.monotonic()
                if etype in BOUNDARY_TYPES or (now - last_flush) >= CHAT_FLUSH_INTERVAL:
                    _try_persist()
                    last_flush = now
        except Exception as exc:
            err = json.dumps(
                stream_event_to_json(StreamErrorEvent(message=str(exc))),
                ensure_ascii=False,
            ) + "\n"
            try:
                self.wfile.write(err.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
        _try_persist()

    def _stream_metrics_ndjson(self, started: float) -> None:
        """NDJSON stream for ``GET /metrics/stream`` — one snapshot per second.

        Writes are best-effort: any pipe error breaks the loop cleanly so the
        connection slot is freed.
        """

        from codeagents.metrics_sampler import get_global_sampler, stream_snapshots

        sampler = get_global_sampler(
            jsonl_path=self.agent.workspace.root / ".codeagents" / "metrics.jsonl"
        )
        stop = threading.Event()
        self.protocol_version = "HTTP/1.1"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for snap in stream_snapshots(sampler, stop=stop):
                line = json.dumps(snap, ensure_ascii=False) + "\n"
                try:
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            stop.set()
        self._log_request("GET", {}, 200, started)

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
            self._index_new_messages_async(saved, new_messages)

    def _index_new_messages_async(self, chat: Chat, new_messages: list[Any]) -> None:
        """Embed user/assistant text from ``new_messages`` into the chat RAG store.

        Fire-and-forget: a worker thread does the actual embed call so the
        HTTP stream response isn't blocked. Failures are silent — recall
        will simply return fewer hits.
        """

        if not chat.id:
            return

        def _run() -> None:
            try:
                from codeagents.chat_rag import index_pending_chat_messages
                from codeagents.chat_store import default_chats_dir
                from codeagents.config import load_app_config
                from codeagents.runtime import OpenAICompatibleRuntime
                from codeagents.schemas import (
                    AssistantMessage,
                    TextContent,
                    UserMessage,
                )

                pending: list[tuple[int, str, str]] = []
                for msg in new_messages:
                    if not isinstance(msg, (UserMessage, AssistantMessage)):
                        continue
                    body_parts: list[str] = []
                    for block in msg.content:
                        if isinstance(block, TextContent):
                            body_parts.append(block.text)
                    text = "\n".join(p for p in body_parts if p).strip()
                    if not text:
                        continue
                    role = "user" if isinstance(msg, UserMessage) else "assistant"
                    pending.append((msg.index, role, text))
                if not pending:
                    return
                cfg = load_app_config()
                embedder = OpenAICompatibleRuntime(cfg.runtime)
                chat_dir = default_chats_dir() / chat.id
                index_pending_chat_messages(
                    chat_dir=chat_dir,
                    messages=pending,
                    embedding_client=embedder,
                    embedding_model=cfg.runtime.embedding_model,
                )
            except Exception:
                # Embedding the chat is best-effort. Swallow any failure
                # (network, missing model, OOM) so the user-facing reply is
                # never delayed by a recall-only optimisation.
                return

        threading.Thread(target=_run, daemon=True).start()

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


# ── HTTP route table ───────────────────────────────────────────────────
# Single source of truth for "where does GET <path> dispatch?". The
# router walks this list in order; the first matching route wins. POST,
# PATCH and DELETE still use an inline if/elif chain in their respective
# ``do_*`` methods (slated for the same treatment in a follow-up pass).

from codeagents.surfaces.http.router import Route as _Route  # noqa: E402

AgentRequestHandler._GET_ROUTES = (  # type: ignore[attr-defined]
    _Route("GET", "_get_health", path="/health"),
    _Route("GET", "_get_version", path="/version"),
    _Route("GET", "_get_models", path="/models"),
    _Route("GET", "_get_tools", path="/tools"),
    _Route("GET", "_get_modes", path="/modes"),
    _Route("GET", "_get_inference_models", path="/inference/models"),
    _Route("GET", "_get_inference_logs", path="/inference/logs"),
    _Route("GET", "_get_service_logs", path="/service/logs"),
    _Route("GET", "_get_metrics_resources", path="/metrics/resources"),
    _Route("GET", "_get_metrics_history", path="/metrics/history"),
    _Route("GET", "_get_metrics_stream", path="/metrics/stream"),
    _Route("GET", "_get_budget_preview", prefix="/budget/preview"),
    _Route("GET", "_get_research", prefix="/research/"),
    _Route("GET", "_get_chats_list", path="/chats"),
    _Route("GET", "_get_chat_one", prefix="/chats/"),
    _Route("GET", "_get_plans_list", path="/plans"),
    _Route("GET", "_get_plan_one", prefix="/plans/"),
)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(
    *,
    host: str,
    port: int,
    workspace: Path,
    gui_dir: Path | None = None,
) -> None:
    agent = AgentCore.from_workspace(workspace)

    class Handler(AgentRequestHandler):
        pass

    Handler.agent = agent
    Handler.model_service = LocalModelService()
    # Single global library — shared between TUI / GUI / SDK regardless of
    # the agent's workspace. Override via ``CODEAGENTS_CHATS_DIR`` env var.
    Handler.chat_store = ChatStore.global_default()
    Handler.plan_store = PlanStore.global_default()
    Handler.request_logger = ServiceRequestLogger()
    Handler.allowed_cors_origins = cors_origins_from_env()
    gui_path: Path | None = gui_dir
    if gui_path is None and os.environ.get("CODEAGENTS_GUI_DIR"):
        gui_path = Path(os.environ["CODEAGENTS_GUI_DIR"])
    if gui_path is not None:
        gui_path = gui_path.expanduser().resolve()
        if not gui_path.is_dir():
            print(f"Warning: --gui-dir is not a directory, ignoring: {gui_path}")
            gui_path = None
        elif not (gui_path / "index.html").is_file():
            print(f"Warning: no index.html in GUI dir, ignoring: {gui_path}")
            gui_path = None
    Handler.gui_static_dir = gui_path

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
    try:
        from codeagents.metrics_sampler import get_global_sampler

        get_global_sampler(
            jsonl_path=workspace / ".codeagents" / "metrics.jsonl"
        )
    except Exception as exc:
        print(f"Warning: metrics sampler did not start: {exc}")
    try:
        from codeagents.index_worker import WorkspaceIndexWorker

        index_worker = WorkspaceIndexWorker(workspace=workspace)
        index_worker.start()
        Handler.index_worker = index_worker  # type: ignore[attr-defined]
    except Exception as exc:
        print(f"Warning: background indexer did not start: {exc}")
    server = ReusableThreadingHTTPServer((host, port), Handler)
    print(f"CodeAgents API listening on http://{host}:{port}")
    print(f"Workspace: {agent.workspace.root}")
    if gui_path is not None:
        print(f"Web UI: http://{host}:{port}/ui/")

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


def _normalize_title(raw: str) -> str:
    """Normalize a model-produced chat title: first non-empty line, max 5 words."""
    if not raw:
        return ""
    line = ""
    for candidate in raw.splitlines():
        candidate = candidate.strip().strip("\"'`*_#-").strip()
        if candidate:
            line = candidate
            break
    if not line:
        return ""
    while line and line[-1] in ".,;:!?…":
        line = line[:-1].rstrip()
    words = line.split()
    if len(words) > 5:
        words = words[:5]
    return " ".join(words)
