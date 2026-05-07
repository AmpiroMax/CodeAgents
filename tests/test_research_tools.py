"""Unit tests for the deep-research tool surface (Phase 2.B.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codeagents.stores.research import ResearchStore
from codeagents.tools import research as R
from codeagents.core.workspace import Workspace


# ── Fakes ────────────────────────────────────────────────────────────


class _FakeRuntime:
    """Yields a single delta whose content is provided by ``script[next_idx]``.

    Tests can inject deterministic LLM responses without running ollama.
    """

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def chat_stream(self, *, model, messages):
        if self.calls >= len(self.script):
            payload = "{}"
        else:
            payload = self.script[self.calls]
        self.calls += 1
        yield {"type": "delta", "content": payload}


class _FakeCfg:
    class _Runtime:
        embedding_model = None
        model = "fake-model"

    runtime = _Runtime()
    models: dict[str, Any] = {}


@pytest.fixture
def fake_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    ws = Workspace.from_path(tmp_path)
    ws.chat_id = "chat-1"
    # Point chat_store.default_chats_dir at our tmp.
    chats_root = tmp_path / "chats"
    chats_root.mkdir()
    monkeypatch.setattr(
        "codeagents.stores.chat.default_chats_dir", lambda: chats_root
    )
    return ws


def _set_runtime(script: list[str]) -> _FakeRuntime:
    rt = _FakeRuntime(script)
    R.set_runtime_factory(lambda: (rt, _FakeCfg()))
    return rt


def setup_function(_fn):
    R.reset_runtime_factory()


def teardown_function(_fn):
    R.reset_runtime_factory()


# ── Tests ────────────────────────────────────────────────────────────


def test_clarify_creates_report_with_questions(fake_workspace: Workspace) -> None:
    _set_runtime(
        [
            json.dumps({"questions": ["Audience?", "Timeframe?", "Tools?"]}),
        ]
    )
    res = R.clarify_research(fake_workspace, {"query": "Compare FastAPI and Litestar"})
    assert res["status"] == "awaiting_clarify"
    assert len(res["questions"]) == 3
    assert res["report_id"]


def test_submit_clarify_answers_advances_status(fake_workspace: Workspace) -> None:
    _set_runtime([json.dumps({"questions": ["A?", "B?", "C?"]})])
    rep = R.clarify_research(fake_workspace, {"query": "x"})
    res = R.submit_clarify_answers(
        fake_workspace,
        {
            "report_id": rep["report_id"],
            "answers": [
                {"question": "A?", "answer": "yes"},
                {"question": "B?", "answer": "1d"},
            ],
        },
    )
    assert res["status"] == "ready_to_plan"
    assert len(res["answers"]) == 2


def test_submit_clarify_skipped(fake_workspace: Workspace) -> None:
    _set_runtime([json.dumps({"questions": ["A?", "B?"]})])
    rep = R.clarify_research(fake_workspace, {"query": "x"})
    res = R.submit_clarify_answers(
        fake_workspace, {"report_id": rep["report_id"], "skipped": True}
    )
    assert res["status"] == "ready_to_plan"
    assert res["answers"] == []


def test_plan_research_requires_query_or_report_id(fake_workspace: Workspace) -> None:
    # In the chat-first flow plan_research accepts either ``query`` or an
    # existing ``report_id``. With neither, it must error out cleanly.
    res = R.plan_research(fake_workspace, {})
    assert "error" in res
    assert "missing argument" in res["error"]


def test_plan_research_creates_report_from_query(fake_workspace: Workspace) -> None:
    _set_runtime(
        [
            json.dumps(
                {
                    "sections": [
                        {"title": "Intro", "questions": ["What is FastAPI?"]},
                    ]
                }
            ),
        ]
    )
    res = R.plan_research(fake_workspace, {"query": "FastAPI overview, audience: backend devs"})
    assert res["status"] == "researching"
    assert res.get("report_id")
    assert [s["title"] for s in res["outline"]] == ["Intro"]


def test_plan_research_builds_outline(fake_workspace: Workspace) -> None:
    _set_runtime(
        [
            json.dumps({"questions": ["A?"]}),
            json.dumps(
                {
                    "sections": [
                        {"title": "Intro", "questions": ["What is FastAPI?"]},
                        {"title": "Compare", "questions": ["FastAPI vs Litestar perf?"]},
                    ]
                }
            ),
        ]
    )
    rep = R.clarify_research(fake_workspace, {"query": "x"})
    R.submit_clarify_answers(fake_workspace, {"report_id": rep["report_id"], "skipped": True})
    res = R.plan_research(fake_workspace, {"report_id": rep["report_id"]})
    assert res["status"] == "researching"
    assert [s["title"] for s in res["outline"]] == ["Intro", "Compare"]


def test_expand_query_returns_n_queries(fake_workspace: Workspace) -> None:
    _set_runtime([json.dumps({"queries": ["q1", "q2", "q3", "q4"]})])
    res = R.expand_query(fake_workspace, {"subgoal": "FastAPI streaming", "n": 3})
    assert res["queries"] == ["q1", "q2", "q3"]


def test_extract_facts_appends_to_section_and_notes(fake_workspace: Workspace) -> None:
    # Sequence: clarify (1), plan (1), extract (1)
    _set_runtime(
        [
            json.dumps({"questions": ["q1?"]}),
            json.dumps(
                {"sections": [{"title": "S", "questions": ["x"]}]}
            ),
            json.dumps(
                {
                    "facts": [
                        {"claim": "FastAPI is async", "span": "FastAPI is async"},
                        {"claim": "Litestar is fast", "span": "Litestar is fast"},
                    ]
                }
            ),
        ]
    )
    rep = R.clarify_research(fake_workspace, {"query": "x"})
    R.submit_clarify_answers(fake_workspace, {"report_id": rep["report_id"], "skipped": True})
    R.plan_research(fake_workspace, {"report_id": rep["report_id"]})
    res = R.extract_facts(
        fake_workspace,
        {
            "text": "FastAPI is async. Litestar is fast. They both run on uvicorn.",
            "source_url": "https://example.com/post",
            "report_id": rep["report_id"],
            "section_idx": 0,
        },
    )
    assert res["count"] == 2
    assert all("source_url" in f for f in res["facts"])

    # Both notes.jsonl and the section facts are populated.
    from codeagents.stores.chat import default_chats_dir

    store = ResearchStore(default_chats_dir())
    notes = list(store.iter_notes("chat-1", rep["report_id"]))
    assert len(notes) == 2
    loaded = store.load("chat-1", rep["report_id"])
    assert len(loaded.outline[0].facts) == 2
    assert len(loaded.sources) == 1


def test_draft_section_emits_markdown(fake_workspace: Workspace) -> None:
    _set_runtime(
        [
            json.dumps({"questions": ["q?"]}),
            json.dumps({"sections": [{"title": "S", "questions": ["x"]}]}),
            json.dumps({"facts": [{"claim": "C1", "span": "C1"}]}),
            "Body of section with [1].",  # draft_section LLM response
        ]
    )
    rep = R.clarify_research(fake_workspace, {"query": "x"})
    R.submit_clarify_answers(fake_workspace, {"report_id": rep["report_id"], "skipped": True})
    R.plan_research(fake_workspace, {"report_id": rep["report_id"]})
    R.extract_facts(
        fake_workspace,
        {
            "text": "C1 source.",
            "source_url": "https://a",
            "report_id": rep["report_id"],
            "section_idx": 0,
        },
    )
    res = R.draft_section(
        fake_workspace, {"report_id": rep["report_id"], "section_idx": 0}
    )
    assert res["title"] == "S"
    assert "Body of section" in res["draft"]


def test_assemble_report_includes_kg_conflicts(fake_workspace: Workspace) -> None:
    """Phase 2.C.4: assemble_report renders a 'Conflicting claims' block."""
    from codeagents.tools import kg as K

    _set_runtime(
        [
            json.dumps({"questions": ["q?"]}),
            json.dumps({"sections": [{"title": "S", "questions": ["x"]}]}),
            "Body",
        ]
    )
    rep = R.clarify_research(fake_workspace, {"query": "x"})
    R.submit_clarify_answers(fake_workspace, {"report_id": rep["report_id"], "skipped": True})
    R.plan_research(fake_workspace, {"report_id": rep["report_id"]})
    R.draft_section(fake_workspace, {"report_id": rep["report_id"], "section_idx": 0})

    # Inject conflicting claims directly via kg_add.
    K.kg_add(
        fake_workspace,
        {
            "claim": "X faster",
            "entities": [{"label": "X"}, {"label": "Y"}],
            "relations": [{"src": "X", "dst": "Y", "rel": "is_faster_than"}],
        },
    )
    K.kg_add(
        fake_workspace,
        {
            "claim": "X slower",
            "entities": [{"label": "X"}, {"label": "Y"}],
            "relations": [{"src": "X", "dst": "Y", "rel": "is_slower_than"}],
        },
    )

    res = R.assemble_report(fake_workspace, {"report_id": rep["report_id"]})
    assert "## Conflicting claims" in res["markdown"]
    assert "is_faster_than" in res["markdown"]


def test_assemble_report_writes_markdown_with_sources(fake_workspace: Workspace) -> None:
    _set_runtime(
        [
            json.dumps({"questions": ["q?"]}),
            json.dumps({"sections": [{"title": "S", "questions": ["x"]}]}),
            json.dumps({"facts": [{"claim": "C1", "span": "C1"}]}),
            "Body [1].",
        ]
    )
    rep = R.clarify_research(fake_workspace, {"query": "x"})
    R.submit_clarify_answers(fake_workspace, {"report_id": rep["report_id"], "skipped": True})
    R.plan_research(fake_workspace, {"report_id": rep["report_id"]})
    R.extract_facts(
        fake_workspace,
        {
            "text": "C1 source.",
            "source_url": "https://example.com/a",
            "report_id": rep["report_id"],
            "section_idx": 0,
        },
    )
    R.draft_section(fake_workspace, {"report_id": rep["report_id"], "section_idx": 0})
    res = R.assemble_report(fake_workspace, {"report_id": rep["report_id"]})
    assert res["status"] == "done"
    assert "# Research report" in res["markdown"]
    assert "## Sources" in res["markdown"]
    assert "https://example.com/a" in res["markdown"]
