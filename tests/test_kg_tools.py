from __future__ import annotations

from pathlib import Path

import pytest

from codeagents.tools_native import kg as K
from codeagents.workspace import Workspace


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    chats_root = tmp_path / "chats"
    chats_root.mkdir()
    monkeypatch.setattr(
        "codeagents.chat_store.default_chats_dir", lambda: chats_root
    )
    w = Workspace.from_path(tmp_path)
    w.chat_id = "chat-x"
    return w


def test_kg_add_creates_entities_and_relations(ws: Workspace) -> None:
    res = K.kg_add(
        ws,
        {
            "claim": "FastAPI uses Starlette",
            "source_url": "https://fastapi",
            "entities": [
                {"label": "FastAPI", "type": "framework"},
                {"label": "Starlette", "type": "framework"},
            ],
            "relations": [{"src": "FastAPI", "dst": "Starlette", "rel": "uses"}],
        },
    )
    assert res["status"] == "ok"
    assert res["added_entities"] == 2
    assert res["added_relations"] == 1


def test_kg_query_returns_neighbours_and_summary(ws: Workspace) -> None:
    K.kg_add(
        ws,
        {
            "claim": "x",
            "source_url": "https://a",
            "entities": [{"label": "A"}, {"label": "B"}],
            "relations": [{"src": "A", "dst": "B", "rel": "relates"}],
        },
    )
    res = K.kg_query(ws, {"entity": "A", "depth": 1})
    assert res["entity"] == "A"
    assert any(e["dst"] for e in res["neighbours"])


def test_kg_resolve_conflicts(ws: Workspace) -> None:
    K.kg_add(
        ws,
        {
            "claim": "x",
            "entities": [{"label": "X"}, {"label": "Y"}],
            "relations": [{"src": "X", "dst": "Y", "rel": "is_faster_than"}],
        },
    )
    K.kg_add(
        ws,
        {
            "claim": "y",
            "entities": [{"label": "X"}, {"label": "Y"}],
            "relations": [{"src": "X", "dst": "Y", "rel": "is_slower_than"}],
        },
    )
    res = K.kg_resolve_conflicts(ws, {})
    assert len(res["conflicts"]) == 1


def test_kg_ingest_facts_pulls_capitalised_terms(ws: Workspace) -> None:
    n = K.kg_ingest_facts(
        ws,
        report_id="r1",
        facts=[
            {"claim": "FastAPI is a framework based on Starlette", "source_url": "https://a"},
        ],
    )
    # FastAPI -> Starlette gives one relation. "framework" not capitalised -> dropped.
    assert n >= 1


def test_kg_disabled_via_config(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(K, "_kg_enabled", lambda: False)
    res = K.kg_add(ws, {"claim": "x", "entities": [{"label": "A"}]})
    assert res["status"] == "disabled"
    assert res["added_entities"] == 0
    res2 = K.kg_query(ws, {"entity": "A"})
    assert res2["status"] == "disabled"
    res3 = K.kg_resolve_conflicts(ws, {})
    assert res3["status"] == "disabled"
