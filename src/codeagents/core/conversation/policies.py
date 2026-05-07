"""Static policy strings layered on top of the resolved system prompt.

These are *situational*: appended only when a particular condition holds
in a given turn (e.g. a plan is being executed). Static per-mode rules
live in ``registry/prompts/modes/<mode>.json`` and are returned by
:func:`codeagents.core.modes.prompts.resolve_prompt` as the FULL system
message — there is no longer a base ``SYSTEM_PROMPT`` constant.
"""

from __future__ import annotations

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


__all__ = ["EXECUTE_PLAN_SYSTEM_ADDENDUM"]
