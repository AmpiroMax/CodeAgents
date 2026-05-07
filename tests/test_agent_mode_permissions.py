from __future__ import annotations

from codeagents.core.orchestrator import _allowed_permissions_for_mode
from codeagents.core.permissions import Permission


def test_allowed_permissions_ask_read_only() -> None:
    perms = _allowed_permissions_for_mode("ask")
    assert perms == {Permission.READ_ONLY}


def test_allowed_permissions_plan_includes_propose() -> None:
    perms = _allowed_permissions_for_mode("plan")
    assert perms == {Permission.READ_ONLY, Permission.PROPOSE}


def test_allowed_permissions_agent_is_unrestricted() -> None:
    assert _allowed_permissions_for_mode("agent") is None
    assert _allowed_permissions_for_mode("general") is None
