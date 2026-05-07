"""Tests for the Plans subsystem (plan_store + native plan tools)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from codeagents.stores.plan import (
    MAX_ACTIVE_PLANS,
    Plan,
    PlanLimitError,
    PlanNotFoundError,
    PlanStore,
    PlanStep,
    default_plans_dir,
)
from codeagents.tools.native_code import (
    create_plan_tool,
    list_plans_tool,
    mark_step_tool,
    patch_plan_tool,
)
from codeagents.tools import ToolRegistry
from codeagents.tools.native_code import register_code_tools
from codeagents.core.workspace import Workspace


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlanStore:
    monkeypatch.setenv("CODEAGENTS_PLANS_DIR", str(tmp_path / "plans"))
    return PlanStore.global_default()


@pytest.fixture()
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(root=root, cwd=root)


def test_default_plans_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEAGENTS_PLANS_DIR", str(tmp_path / "p"))
    assert default_plans_dir() == (tmp_path / "p").resolve()


def test_per_chat_layout_writes_under_chats_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without CODEAGENTS_PLANS_DIR, plans must land in
    ``<chats_dir>/<chat_id>/plans/`` so they live next to their chat file."""
    monkeypatch.delenv("CODEAGENTS_PLANS_DIR", raising=False)
    chats = tmp_path / "chats"
    monkeypatch.setenv("CODEAGENTS_CHATS_DIR", str(chats))
    store = PlanStore.global_default()
    plan = store.create(
        title="Per-chat",
        summary="x",
        steps=[{"title": "a"}],
        chat_id="abc123",
    )
    expected = chats / "abc123" / "plans" / f"{plan.id}.json"
    assert expected.exists(), expected
    # Listing must find it.
    ids = {p.id for p in store.list()}
    assert plan.id in ids
    # And it survives a round-trip load by id (no chat_id needed).
    assert store.load(plan.id).title == "Per-chat"


def test_per_chat_layout_orphan_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CODEAGENTS_PLANS_DIR", raising=False)
    chats = tmp_path / "chats"
    monkeypatch.setenv("CODEAGENTS_CHATS_DIR", str(chats))
    store = PlanStore.global_default()
    plan = store.create(title="Orphan", summary="x", steps=[{"title": "a"}])
    assert (chats / "_orphans" / "plans" / f"{plan.id}.json").exists()


def test_create_and_load_plan(store: PlanStore) -> None:
    plan = store.create(
        title="Add audit log",
        summary="Persist tool calls.",
        steps=[
            {"title": "Design schema", "detail": "JSONL fields"},
            {"title": "Wire writer"},
        ],
        workspace="/tmp/ws",
        chat_id="c1",
    )
    assert plan.id and plan.title == "Add audit log"
    assert plan.total_steps == 2
    assert plan.steps[0].n == 1 and plan.steps[1].n == 2
    assert plan.steps[0].status == "pending"
    assert plan.status == "draft"
    again = store.load(plan.id)
    assert again.title == plan.title
    # Markdown sibling is best-effort but should be present (legacy flat layout
    # — tests pin CODEAGENTS_PLANS_DIR so the store keeps everything in one
    # directory next to the JSON).
    assert store.flat_root is not None
    assert (store.flat_root / f"{plan.id}.md").exists()


def test_active_limit(store: PlanStore) -> None:
    for i in range(MAX_ACTIVE_PLANS):
        store.create(
            title=f"Plan {i}",
            summary="x",
            steps=[{"title": "do"}],
        )
    with pytest.raises(PlanLimitError):
        store.create(title="overflow", summary="x", steps=[{"title": "y"}])


def test_active_limit_is_per_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chat hitting its own MAX_ACTIVE_PLANS cap must NOT block creation in
    a different chat. Pre-fix the cap was global and any active plan from a
    long-forgotten chat would refuse new plans everywhere else."""
    monkeypatch.delenv("CODEAGENTS_PLANS_DIR", raising=False)
    monkeypatch.setenv("CODEAGENTS_CHATS_DIR", str(tmp_path / "chats"))
    store = PlanStore.global_default()
    for i in range(MAX_ACTIVE_PLANS):
        store.create(
            title=f"chat-A {i}",
            summary="x",
            steps=[{"title": "do"}],
            chat_id="chatA",
        )
    with pytest.raises(PlanLimitError):
        store.create(
            title="overflow A",
            summary="x",
            steps=[{"title": "y"}],
            chat_id="chatA",
        )
    # Second chat starts with a fresh quota.
    other = store.create(
        title="first in chat B",
        summary="x",
        steps=[{"title": "y"}],
        chat_id="chatB",
    )
    assert other.chat_id == "chatB"


def test_mark_step_progresses_status(store: PlanStore) -> None:
    plan = store.create(
        title="T",
        summary="s",
        steps=[{"title": "a"}, {"title": "b"}],
    )
    started = store.mark_step(plan.id, 1, "in_progress")
    assert started.status == "building"
    done1 = store.mark_step(plan.id, 1, "done")
    assert done1.steps[0].status == "done"
    assert done1.status == "building"  # not yet complete
    done2 = store.mark_step(plan.id, 2, "done")
    assert done2.status == "completed"
    assert done2.is_complete


def test_patch_plan_preserves_existing_step_status(store: PlanStore) -> None:
    plan = store.create(
        title="T",
        summary="s",
        steps=[{"title": "alpha"}, {"title": "beta"}],
    )
    store.mark_step(plan.id, 1, "done")
    patched = store.patch(
        plan.id,
        steps=[{"title": "alpha"}, {"title": "gamma"}, {"title": "beta"}],
    )
    by_title = {s.title: s for s in patched.steps}
    assert by_title["alpha"].status == "done"
    assert by_title["gamma"].status == "pending"
    assert by_title["beta"].status == "pending"
    assert [s.n for s in patched.steps] == [1, 2, 3]


def test_reject_marks_status(store: PlanStore) -> None:
    plan = store.create(title="T", summary="s", steps=[{"title": "a"}])
    rejected = store.reject(plan.id)
    assert rejected.status == "rejected"


def test_delete_plan_removes_files(store: PlanStore) -> None:
    plan = store.create(title="T", summary="s", steps=[{"title": "a"}])
    assert store.delete(plan.id) is True
    with pytest.raises(PlanNotFoundError):
        store.load(plan.id)


def test_create_plan_tool_via_handler(workspace: Workspace, store: PlanStore) -> None:
    out = create_plan_tool(
        workspace,
        {
            "title": "Plan from tool",
            "summary": "Backend test of the agent-facing handler.",
            "steps": [
                {"title": "Step 1", "detail": "do A"},
                {"title": "Step 2", "detail": "do B"},
            ],
            "chat_id": "chat-x",
        },
    )
    assert "error" not in out, out
    assert out["title"] == "Plan from tool"
    assert out["total_steps"] == 2
    assert "markdown" in out
    plan = store.load(out["id"])
    assert plan.chat_id == "chat-x"


def test_plan_tools_expose_full_json_schema(workspace: Workspace) -> None:
    # Regression: without an ``mcp_input_schema`` the agent's argument validator
    # treats every plan-tool call as "extra args" and rejects it before the
    # handler runs (see chat def6d2354e5e47559431b57b: 3× rejected create_plan).
    registry = ToolRegistry()
    register_code_tools(registry, workspace)
    expected = {
        "create_plan": {"title", "summary", "steps"},
        "patch_plan": {"plan_id"},
        "mark_step": {"plan_id", "step_n", "status"},
        "list_plans": set(),
    }
    for name, required in expected.items():
        spec = registry.get(name)
        assert spec is not None, f"{name} not registered"
        schema = spec.mcp_input_schema or {}
        props = set((schema.get("properties") or {}).keys())
        assert required.issubset(props), (
            f"{name} schema missing required props: {required - props}"
        )


def test_create_plan_tool_validation(workspace: Workspace, store: PlanStore) -> None:
    bad = create_plan_tool(workspace, {"title": "T", "summary": "s", "steps": []})
    assert "error" in bad
    bad2 = create_plan_tool(
        workspace, {"title": "T", "summary": "s", "steps": [{"detail": "no title"}]}
    )
    assert "error" in bad2


def test_mark_step_tool_validates_status(workspace: Workspace, store: PlanStore) -> None:
    plan = store.create(title="T", summary="s", steps=[{"title": "a"}])
    bad = mark_step_tool(
        workspace, {"plan_id": plan.id, "step_n": 1, "status": "weird"}
    )
    assert "error" in bad
    ok = mark_step_tool(
        workspace, {"plan_id": plan.id, "step_n": 1, "status": "done"}
    )
    assert ok["status"] == "completed"


def test_patch_plan_tool_requires_a_field(workspace: Workspace, store: PlanStore) -> None:
    plan = store.create(title="T", summary="s", steps=[{"title": "a"}])
    bad = patch_plan_tool(workspace, {"plan_id": plan.id})
    assert "error" in bad


def test_list_plans_tool_filters(workspace: Workspace, store: PlanStore) -> None:
    p1 = store.create(title="A", summary="s", steps=[{"title": "x"}])
    p2 = store.create(title="B", summary="s", steps=[{"title": "y"}])
    store.reject(p2.id)
    out_all = list_plans_tool(workspace, {"status": "all"})
    assert len(out_all["plans"]) == 2
    out_active = list_plans_tool(workspace, {"status": "active"})
    ids = {p["id"] for p in out_active["plans"]}
    assert p1.id in ids and p2.id not in ids
    out_rej = list_plans_tool(workspace, {"status": "rejected"})
    assert {p["id"] for p in out_rej["plans"]} == {p2.id}


def test_plan_markdown_renders_steps(store: PlanStore) -> None:
    plan = store.create(
        title="My plan",
        summary="One paragraph of context.",
        steps=[{"title": "First", "detail": "Do the thing"}],
    )
    md = plan.to_markdown()
    assert "# My plan" in md
    assert "1. [ ] **First**" in md
    assert "Do the thing" in md
