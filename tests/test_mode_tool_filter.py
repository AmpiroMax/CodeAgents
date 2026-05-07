from __future__ import annotations

from pathlib import Path

import pytest

from codeagents.agent import AgentCore
from codeagents.mode_tools import MODE_TOOLS, filter_for_mode, whitelist_for


def _names(specs) -> set[str]:
    return {s.name for s in specs}


def test_whitelist_for_known_modes() -> None:
    assert whitelist_for("ask") == set(MODE_TOOLS["ask"])
    assert whitelist_for("plan") == set(MODE_TOOLS["plan"])
    assert whitelist_for("research") == set(MODE_TOOLS["research"])


def test_whitelist_returns_none_for_agent_and_unknown() -> None:
    assert whitelist_for("agent") is None
    assert whitelist_for("general") is None
    assert whitelist_for("") is None
    assert whitelist_for("totally-unknown") is None


class _S:
    def __init__(self, name: str) -> None:
        self.name = name


def test_filter_drops_outside_whitelist() -> None:
    specs = [_S("read_file"), _S("write_file"), _S("bash"), _S("grep")]
    out = filter_for_mode("ask", specs)
    assert _names(out) == {"read_file", "grep"}


def test_filter_keeps_everything_in_agent_mode() -> None:
    specs = [_S("read_file"), _S("write_file"), _S("bash")]
    assert _names(filter_for_mode("agent", specs)) == {"read_file", "write_file", "bash"}


def test_agent_visible_tools_per_mode(tmp_path: Path) -> None:
    agent = AgentCore.from_workspace(tmp_path)

    ask_specs = agent._agent_tools_as_specs(
        allowed_permissions={agent.tools.get("read_file").permission}, mode="ask"
    )
    plan_specs = agent._agent_tools_as_specs(
        allowed_permissions={agent.tools.get("read_file").permission}, mode="plan"
    )
    agent_specs = agent._agent_tools_as_specs(mode="agent")

    ask_names = _names(ask_specs)
    plan_names = _names(plan_specs)
    agent_names = _names(agent_specs)

    # ask is fully read-only; no write/shell/plan tools.
    assert "bash" not in ask_names
    assert "write_file" not in ask_names
    assert "create_plan" not in ask_names
    assert "read_file" in ask_names
    assert "web_search" in ask_names
    assert "recall_chat" in ask_names

    # plan = ask superset + plan tools
    assert {"create_plan", "patch_plan", "mark_step", "list_plans"}.issubset(plan_names)
    # plan still cannot write/bash
    assert "bash" not in plan_names
    assert "write_file" not in plan_names

    # agent has the full default toolbox (>= 15 tools).
    assert len(agent_names) >= 15
    assert "bash" in agent_names
    assert "write_file" in agent_names


def test_research_mode_excludes_destructive_tools(tmp_path: Path) -> None:
    agent = AgentCore.from_workspace(tmp_path)
    # Research tools (clarify_research/...) are not registered yet at this
    # point in the migration, but the mode whitelist for read tools must
    # already drop bash/write/edit.
    specs = agent._agent_tools_as_specs(mode="research")
    names = _names(specs)
    for forbidden in {"bash", "shell", "write_file", "edit_file", "rm"}:
        assert forbidden not in names, f"{forbidden} must not be visible in research"


def test_visible_token_budget_dropped_significantly(tmp_path: Path) -> None:
    agent = AgentCore.from_workspace(tmp_path)
    full = agent._agent_tools_as_specs(mode="agent")
    ask = agent._agent_tools_as_specs(
        allowed_permissions={agent.tools.get("read_file").permission}, mode="ask"
    )
    # Ask exposes a small subset; we want at least a 30% reduction.
    assert len(ask) <= len(full) * 0.7
