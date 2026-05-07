"""Plan-store tools: create_plan / patch_plan / mark_step / list_plans.

Thin handlers around :class:`codeagents.stores.plan.PlanStore`. The
``chat_id`` is injected by ``AgentCore`` via ``workspace.chat_id`` so the
model never has to pass it explicitly.
"""

from __future__ import annotations

from typing import Any

from codeagents.core.workspace import Workspace


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required string argument: {key}")
    return value


def _plan_store():
    """Local import + lazy global so tests can swap the env override before use."""
    from codeagents.stores.plan import PlanStore

    return PlanStore.global_default()


def _plan_summary_dict(plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "title": plan.title,
        "status": plan.status,
        "total_steps": plan.total_steps,
        "done_steps": plan.done_steps,
        "workspace": plan.workspace,
        "chat_id": plan.chat_id,
        "updated_at": plan.updated_at,
    }


def create_plan_tool(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    from codeagents.stores.plan import PlanLimitError

    title = _require_str(args, "title").strip()
    summary = _require_str(args, "summary").strip()
    raw_steps = args.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return {"error": "steps must be a non-empty list of {title, detail} objects"}
    norm_steps: list[dict[str, Any]] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            return {"error": "each step must be an object with 'title' and 'detail'"}
        s_title = str(step.get("title") or step.get("name") or "").strip()
        if not s_title:
            return {"error": "each step requires a non-empty 'title'"}
        norm_steps.append(
            {
                "title": s_title,
                "detail": str(step.get("detail") or step.get("description") or "").strip(),
            }
        )
    chat_id = (workspace.chat_id or str(args.get("chat_id") or "")).strip()
    try:
        plan = _plan_store().create(
            title=title,
            summary=summary,
            steps=norm_steps,
            workspace=str(workspace.root),
            chat_id=chat_id,
        )
    except PlanLimitError as exc:
        return {"error": str(exc)}
    return {
        **_plan_summary_dict(plan),
        "markdown": plan.to_markdown(),
        "notice": (
            "Plan created. The user sees it in the chat banner; they need to click "
            "Build before you start executing it."
        ),
    }


def patch_plan_tool(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    from codeagents.stores.plan import PlanNotFoundError

    plan_id = _require_str(args, "plan_id").strip()
    title = args.get("title")
    summary = args.get("summary")
    raw_steps = args.get("steps")
    steps: list[dict[str, Any]] | None = None
    if raw_steps is not None:
        if not isinstance(raw_steps, list) or not raw_steps:
            return {"error": "steps must be a non-empty list when provided"}
        steps = []
        for step in raw_steps:
            if not isinstance(step, dict):
                return {"error": "each step must be an object"}
            s_title = str(step.get("title") or step.get("name") or "").strip()
            if not s_title:
                return {"error": "each step requires a non-empty 'title'"}
            steps.append(
                {
                    "title": s_title,
                    "detail": str(step.get("detail") or step.get("description") or "").strip(),
                }
            )
    if title is None and summary is None and steps is None:
        return {"error": "provide at least one of title, summary, steps"}
    try:
        plan = _plan_store().patch(
            plan_id,
            title=title.strip() if isinstance(title, str) else None,
            summary=summary.strip() if isinstance(summary, str) else None,
            steps=steps,
        )
    except PlanNotFoundError:
        return {"error": f"plan {plan_id} not found"}
    return {**_plan_summary_dict(plan), "markdown": plan.to_markdown()}


def mark_step_tool(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    from codeagents.stores.plan import PlanNotFoundError

    plan_id = _require_str(args, "plan_id").strip()
    try:
        step_n = int(args["step_n"])
    except (KeyError, TypeError, ValueError):
        return {"error": "step_n must be an integer"}
    status = str(args.get("status") or "").strip()
    if status not in {"pending", "in_progress", "done", "skipped"}:
        return {
            "error": "status must be one of 'pending', 'in_progress', 'done', 'skipped'"
        }
    note = str(args.get("note") or "").strip()
    try:
        plan = _plan_store().mark_step(plan_id, step_n, status, note=note)  # type: ignore[arg-type]
    except PlanNotFoundError:
        return {"error": f"plan {plan_id} not found"}
    except ValueError as exc:
        return {"error": str(exc)}
    return _plan_summary_dict(plan)


def list_plans_tool(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    raw = str(args.get("status") or "all").strip().lower()
    plans = _plan_store().list()
    if raw == "active":
        from codeagents.stores.plan import ACTIVE_STATUSES

        plans = [p for p in plans if p.status in ACTIVE_STATUSES]
    elif raw in {"draft", "building", "completed", "rejected"}:
        plans = [p for p in plans if p.status == raw]
    return {"plans": [_plan_summary_dict(p) for p in plans]}


__all__ = [
    "create_plan_tool",
    "list_plans_tool",
    "mark_step_tool",
    "patch_plan_tool",
]
