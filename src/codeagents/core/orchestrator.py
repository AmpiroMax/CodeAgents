from __future__ import annotations

import queue
import shlex
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codeagents.core.routing import (
    CODING_TASKS,
    RESEARCH_STAGE_BY_TOOL as _RESEARCH_STAGE_BY_TOOL,
    ConfirmationDecision,
    ToolCallResult,
    _PENDING_DECISIONS,
    allowed_permissions_for_mode as _allowed_permissions_for_mode,
    has_shell_metacharacters as _has_shell_metacharacters,
    submit_confirmation,
    summarize_result as _summarize_result,
)
from codeagents.observability.audit import AuditLog
from codeagents.core.config import PROJECT_ROOT, AppConfig, load_app_config
from codeagents.core.runtime.router import ModelRouter
from codeagents.core.permissions import (
    Permission,
    PermissionPolicy,
    WorkspaceApprovalStore,
    load_permission_policy,
)
from codeagents.core.runtime.openai_client import OpenAICompatibleRuntime
from codeagents.core.schemas import (
    Chat,
    FunctionParameter,
    FunctionSpec,
    function_parameters_from_json_schema,
    merge_chat_meta,
    SystemMessage,
    TextContent,
)
from codeagents.core.stream_events import (
    AgentStreamEvent,
    StreamDeltaEvent,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamModelInfoEvent,
    StreamNoticeEvent,
    StreamContextUsageEvent,
    StreamThinkingEvent,
    StreamToolCallDeltaEvent,
    StreamToolCallEvent,
    StreamToolCallStartEvent,
    StreamToolPendingEvent,
    StreamToolResultEvent,
    StreamResearchProgressEvent,
)


from codeagents.core.conversation.auto_recall import maybe_recall
from codeagents.surfaces.mcp.bridge import register_mcp_tools
from codeagents.core.modes import filter_for_mode
from codeagents.core.conversation.summarisation import collapse_messages, needs_summary
from codeagents.core.budget.token_counter import TokenBudget
from codeagents.tools import (
    NATIVE_TOOL_SPECS,
    ToolRegistry,
    ToolSpec,
    register_all_native_tools,
    register_native_specs,
)
from codeagents.core.workspace import Workspace
from codeagents.rag.workspace_index import build_index, index_summary, search_index


# ─── System prompt ───────────────────────────────────────────────────
# The base prompt and per-mode addendums moved to
# ``registry/prompts/modes/<mode>.json`` (Stage 3); see
# :func:`codeagents.core.modes.prompts.resolve_prompt`. Only the
# situational plan-execution addendum is still defined in code.

from codeagents.core.conversation.policies import EXECUTE_PLAN_SYSTEM_ADDENDUM

# TODO(stage-5 follow-up): the body of :meth:`AgentCore.complete_chat_stream`
# is still ~700 LOC. The natural next move is to extract it into
# ``core/conversation/loop.py`` with a small ``ConversationContext``
# dataclass capturing the per-turn state (chat, mode, model, audit,
# token budget, runtime). Skipped here because the method threads
# dozens of ``self.*`` references and a safe carve-out warrants its
# own focused PR.

class AgentCore:
    def __init__(
        self,
        *,
        workspace: Workspace,
        config: AppConfig | None = None,
        policy: PermissionPolicy | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config or load_app_config()
        self.policy = policy or load_permission_policy(
            PROJECT_ROOT / "registry" / "permissions.toml"
        )
        self.approvals = WorkspaceApprovalStore(workspace.root)
        self.router = ModelRouter(self.config)
        self.runtime = OpenAICompatibleRuntime(self.config.runtime)
        self.audit = AuditLog(workspace.root / ".codeagents" / "audit.jsonl")
        self.lsp = self._build_lsp_manager(workspace)
        self.tools = ToolRegistry()
        register_native_specs(self.tools, NATIVE_TOOL_SPECS)
        register_all_native_tools(self.tools, workspace, lsp=self.lsp)
        register_mcp_tools(self.tools, PROJECT_ROOT / "registry" / "mcp.toml")
        self._tool_specs_cache: dict[str | None, list[FunctionSpec]] = {}
        self.token_budget = TokenBudget.for_workspace(workspace.root)
        # Last observed real prompt_tokens, surfaced via /budget/preview
        # so the GUI can show "tokens: last X · next ~Y / ctx".
        self._last_prompt_tokens: int = 0
        self._last_context_window: int = 0
        self._last_estimate: int = 0
        self._last_model: str = ""
        # React to change_workspace tool calls: refresh per-workspace state.
        workspace.on_root_change.append(self._on_workspace_root_change)

    def _on_workspace_root_change(self, workspace: Workspace) -> None:
        """Refresh per-workspace state after change_workspace."""
        self.approvals = WorkspaceApprovalStore(workspace.root)
        self.audit = AuditLog(workspace.root / ".codeagents" / "audit.jsonl")
        self._tool_specs_cache.clear()
        try:
            old = getattr(self, "lsp", None)
            if old is not None:
                old.shutdown_all()
        except Exception:
            pass
        self.lsp = self._build_lsp_manager(workspace)

    @staticmethod
    def _build_lsp_manager(workspace: Workspace):
        """Construct an :class:`LspManager` for ``workspace`` (None on failure)."""
        try:
            from codeagents.lsp import LspManager, load_lsp_servers_for_project

            entries = load_lsp_servers_for_project(PROJECT_ROOT)
            if not entries:
                return None
            return LspManager(workspace.root, entries)
        except Exception:
            return None

    def shutdown(self) -> None:
        """Best-effort cleanup; safe to call multiple times."""
        try:
            if getattr(self, "lsp", None) is not None:
                self.lsp.shutdown_all()
        except Exception:
            pass

    def reroot(self, path: Path | str) -> Workspace:
        """Public helper to switch the workspace root programmatically."""
        self.workspace.change_root(path)
        return self.workspace

    @classmethod
    def from_workspace(cls, path: Path | str = ".") -> "AgentCore":
        return cls(workspace=Workspace.from_path(path))

    def chat(self, prompt: str, *, task: str | None = "general") -> str:
        from codeagents.core.modes.prompts import resolve_prompt

        model = self.router.for_task(task)
        enriched_prompt = self._with_workspace_context(prompt, task=task)
        chat = Chat.from_prompt(
            enriched_prompt,
            system=resolve_prompt("agent", getattr(model, "name", None)),
            meta={"task": task or "general"},
        )
        return self.runtime.chat(model=model, chat=chat)

    def infer_chat(
        self,
        chat: Chat,
        *,
        task: str | None = "general",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        model = self.router.for_task(task)
        return self.runtime.infer(
            model=model,
            chat=chat,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def complete_chat(self, chat: Chat, *, task: str | None = "general") -> str:
        model = self.router.for_task(task)
        if task in CODING_TASKS:
            chat = self._chat_with_workspace_context(chat)
        return self.runtime.chat(model=model, chat=chat)

    def complete_chat_stream(
        self, chat: Chat, *, task: str | None = "general"
    ) -> Generator[AgentStreamEvent, None, None]:
        """Stream tokens with automatic tool-calling loop.

        Events are forwarded in real-time for responsive streaming.
        If the turn ends with tool calls, we execute them and continue.
        The TUI handles visual reclassification of deltas as thinking
        when tool_calls follow.
        """
        import json as _json

        model = self.router.for_task(task)
        model_name = model.name
        if task in CODING_TASKS:
            chat = self._chat_with_workspace_context(chat)
        # Pin the active chat id onto the workspace so plan tools (and any
        # future per-chat side-effects) write into <chats_dir>/<chat_id>/plans/
        # without forcing the model to thread chat_id through every call.
        self.workspace.chat_id = chat.id or ""
        meta = merge_chat_meta(chat.meta)
        mode = meta.mode
        if mode == "general":
            mode = "agent"
        allowed = _allowed_permissions_for_mode(mode)
        tools = (
            list(chat.functions)
            if chat.functions
            else self._agent_tools_as_specs(allowed_permissions=allowed, mode=mode)
        )
        chat = self._ensure_system_prompt(chat, mode=mode, model_name=model_name)

        messages = chat.to_openai_messages()
        tool_schemas = [t.to_json_schema() for t in tools] if tools else None

        # ── Auto chat-RAG (Phase 2.A.3) ──
        # On every turn, if history is long enough, ask the per-chat
        # embedding store for relevant earlier messages and inject them
        # as a hidden ``system`` block right before the latest user msg.
        try:
            self._inject_auto_recall(messages)
        except Exception:
            pass

        # ── Summarisation cap (Phase 2.A.4) ──
        # If our pre-call estimate exceeds 0.85 * ctx_window we collapse
        # everything between the head system block and the recent tail
        # into a single "[summary vN]" system message.
        try:
            ctx_window = self.token_budget.context_window(model_name)
            estimate = self.token_budget.estimate(
                model=model_name, messages=messages, tools=tool_schemas
            )
            self._last_estimate = estimate
            self._last_context_window = ctx_window
            self._last_model = model_name
            if needs_summary(estimated_tokens=estimate, ctx_window=ctx_window):
                result = collapse_messages(
                    messages,
                    summarise=lambda corpus: self._summarise_corpus(corpus, model=model),
                )
                if result.applied:
                    messages = result.new_messages
                    yield StreamNoticeEvent(
                        level="info",
                        message=(
                            f"context cap hit ({estimate}/{ctx_window} tokens) — "
                            f"collapsed {result.dropped} older messages into a summary."
                        ),
                    )
        except Exception:
            pass

        yield StreamModelInfoEvent(model=model_name)

        max_turns = 1000
        max_auto_continues = 100
        auto_continues_used = 0
        for turn in range(max_turns):
            full_content = ""
            had_thinking = False
            collected_tool_calls: list[dict[str, Any]] = []
            # Captured runtime error (e.g. HTTP 500 "invalid JSON in tool
            # call"). When set, we feed it back to the model as a
            # corrective nudge instead of stalling the chat.
            stream_error_msg: str | None = None

            # ── Per-turn summarisation cap ──
            # The initial cap-check at the top of complete_chat_stream
            # only runs once. Inside this loop the agent can fire dozens
            # of tool calls, each appending tool_result rows to
            # ``messages`` — without this gate the prompt would balloon
            # past num_ctx and ollama would silently truncate from the
            # head (losing the system prompt + earliest user turn).
            try:
                ctx_window_now = self.token_budget.context_window(model_name)
                estimate_now = self.token_budget.estimate(
                    model=model_name, messages=messages, tools=tool_schemas
                )
                self._last_estimate = estimate_now
                if ctx_window_now:
                    self._last_context_window = ctx_window_now
                if needs_summary(
                    estimated_tokens=estimate_now, ctx_window=ctx_window_now
                ):
                    result = collapse_messages(
                        messages,
                        summarise=lambda corpus: self._summarise_corpus(
                            corpus, model=model
                        ),
                    )
                    if result.applied:
                        messages = result.new_messages
                        yield StreamNoticeEvent(
                            level="info",
                            message=(
                                f"context cap hit mid-turn "
                                f"({estimate_now}/{ctx_window_now} tokens) — "
                                f"collapsed {result.dropped} older messages."
                            ),
                        )
            except Exception:
                pass

            for event in self.runtime.chat_stream(
                model=model,
                messages=messages,
                tool_schemas=tool_schemas,
            ):
                etype = event.get("type")
                if etype == "delta":
                    full_content += event.get("content", "")
                    yield StreamDeltaEvent(content=event.get("content", ""))
                elif etype == "thinking":
                    had_thinking = True
                    yield StreamThinkingEvent(content=event.get("content", ""))
                elif etype == "tool_call":
                    collected_tool_calls.append(event)
                    yield StreamToolCallEvent(
                        name=str(event.get("name", "")),
                        arguments=str(event.get("arguments", "")),
                        tool_call_id=str(event.get("_id", "")),
                    )
                elif etype == "tool_call_start":
                    yield StreamToolCallStartEvent(
                        index=int(event.get("index", 0)),
                        name=str(event.get("name", "")),
                    )
                elif etype == "tool_call_delta":
                    yield StreamToolCallDeltaEvent(
                        index=int(event.get("index", 0)),
                        delta=str(event.get("delta", "")),
                        name=str(event.get("name", "")),
                    )
                elif etype == "context_usage":
                    real_pt = int(event.get("prompt_tokens", 0) or 0)
                    cw = int(event.get("context_window", 0) or 0)
                    if real_pt > 0:
                        self._last_prompt_tokens = real_pt
                        if cw > 0:
                            self._last_context_window = cw
                        # Calibrate the local TokenBudget against the real
                        # prompt_eval_count reported by the runtime.
                        if self._last_estimate > 0:
                            self.token_budget.record(
                                model=self._last_model or model_name,
                                predicted=self._last_estimate,
                                actual=real_pt,
                            )
                    yield StreamContextUsageEvent(
                        prompt_tokens=real_pt,
                        completion_tokens=int(event.get("completion_tokens", 0) or 0),
                        total_tokens=int(event.get("total_tokens", 0) or 0),
                        context_window=cw,
                    )
                elif etype == "error":
                    msg = str(event.get("message", ""))
                    stream_error_msg = msg
                    yield StreamErrorEvent(message=msg)

            # Recover from runtime-level errors (most importantly HTTP 500
            # "model produced invalid JSON in a tool call") by feeding the
            # error message back to the model as a corrective nudge so the
            # chat doesn't simply stall with a red banner.
            if stream_error_msg and not collected_tool_calls and not full_content.strip():
                if auto_continues_used >= max_auto_continues:
                    yield StreamDoneEvent(model=model_name, stop_reason="error")
                    return
                auto_continues_used += 1
                is_tool_json_error = "invalid JSON in a tool call" in stream_error_msg
                if is_tool_json_error:
                    nudge = (
                        "Your previous tool call failed because its JSON "
                        "arguments were malformed. Runtime said: "
                        f"{stream_error_msg}\n\n"
                        "RETRY the same tool call, but make sure every "
                        "string argument is valid JSON:\n"
                        "  - backslashes must be doubled (\\\\)\n"
                        "  - double quotes must be escaped (\\\")\n"
                        "  - newlines must be \\n, tabs \\t.\n"
                        "Do NOT change the tool name or the intent — just "
                        "fix the escaping and call it again."
                    )
                else:
                    nudge = (
                        "Your previous turn errored out at the runtime level: "
                        f"{stream_error_msg}\n\nRetry the action."
                    )
                yield StreamNoticeEvent(
                    level="info",
                    message=(
                        f"Auto-continue ({auto_continues_used}/{max_auto_continues}): "
                        f"{'tool-call JSON error' if is_tool_json_error else 'runtime error'}"
                    ),
                )
                messages.append({"role": "assistant", "content": ""})
                messages.append({"role": "user", "content": nudge})
                continue

            # Auto-continue when the model produced an empty turn (no text and
            # no tool calls). This happens with reasoning models that "think"
            # but forget to emit a final answer or tool call. Inject a nudge
            # and retry, capped to avoid infinite loops.
            if not collected_tool_calls and not full_content.strip():
                if auto_continues_used >= max_auto_continues:
                    reason = (
                        "Stopped: model produced empty responses for "
                        f"{max_auto_continues + 1} turns in a row "
                        f"(thinking only={had_thinking})."
                    )
                    yield StreamNoticeEvent(level="warn", message=reason)
                    yield StreamDoneEvent(model=model_name, stop_reason="empty_turns")
                    return
                auto_continues_used += 1
                nudge = (
                    "Your previous turn was empty (no text, no tool calls). "
                    "Continue the user's task. If you finished, say so explicitly. "
                    "If you need to call a tool, do it now."
                )
                yield StreamNoticeEvent(
                    level="info",
                    message=(
                        f"Auto-continue ({auto_continues_used}/{max_auto_continues}): empty turn"
                    ),
                )
                messages.append({"role": "assistant", "content": ""})
                messages.append({"role": "user", "content": nudge})
                continue

            if not collected_tool_calls:
                yield StreamDoneEvent(model=model_name, stop_reason="completed")
                return

            # Intermediate turn with tool calls — execute every one of them.
            # Each call produces its own tool_result (success or error JSON),
            # so the model can see exactly which ones worked and which failed
            # before deciding the next turn.
            tc_openai = []
            for i, tc in enumerate(collected_tool_calls):
                call_id = f"call_{turn}_{i}"
                tc["_id"] = call_id
                tc_openai.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    }
                )

            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if full_content:
                assistant_msg["content"] = full_content
            if tc_openai:
                assistant_msg["tool_calls"] = tc_openai
            messages.append(assistant_msg)

            for tc in collected_tool_calls:
                name = tc["name"]
                try:
                    args = _json.loads(tc["arguments"])
                except (ValueError, TypeError):
                    args = {}

                needs_confirm = False
                confirm_warning = ""
                try:
                    spec = self.tools.get(name)
                    invalid_args = self._invalid_tool_arguments(name, args, spec)
                    if invalid_args is not None:
                        result_text = _json.dumps(invalid_args, ensure_ascii=False)
                        yield StreamToolResultEvent(
                            name=name,
                            result=result_text,
                            tool_call_id=tc["_id"],
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["_id"],
                                "content": result_text,
                            }
                        )
                        continue
                    needs_confirm, confirm_warning = self._confirmation_requirements(
                        name, args, spec
                    )
                except Exception:
                    pass

                if needs_confirm:
                    decision_id = uuid.uuid4().hex
                    decision_q: queue.Queue = queue.Queue(maxsize=1)
                    _PENDING_DECISIONS[decision_id] = decision_q
                    yield StreamToolPendingEvent(
                        decision_id=decision_id,
                        name=name,
                        arguments=str(tc["arguments"]),
                        remember_supported=self._remember_supported(name, args),
                        warning=confirm_warning,
                        tool_call_id=tc["_id"],
                    )
                    try:
                        decision = decision_q.get(timeout=300)
                    except queue.Empty:
                        decision = ConfirmationDecision(approved=False)
                    finally:
                        _PENDING_DECISIONS.pop(decision_id, None)
                    if not decision.approved:
                        result_text = _json.dumps(
                            {"status": "rejected_by_user", "tool": name}
                        )
                        yield StreamToolResultEvent(
                            name=name,
                            result=result_text,
                            tool_call_id=tc["_id"],
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["_id"],
                                "content": result_text,
                            }
                        )
                        continue
                    if decision.remember and self._remember_supported(name, args):
                        try:
                            approval_label = self._persist_remembered_approval(
                                name, args, spec
                            )
                            yield StreamNoticeEvent(
                                level="info",
                                message=(
                                    f"Approved {approval_label} for future use in workspace "
                                    f"{self.workspace.root}."
                                ),
                            )
                        except Exception as exc:
                            yield StreamNoticeEvent(
                                level="warn",
                                message=f"Failed to persist approval for {name}: {exc}",
                            )

                try:
                    if needs_confirm:
                        # User-approved: execute directly to bypass policy gate.
                        spec = self.tools.get(name)
                        invalid_args = self._invalid_tool_arguments(name, args, spec)
                        if invalid_args is not None:
                            result_value = invalid_args
                            result_text = _json.dumps(result_value, ensure_ascii=False)
                            yield StreamToolResultEvent(
                                name=name,
                                result=result_text,
                                tool_call_id=tc["_id"],
                            )
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc["_id"],
                                    "content": result_text,
                                }
                            )
                            continue
                        handler = self.tools.handler(name)
                        result_value = handler(args)
                        self.audit.record(
                            tool_name=name,
                            permission=spec.permission.value,
                            arguments=args,
                            result_summary=_summarize_result(result_value),
                            confirmation_required=True,
                        )
                    else:
                        call_result = self.call_tool(name, args)
                        result_value = call_result.result
                    result_text = _json.dumps(result_value, ensure_ascii=False)
                except Exception as exc:
                    result_text = _json.dumps({"error": str(exc)})

                yield StreamToolResultEvent(
                    name=name,
                    result=result_text,
                    tool_call_id=tc["_id"],
                )
                # Phase 2.B.3: surface research progress to the GUI without
                # making it parse tool result JSON.
                stage = _RESEARCH_STAGE_BY_TOOL.get(name)
                if stage and isinstance(result_value := locals().get("result_value"), dict):
                    if "error" not in result_value:
                        try:
                            yield StreamResearchProgressEvent(
                                chat_id=self.workspace.chat_id or "",
                                report_id=str(result_value.get("report_id", "")),
                                stage=stage,
                                section_idx=(
                                    int(result_value["section_idx"])
                                    if isinstance(result_value.get("section_idx"), int)
                                    else None
                                ),
                                detail={
                                    k: v
                                    for k, v in result_value.items()
                                    if k in {"status", "title", "count", "sections", "sources"}
                                },
                            )
                        except Exception:
                            pass
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["_id"],
                        "content": result_text,
                    }
                )

        # Reached max_turns without a clean text answer. Tell the user why so
        # they don't see a silent stop, and ask the model for a final summary.
        yield StreamNoticeEvent(
            level="warn",
            message=(
                f"Reached the {max_turns}-turn tool-calling limit. "
                "Asking the model to summarize progress and what is left."
            ),
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    "You've reached the maximum number of tool-call iterations for this "
                    "request. Stop calling tools now. In plain text, summarize: (1) what "
                    "you did, (2) what is still left, (3) any blockers."
                ),
            }
        )
        try:
            for event in self.runtime.chat_stream(
                model=model, messages=messages, tool_schemas=None
            ):
                etype = event.get("type")
                if etype == "delta":
                    yield StreamDeltaEvent(content=event.get("content", ""))
                elif etype == "thinking":
                    yield StreamThinkingEvent(content=event.get("content", ""))
                elif etype == "context_usage":
                    yield StreamContextUsageEvent(
                        prompt_tokens=int(event.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(event.get("completion_tokens", 0) or 0),
                        total_tokens=int(event.get("total_tokens", 0) or 0),
                        context_window=int(event.get("context_window", 0) or 0),
                    )
                elif etype == "error":
                    yield StreamErrorEvent(message=str(event.get("message", "")))
        except Exception as exc:
            yield StreamErrorEvent(message=f"Summary turn failed: {exc}")
        yield StreamDoneEvent(model=model_name, stop_reason="max_turns")

    def _ensure_system_prompt(
        self,
        chat: Chat,
        *,
        mode: str = "agent",
        model_name: str | None = None,
    ) -> Chat:
        """Prepend the system prompt if the chat doesn't already have one.

        The resolved prompt is the FULL system message for the active
        ``(mode, model)`` pair (see
        :func:`codeagents.core.modes.prompts.resolve_prompt`). The only
        thing layered on top is the plan-execution addendum, which is
        situational.
        """
        from codeagents.core.modes.prompts import resolve_prompt

        base_prompt = resolve_prompt(mode, model_name)
        situational = ""
        if mode != "plan" and self._has_active_plan_for_chat(chat):
            situational += EXECUTE_PLAN_SYSTEM_ADDENDUM
        if chat.messages and chat.messages[0].role == "system":
            if not situational:
                return chat
            first = chat.messages[0]
            if isinstance(first, SystemMessage) and first.content:
                c0 = first.content[0]
                if isinstance(c0, TextContent):
                    new_first = SystemMessage(
                        index=first.index,
                        content=[TextContent(text=c0.text + situational)],
                    )
                    return Chat(
                        messages=[new_first, *chat.messages[1:]],
                        meta=chat.meta,
                        functions=chat.functions,
                    )
            return chat
        sys_msg = SystemMessage(
            index=0,
            content=[TextContent(text=base_prompt + situational)],
        )
        return Chat(
            messages=[sys_msg, *chat.messages],
            meta=chat.meta,
            functions=chat.functions,
        )

    def _has_active_plan_for_chat(self, chat: Chat) -> bool:
        """Whether the chat has any plan that's still draft/building."""
        try:
            from codeagents.stores.plan import ACTIVE_STATUSES, PlanStore

            store = PlanStore.global_default()
            chat_id = chat.id or ""
            for plan in store.list():
                if plan.status not in ACTIVE_STATUSES:
                    continue
                if not chat_id or plan.chat_id == chat_id:
                    return True
        except Exception:
            return False
        return False

    def _summarise_corpus(self, corpus: str, *, model: Any) -> str:
        """Run a short non-streaming summary call. Used by collapse_messages."""
        from codeagents.core.conversation.summarisation import SUMMARY_PROMPT

        msgs = [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": corpus},
        ]
        try:
            stream = self.runtime.chat_stream(model=model, messages=msgs)
            buf: list[str] = []
            for ev in stream:
                if ev.get("type") == "delta":
                    buf.append(str(ev.get("content", "")))
            return "".join(buf).strip()
        except Exception:
            return ""

    def _inject_auto_recall(self, messages: list[dict[str, Any]]) -> None:
        """Insert a hidden auto-recall system block before the last user msg.

        No-op when:
          * the chat is too short (< auto_recall.MIN_HISTORY messages),
          * there's no active chat folder yet,
          * the embedding client is unavailable / offline,
          * no message scored above the threshold.
        """
        # Find the latest user message text.
        last_user: str = ""
        last_user_idx: int = -1
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    last_user = content
                elif isinstance(content, list):
                    parts: list[str] = []
                    for c in content:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            parts.append(c["text"])
                        elif isinstance(c, str):
                            parts.append(c)
                    last_user = "\n".join(parts)
                last_user_idx = i
                break
        if last_user_idx < 0 or not last_user.strip():
            return

        chat_id = (self.workspace.chat_id or "").strip()
        chat_dir: Path | None = None
        if chat_id:
            try:
                from codeagents.stores.chat import default_chats_dir

                chat_dir = default_chats_dir() / chat_id
            except Exception:
                chat_dir = None

        result = maybe_recall(
            chat_dir=chat_dir,
            history_len=len(messages),
            last_user_text=last_user,
            embedding_client=self.runtime,
            embedding_model=self.config.runtime.embedding_model or None,
        )
        if not result.system_text:
            return

        # NOTE: keep keys to {role, content} only - some OpenAI-compatible
        # backends reject unknown fields. Track auto-recall in the audit log
        # below rather than annotating the message itself.
        messages.insert(last_user_idx, {"role": "system", "content": result.system_text})
        try:
            self.audit.record(
                tool_name="auto_recall",
                permission="read_only",
                arguments={"hit_count": result.hit_count, "top_score": result.top_score},
                result_summary=f"injected {result.hit_count} hits ({result.estimate_chars} chars)",
                confirmation_required=False,
            )
        except Exception:
            pass

    def _agent_tools_as_specs(
        self,
        *,
        allowed_permissions: set[Permission] | None = None,
        mode: str | None = None,
    ) -> list[FunctionSpec]:
        """Convert registered tools into FunctionSpec with proper JSON Schema parameters.

        ``mode`` (Phase 2.A.2) further trims the visible toolbox via
        ``mode_tools.filter_for_mode``. ``allowed_permissions`` is the
        permission-level filter that already existed.
        """
        cache_key = (
            "__all__"
            if allowed_permissions is None
            else ",".join(sorted(p.value for p in allowed_permissions))
        )
        if mode:
            cache_key = f"{cache_key}|mode={mode}"
        cached = self._tool_specs_cache.get(cache_key)
        if cached is not None:
            return cached

        specs: list[FunctionSpec] = []
        for ts in self.tools.list():
            if allowed_permissions is not None and ts.permission not in allowed_permissions:
                continue
            if ts.mcp_input_schema:
                params = function_parameters_from_json_schema(ts.mcp_input_schema)
            else:
                params = []
                for p in ts.params:
                    schema: dict[str, Any] = {"type": p.type}
                    if p.enum:
                        schema["enum"] = list(p.enum)
                    params.append(
                        FunctionParameter(
                            name=p.name,
                            schema=schema,
                            description=p.description,
                            required=p.required,
                        )
                    )
            specs.append(
                FunctionSpec(
                    name=ts.name,
                    description=ts.description or "",
                    parameters=params,
                )
            )
        if mode:
            specs = filter_for_mode(mode, specs)
        self._tool_specs_cache[cache_key] = specs
        return specs

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallResult:
        spec = self.tools.get(tool_name)
        invalid_args = self._invalid_tool_arguments(tool_name, arguments, spec)
        if invalid_args is not None:
            self.audit.record(
                tool_name=tool_name,
                permission=spec.permission.value,
                arguments=arguments,
                result_summary=_summarize_result(invalid_args),
                confirmation_required=False,
            )
            return ToolCallResult(
                tool_name=tool_name,
                result=invalid_args,
                confirmation_required=False,
            )
        confirmation_required, _warning = self._confirmation_requirements(
            tool_name, arguments, spec
        )
        if confirmation_required:
            result = {"status": "confirmation_required", "tool": tool_name}
        else:
            handler = self.tools.handler(tool_name)
            result = handler(arguments)

        self.audit.record(
            tool_name=tool_name,
            permission=spec.permission.value,
            arguments=arguments,
            result_summary=_summarize_result(result),
            confirmation_required=confirmation_required,
        )
        return ToolCallResult(
            tool_name=tool_name,
            result=result,
            confirmation_required=confirmation_required,
        )

    def _invalid_tool_arguments(
        self, tool_name: str, arguments: Any, spec: ToolSpec
    ) -> dict[str, Any] | None:
        if not isinstance(arguments, dict):
            return {
                "error": "invalid_tool_arguments",
                "status": "rejected_invalid_arguments",
                "tool": tool_name,
                "reason": "Tool arguments must be a JSON object.",
                "allowed_arguments": [param.name for param in spec.params],
                "message_to_model": (
                    f"Do not call {tool_name} with non-object arguments. "
                    "Retry with a JSON object matching the tool schema."
                ),
            }
        if spec.mcp_input_schema:
            props = spec.mcp_input_schema.get("properties")
            allowed = set(props.keys()) if isinstance(props, dict) else set()
        else:
            allowed = {param.name for param in spec.params}
        extra = sorted(key for key in arguments if key not in allowed)
        if not extra:
            return None
        return {
            "error": "invalid_tool_arguments",
            "status": "rejected_invalid_arguments",
            "tool": tool_name,
            "reason": "The model passed unsupported extra arguments to the tool.",
            "extra_arguments": extra,
            "allowed_arguments": sorted(allowed),
            "message_to_model": (
                f"Do not call {tool_name} with unsupported arguments: {', '.join(extra)}. "
                f"Allowed arguments are: {', '.join(sorted(allowed)) or '(none)'}. "
                "Retry the tool call using only the declared schema fields."
            ),
        }

    def _confirmation_requirements(
        self, tool_name: str, arguments: dict[str, Any], spec: ToolSpec
    ) -> tuple[bool, str]:
        if not self.policy.requires_confirmation(spec.permission):
            return False, ""
        warning = self._confirmation_warning(tool_name, arguments)
        shell_command = self._shell_command_name(tool_name, arguments)
        if (
            shell_command
            and not warning
            and self.approvals.is_shell_command_approved(shell_command, spec.permission)
        ):
            return False, ""
        if tool_name == "shell":
            return True, warning
        if not warning and self.approvals.is_tool_approved(tool_name, spec.permission):
            return False, ""
        return True, warning

    def _remember_supported(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if tool_name == "shell":
            return self._shell_command_name(tool_name, arguments) is not None
        return not self._confirmation_warning(tool_name, arguments)

    def _persist_remembered_approval(
        self, tool_name: str, arguments: dict[str, Any], spec: ToolSpec
    ) -> str:
        shell_command = self._shell_command_name(tool_name, arguments)
        if shell_command:
            self.approvals.approve_shell_command(shell_command, spec.permission)
            return f"shell command `{shell_command}`"
        self.approvals.approve_tool(tool_name, spec.permission)
        return tool_name

    def _confirmation_warning(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "shell":
            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                return ""
            if self._shell_command_name(tool_name, arguments) is None:
                return (
                    "BIG WARNING: this shell command uses shell syntax such as pipes, "
                    "redirects, variables, subcommands, or command chaining. Persistent "
                    "approval will NOT apply; this requires a one-time decision."
                )
            return ""
        if tool_name != "rm":
            return ""
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return "BIG WARNING: rm requested without a valid path. Persistent approval will not apply."
        try:
            resolved = self.workspace.resolve_inside(raw_path)
        except Exception:
            return (
                "BIG WARNING: rm requested a path outside the current workspace. "
                "Persistent approval will NOT apply; this requires a one-time decision "
                "and the workspace-scoped rm tool will reject paths outside the workspace."
            )
        if resolved == self.workspace.root:
            return (
                "BIG WARNING: rm requested the workspace root. Persistent approval will "
                "NOT apply and the rm tool will refuse to delete it."
            )
        try:
            rel = resolved.relative_to(self.workspace.root)
        except ValueError:
            return (
                "BIG WARNING: rm requested a path outside the current workspace. "
                "Persistent approval will NOT apply."
            )
        if rel.parts and rel.parts[0] == ".codeagents":
            return (
                "BIG WARNING: rm requested CodeAgents internal state. Persistent "
                "approval will NOT apply and the rm tool will refuse to delete it."
            )
        return ""

    def _shell_command_name(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str | None:
        if tool_name != "shell":
            return None
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        if _has_shell_metacharacters(command):
            return None
        try:
            argv = shlex.split(command)
        except ValueError:
            return None
        if not argv:
            return None
        return Path(argv[0]).name or None

    def _with_workspace_context(self, prompt: str, *, task: str | None) -> str:
        if task not in CODING_TASKS:
            return prompt
        context = self._workspace_context_text(prompt)
        return f"{context}\n\nUser request:\n{prompt}"

    def _chat_with_workspace_context(self, chat: Chat) -> Chat:
        latest_prompt = ""
        for message in reversed(chat.messages):
            if message.role == "user":
                latest_prompt = message.text()
                break
        context = self._workspace_context_text(latest_prompt)
        context_message = SystemMessage(
            index=0,
            content=[
                TextContent(
                    text=(
                        "Use this as the current working directory context for the "
                        f"conversation:\n{context}"
                    )
                )
            ],
        )
        return Chat(
            messages=[context_message, *chat.messages],
            meta={**chat.meta, "workspace": str(self.workspace.root)},
            functions=chat.functions,
        )

    def _workspace_context_text(self, prompt: str = "") -> str:
        build_index(self.workspace.root)
        summary = index_summary(self.workspace.root)
        languages = summary.get("languages", {})
        top_dirs = summary.get("top_dirs", {})
        language_text = (
            ", ".join(f"{name}={count}" for name, count in languages.items()) or "none"
        )
        dir_text = (
            ", ".join(f"{name}={count}" for name, count in top_dirs.items()) or "none"
        )
        relevant_text = ""
        if prompt.strip():
            semantic = int(summary.get("embedded_chunks", 0) or 0) > 0
            try:
                results = search_index(
                    self.workspace.root,
                    prompt,
                    semantic=semantic,
                    embedding_client=self.runtime if semantic else None,
                    embedding_model=(
                        self.config.runtime.embedding_model if semantic else None
                    ),
                    limit=8,
                )
            except Exception:
                results = search_index(
                    self.workspace.root, prompt, semantic=False, limit=8
                )
            if results:
                relevant_text = "\nRelevant indexed matches:\n" + "\n".join(
                    f"- {result.path}:{result.start_line}-{result.end_line} "
                    f"({result.kind}, score={result.score:.2f}) {result.preview}"
                    for result in results
                )
        return (
            f"Current workspace: {self.workspace.root}\n"
            f"Indexed files: {summary.get('files', 0)}\n"
            f"Indexed symbols: {summary.get('symbols', 0)}\n"
            f"Indexed chunks: {summary.get('chunks', 0)}\n"
            f"Embedded chunks: {summary.get('embedded_chunks', 0)}\n"
            f"Languages: {language_text}\n"
            f"Top paths: {dir_text}"
            f"{relevant_text}"
        )


