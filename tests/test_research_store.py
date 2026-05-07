from __future__ import annotations

from pathlib import Path

from codeagents.stores.research import (
    ResearchReport,
    ResearchSection,
    ResearchStore,
)


def test_create_and_load_round_trip(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    rep = store.create(chat_id="c1", query="Compare FastAPI and Litestar")
    assert rep.id
    assert rep.status == "created"
    assert rep.created_ts > 0

    loaded = store.load("c1", rep.id)
    assert loaded.id == rep.id
    assert loaded.query == "Compare FastAPI and Litestar"


def test_set_status_persists(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    rep = store.create(chat_id="c1", query="x")
    store.set_status("c1", rep.id, "researching")
    assert store.load("c1", rep.id).status == "researching"


def test_save_section(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    rep = store.create(chat_id="c1", query="x")
    rep.outline = [ResearchSection(title="Intro"), ResearchSection(title="Comparison")]
    store.save(rep)

    new_section = ResearchSection(
        title="Intro",
        questions=["What is X?"],
        draft="Body of the intro.",
        status="drafted",
    )
    store.save_section("c1", rep.id, index=0, section=new_section)
    loaded = store.load("c1", rep.id)
    assert loaded.outline[0].draft == "Body of the intro."
    assert loaded.outline[0].status == "drafted"
    assert loaded.outline[1].title == "Comparison"


def test_clarify_round_trip(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    rep = store.create(chat_id="c1", query="x")
    rep.clarify_questions = ["Q1?", "Q2?", "Q3?"]
    rep.clarify_answers = [{"question": "Q1?", "answer": "A1"}]
    rep.clarify_skipped = False
    store.save(rep)

    loaded = store.load("c1", rep.id)
    assert loaded.clarify_questions == ["Q1?", "Q2?", "Q3?"]
    assert loaded.clarify_answers == [{"question": "Q1?", "answer": "A1"}]


def test_list_orders_newest_first(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    a = store.create(chat_id="c1", query="a")
    b = store.create(chat_id="c1", query="b")
    # b is created after a -> b should sort first.
    items = store.list("c1")
    assert [r.id for r in items] == [b.id, a.id]


def test_notes_jsonl_append_and_read(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    rep = store.create(chat_id="c1", query="x")
    store.append_note("c1", rep.id, {"claim": "X", "source": "https://a"})
    store.append_note("c1", rep.id, {"claim": "Y", "source": "https://b"})
    notes = list(store.iter_notes("c1", rep.id))
    assert [n["claim"] for n in notes] == ["X", "Y"]


def test_markdown_round_trip(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    rep = store.create(chat_id="c1", query="x")
    md = "# Report\n\nBody"
    p = store.write_markdown("c1", rep.id, md)
    assert p.exists()
    assert store.read_markdown("c1", rep.id) == md


def test_load_missing_raises(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path)
    try:
        store.load("c1", "nonexistent")
    except FileNotFoundError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected FileNotFoundError")
