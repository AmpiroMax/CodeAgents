"""Conversation-loop helpers used by :class:`codeagents.agent.AgentCore`.

Stage-5 carve-out: small, side-effect-free policies extracted from
``agent.py`` so the chat loop body becomes scannable. Heavier helpers
(e.g. summarisation, recall hydration) live in their own modules.
"""

from codeagents.core.conversation.policies import EXECUTE_PLAN_SYSTEM_ADDENDUM

__all__ = ["EXECUTE_PLAN_SYSTEM_ADDENDUM"]
