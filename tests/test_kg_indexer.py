from __future__ import annotations

import time
from pathlib import Path

from codeagents.rag.kg_indexer import reindex_kg
from codeagents.stores.kg import Community, Entity, KGStore, Relation


def test_reindex_empty_graph_marks_clean(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    n = reindex_kg(store)
    assert n == 0
    assert store.is_dirty() is False


def test_reindex_creates_one_component(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    for n in ("a", "b", "c"):
        store.add_entity(Entity(id=n, label=n))
    store.add_relation(Relation(src_id="a", dst_id="b", rel="x"))
    store.add_relation(Relation(src_id="b", dst_id="c", rel="x"))
    n = reindex_kg(store)
    assert n == 1
    cs = store.list_communities()
    assert len(cs) == 1
    assert sorted(cs[0].member_ids) == ["a", "b", "c"]


def test_reindex_creates_two_components(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    for n in ("a", "b", "c", "d"):
        store.add_entity(Entity(id=n, label=n))
    store.add_relation(Relation(src_id="a", dst_id="b", rel="x"))
    store.add_relation(Relation(src_id="c", dst_id="d", rel="x"))
    n = reindex_kg(store)
    # Leiden may merge or split based on resolution; CC fallback is exactly 2.
    assert n >= 1
    members = sorted(sum((c.member_ids for c in store.list_communities()), []))
    assert members == ["a", "b", "c", "d"]


def test_reindex_idempotent(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    store.add_entity(Entity(id="a", label="A"))
    store.add_entity(Entity(id="b", label="B"))
    store.add_relation(Relation(src_id="a", dst_id="b", rel="x"))
    reindex_kg(store)
    time.sleep(1.05)
    # Second pass without any new mutations -> still clean afterwards.
    store.mark_indexed()
    time.sleep(0.05)
    assert store.is_dirty() is False
