from __future__ import annotations

import queue
import shlex
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Global registry for pending confirmations.
# Keyed by decision_id; value is a Queue[ConfirmationDecision] that the HTTP
# confirm endpoint fills.
_PENDING_DECISIONS: dict[str, queue.Queue] = {}


@dataclass(frozen=True)
class ConfirmationDecision:
    approved: bool
    remember: bool = False


def submit_confirmation(
    decision_id: str, approved: bool, *, remember: bool = False
) -> bool:
    """Deliver a decision for a pending tool confirmation. Returns True if delivered."""
    q = _PENDING_DECISIONS.get(decision_id)
    if q is None:
        return False
    q.put(ConfirmationDecision(approved=approved, remember=remember))
    return True


from codeagents.audit import AuditLog
from codeagents.config import PROJECT_ROOT, AppConfig, load_app_config
from codeagents.model_router import ModelRouter
from codeagents.permissions import (
    Permission,
    PermissionPolicy,
    WorkspaceApprovalStore,
    load_permission_policy,
)
from codeagents.runtime import OpenAICompatibleRuntime
from codeagents.schemas import (
    Chat,
    FunctionParameter,
    FunctionSpec,
    function_parameters_from_json_schema,
    merge_chat_meta,
    SystemMessage,
    TextContent,
)
from codeagents.stream_events import (
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
)
from codeagents.mcp.bridge import register_mcp_tools
from codeagents.tools import ToolRegistry, ToolSpec, load_tool_registry
from codeagents.tools_native import register_code_tools
from codeagents.workspace import Workspace
from codeagents.indexer import build_index, index_summary, search_index


# ─── System prompt ───────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a local coding assistant with direct access to the user's file system \
through tools. You run on the user's machine and can read, write, search, and \
execute commands in their workspace.

## Capabilities
You have tools for: reading files, writing files, editing files, searching code, \
listing directories, finding files by pattern, running shell commands, and git operations.

## Rules
- You may emit multiple tool calls per turn. Each call returns its own
  result (success or `{"error": "..."}`); the next turn you'll see all of
  them and can react to any failures individually.
- Always read a file before editing it. Never guess file contents.
- Use edit_file for small targeted changes (replacing a specific fragment). \
Use write_file when rewriting the entire file.
- Use search or glob_files to locate code before modifying it.
- Use list_directory to understand project structure when starting.
- Use git_status and git_diff to understand the current state of changes.
- When no tool is needed, answer the question directly in text.
- Be concise. State uncertainty when unsure.
- Respond in the same language the user writes in.

## Tool-call JSON escaping (critical)
Tool arguments are parsed as strict JSON. When you put code, file content, or \
shell snippets into a string field:
- Every backslash must be doubled: write "\\\\n", "\\\\t", "C:\\\\Users\\\\name", \
".\\\\venv\\\\Scripts\\\\activate" — never bare "\\n" or "\\".
- Every double-quote inside the string must be escaped: \\".
- Newlines inside multi-line content must be the two-char sequence "\\n", not a \
literal newline.
A single unescaped backslash (e.g. ".venv\\Scripts") will make the runtime \
reject the entire tool call with "invalid character in string escape code", \
and the tool will not execute.\
"""

MODE_SYSTEM_ADDENDUM = {
    "ask": (
        "\n\n## Session mode: ask\n"
        "You are in ask mode: prefer explanations and read-only inspection. "
        "Only read-only tools are exposed; do not insist on write or shell actions."
    ),
    "plan": (
        "\n\n## Session mode: plan\n"
        "You MUST produce an actionable plan and persist it via the `create_plan` tool. "
        "You may use any read-only tool (read_file, ls, grep, web_search, docs_search, ...) "
        "to investigate before drafting. You may NOT call write/shell/network-write tools.\n\n"
        "Workflow:\n"
        "  1. Briefly think through scope, constraints, and tradeoffs out loud (this thinking "
        "is NOT the plan itself).\n"
        "  2. Call `create_plan` exactly once. Arguments:\n"
        "       title   — ≤ 60 chars, descriptive (e.g. 'Add SQLite backed audit log')\n"
        "       summary — 1–3 short paragraphs explaining context, key tradeoffs, and the "
        "                   final shape of the change. NO numbered list here.\n"
        "       steps   — ordered list of {title, detail} objects. ``title`` is a short "
        "                   verb-led step (≤ 8 words). ``detail`` describes the substep, "
        "                   files involved, what 'done' looks like.\n"
        "  3. After create_plan, give the user a short message: 'Plan saved as: <title>. "
        "Click Build to execute, or refine via patch_plan.' Do NOT execute the steps yourself "
        "in plan mode — wait for Build.\n\n"
        "Up to 3 plans can be active at once; if create_plan returns a limit error, ask "
        "the user to reject one first.\n"
    ),
}


EXECUTE_PLAN_SYSTEM_ADDENDUM = (
    "\n\n## Plan execution\n"
    "There is one or more active plans pinned to this chat. Treat each step in order:\n"
    "  - Before starting step N, call `mark_step(plan_id, step_n=N, status='in_progress')`.\n"
    "  - Do the work using whatever tools are needed (write_file, run_command, etc.).\n"
    "  - When the step is finished, call `mark_step(..., status='done')` (or 'skipped' "
    "with a brief note explaining why).\n"
    "  - Then move on to the next step. Do NOT pause between steps; finish the entire "
    "plan unless the user explicitly tells you to stop.\n"
    "  - If the live work makes the plan stale, call `patch_plan` to revise the remaining "
    "steps before continuing.\n"
    "If a plan is already partially done (some steps marked done), pick up at the first "
    "step whose status is NOT 'done' or 'skipped'.\n"
)

CODING_TASKS = {"code", "coding", "edit", "fast"}


def _allowed_permissions_for_mode(mode: str) -> set[Permission] | None:
    """None means all enabled tools; otherwise restrict by tool permission."""
    if mode == "ask":
        return {Permission.READ_ONLY}
    if mode == "plan":
        return {Permission.READ_ONLY, Permission.PROPOSE}
    return None


@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str
    result: dict[str, Any]
    confirmation_required: bool


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
            PROJECT_ROOT / "config" / "tools.toml"
        )
        self.approvals = WorkspaceApprovalStore(workspace.root)
        self.router = ModelRouter(self.config)
        self.runtime = OpenAICompatibleRuntime(self.config.runtime)
        self.audit = AuditLog(workspace.root / ".codeagents" / "audit.jsonl")
        self.tools = load_tool_registry(PROJECT_ROOT / "config" / "tools.toml")
        register_code_tools(self.tools, workspace)
        register_mcp_tools(self.tools, PROJECT_ROOT / "config" / "tools.toml")
        self._tool_specs_cache: dict[str | None, list[FunctionSpec]] = {}
        # React to change_workspace tool calls: refresh per-workspace state.
        workspace.on_root_change.append(self._on_workspace_root_change)

    def _on_workspace_root_change(self, workspace: Workspace) -> None:
        """Refresh per-workspace state after change_workspace."""
        self.approvals = WorkspaceApprovalStore(workspace.root)
        self.audit = AuditLog(workspace.root / ".codeagents" / "audit.jsonl")
        self._tool_specs_cache.clear()

    def reroot(self, path: Path | str) -> Workspace:
        """Public helper to switch the workspace root programmatically."""
        self.workspace.change_root(path)
        return self.workspace

    @classmethod
    def from_workspace(cls, path: Path | str = ".") -> "AgentCore":
        return cls(workspace=Workspace.from_path(path))

    def chat(self, prompt: str, *, task: str | None = "general") -> str:
        model = self.router.for_task(task)
        enriched_prompt = self._with_workspace_context(prompt, task=task)
        chat = Chat.from_prompt(
            enriched_prompt,
            system=SYSTEM_PROMPT,
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
            else self._agent_tools_as_specs(allowed_permissions=allowed)
        )
        chat = self._ensure_system_prompt(chat, mode=mode, model_name=model_name)

        messages = chat.to_openai_messages()
        tool_schemas = [t.to_json_schema() for t in tools] if tools else None

        yield StreamModelInfoEvent(model=model_name)

        max_turns = 1000
        max_auto_continues = 100
        auto_continues_used = 0
        for turn in range(max_turns):
            full_content = ""
            had_thinking = False
            collected_tool_calls: list[dict[str, Any]] = []

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
                    yield StreamContextUsageEvent(
                        prompt_tokens=int(event.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(event.get("completion_tokens", 0) or 0),
                        total_tokens=int(event.get("total_tokens", 0) or 0),
                        context_window=int(event.get("context_window", 0) or 0),
                    )
                elif etype == "error":
                    yield StreamErrorEvent(message=str(event.get("message", "")))

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
        """Prepend the system prompt if the chat doesn't already have one."""
        from codeagents.system_prompts import system_prompt_addendum

        # Stack: model-specific addendum (per-mode override) + mode-level
        # behavioural rules + (optionally) plan-execution rules. Order
        # matters: model-specific lines sit closest to the base prompt so
        # they read as "who you are", while plan-execution sits last as
        # "what to do right now".
        addendum = ""
        model_block = system_prompt_addendum(model_name, mode)
        if model_block:
            addendum += "\n\n## Model profile\n" + model_block
        addendum += MODE_SYSTEM_ADDENDUM.get(mode, "")
        if mode != "plan" and self._has_active_plan_for_chat(chat):
            addendum += EXECUTE_PLAN_SYSTEM_ADDENDUM
        if chat.messages and chat.messages[0].role == "system":
            if not addendum:
                return chat
            first = chat.messages[0]
            if isinstance(first, SystemMessage) and first.content:
                c0 = first.content[0]
                if isinstance(c0, TextContent):
                    new_first = SystemMessage(
                        index=first.index,
                        content=[TextContent(text=c0.text + addendum)],
                    )
                    return Chat(
                        messages=[new_first, *chat.messages[1:]],
                        meta=chat.meta,
                        functions=chat.functions,
                    )
            return chat
        sys_msg = SystemMessage(
            index=0,
            content=[TextContent(text=SYSTEM_PROMPT + addendum)],
        )
        return Chat(
            messages=[sys_msg, *chat.messages],
            meta=chat.meta,
            functions=chat.functions,
        )

    def _has_active_plan_for_chat(self, chat: Chat) -> bool:
        """Whether the chat has any plan that's still draft/building."""
        try:
            from codeagents.plan_store import ACTIVE_STATUSES, PlanStore

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

    def _agent_tools_as_specs(
        self, *, allowed_permissions: set[Permission] | None = None
    ) -> list[FunctionSpec]:
        """Convert registered tools into FunctionSpec with proper JSON Schema parameters."""
        cache_key = (
            "__all__"
            if allowed_permissions is None
            else ",".join(sorted(p.value for p in allowed_permissions))
        )
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


def _summarize_result(result: dict[str, Any]) -> str:
    if "status" in result:
        return str(result["status"])
    if "exit_code" in result:
        return f"exit_code={result['exit_code']}"
    if "content" in result:
        return f"content_chars={len(str(result['content']))}"
    if "diff" in result:
        return f"diff_chars={len(str(result['diff']))}"
    return "ok"


def _has_shell_metacharacters(command: str) -> bool:
    return any(char in command for char in "\n\r;&|<>`$")
