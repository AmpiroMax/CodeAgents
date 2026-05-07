from __future__ import annotations

import time
from pathlib import Path

from codeagents.kg_store import Community, Entity, KGStore, Relation


def test_add_and_get_entity(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    store.add_entity(
        Entity(id="e1", label="FastAPI", type="framework", sources=["https://a"])
    )
    got = store.get_entity("e1")
    assert got is not None
    assert got.label == "FastAPI"
    assert got.type == "framework"
    assert got.sources == ["https://a"]


def test_upsert_entity_keeps_type_when_blank(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    store.add_entity(Entity(id="e1", label="A", type="orig"))
    store.add_entity(Entity(id="e1", label="A2", type=""))
    e = store.get_entity("e1")
    assert e is not None
    assert e.label == "A2"
    assert e.type == "orig"


def test_relations_and_neighbours(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    for nid in ("a", "b", "c"):
        store.add_entity(Entity(id=nid, label=nid))
    store.add_relation(Relation(src_id="a", dst_id="b", rel="uses"))
    store.add_relation(Relation(src_id="b", dst_id="c", rel="depends_on"))

    nb1 = store.neighbours("a", depth=1)
    assert len(nb1) == 1
    nb2 = store.neighbours("a", depth=2)
    # Both edges are reachable from a within depth=2.
    rels = sorted({e["rel"] for e in nb2})
    assert rels == ["depends_on", "uses"]


def test_relation_weight_accumulates(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    store.add_relation(Relation(src_id="a", dst_id="b", rel="x", weight=1.0))
    store.add_relation(Relation(src_id="a", dst_id="b", rel="x", weight=2.5))
    rels = list(store.all_relations())
    assert len(rels) == 1
    assert rels[0].weight == 3.5


def test_dirty_flag_round_trip(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    assert store.is_dirty() is False
    store.add_entity(Entity(id="a", label="A"))
    assert store.is_dirty() is True
    # Sleep a tiny bit so the indexed timestamp is strictly newer.
    time.sleep(1.05)
    store.mark_indexed()
    assert store.is_dirty() is False


def test_community_upsert(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    store.upsert_community(Community(id="c1", level=0, member_ids=["a", "b"], summary="alpha"))
    items = store.list_communities()
    assert len(items) == 1
    assert items[0].summary == "alpha"
    store.upsert_community(Community(id="c1", level=0, member_ids=["a", "b", "c"], summary="beta"))
    items2 = store.list_communities()
    assert len(items2) == 1
    assert items2[0].summary == "beta"
    assert items2[0].member_ids == ["a", "b", "c"]


def test_detect_conflicts(tmp_path: Path) -> None:
    store = KGStore(tmp_path)
    for nid in ("a", "b"):
        store.add_entity(Entity(id=nid, label=nid))
    store.add_relation(
        Relation(src_id="a", dst_id="b", rel="is_faster_than", sources=["s1"])
    )
    store.add_relation(
        Relation(src_id="a", dst_id="b", rel="is_slower_than", sources=["s2"])
    )
    conflicts = store.detect_conflicts()
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["src_id"] == "a"
    assert c["dst_id"] == "b"
    assert {c["rel_a"], c["rel_b"]} == {"is_faster_than", "is_slower_than"}
