"""Standalone primitives extracted from the agent loop.

These were previously module-level definitions in ``agent.py``. Pulled
out so :mod:`codeagents.core.orchestrator` only contains the
:class:`AgentCore` class and the helpers that genuinely depend on
``self``.

Contents:

* :class:`ConfirmationDecision`, :func:`submit_confirmation` and the
  in-process queue ``_PENDING_DECISIONS`` — bridge between the HTTP
  ``/confirm`` endpoint and the agent's tool-call loop.
* :class:`ToolCallResult` — return type of ``AgentCore.call_tool``.
* :func:`allowed_permissions_for_mode` — convenience wrapper kept for
  the legacy import path ``codeagents.agent._allowed_permissions_for_mode``.
* :data:`RESEARCH_STAGE_BY_TOOL` — mapping used to tag streaming events
  emitted while research tools execute.
* :data:`CODING_TASKS` — task labels that should route to a coding-tuned
  model.
* :func:`summarize_result` / :func:`has_shell_metacharacters` — tiny
  pure helpers reused by the orchestrator and tool dispatch.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Any

from codeagents.core.permissions import Permission


# Global registry for pending tool-call confirmations. Keyed by
# ``decision_id``; value is a ``Queue[ConfirmationDecision]`` that the
# HTTP confirm endpoint fills.
_PENDING_DECISIONS: dict[str, queue.Queue] = {}


@dataclass(frozen=True)
class ConfirmationDecision:
    approved: bool
    remember: bool = False


def submit_confirmation(
    decision_id: str, approved: bool, *, remember: bool = False
) -> bool:
    """Deliver a decision for a pending tool confirmation.

    Returns ``True`` if a waiter was registered for ``decision_id``.
    """
    q = _PENDING_DECISIONS.get(decision_id)
    if q is None:
        return False
    q.put(ConfirmationDecision(approved=approved, remember=remember))
    return True


@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str
    result: dict[str, Any]
    confirmation_required: bool


# Maps research tool names to a stream "stage" tag (Phase 2.B.3).
RESEARCH_STAGE_BY_TOOL: dict[str, str] = {
    "clarify_research": "clarify_ready",
    "submit_clarify_answers": "clarify_answered",
    "plan_research": "plan_ready",
    "expand_query": "queries_ready",
    "extract_facts": "facts_extracted",
    "draft_section": "section_drafted",
    "assemble_report": "assembled",
}


CODING_TASKS = {"code", "coding", "edit", "fast"}


def allowed_permissions_for_mode(mode: str) -> set[Permission] | None:
    """``None`` means all enabled tools; otherwise restrict by permission.

    Thin wrapper around :func:`codeagents.core.modes.allowed_permissions_for`.
    """
    from codeagents.core.modes import allowed_permissions_for

    return allowed_permissions_for(mode)


def summarize_result(result: dict[str, Any]) -> str:
    if "status" in result:
        return str(result["status"])
    if "exit_code" in result:
        return f"exit_code={result['exit_code']}"
    if "content" in result:
        return f"content_chars={len(str(result['content']))}"
    if "diff" in result:
        return f"diff_chars={len(str(result['diff']))}"
    return "ok"


def has_shell_metacharacters(command: str) -> bool:
    return any(char in command for char in "\n\r;&|<>`$")


__all__ = [
    "CODING_TASKS",
    "ConfirmationDecision",
    "RESEARCH_STAGE_BY_TOOL",
    "ToolCallResult",
    "_PENDING_DECISIONS",
    "allowed_permissions_for_mode",
    "has_shell_metacharacters",
    "submit_confirmation",
    "summarize_result",
]
