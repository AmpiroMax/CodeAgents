"""LightRAG-style knowledge-graph storage (Phase 2.C.1).

Per-chat SQLite database holding extracted entities, relations between
them, and hierarchical *communities* (Leiden clusters with summary text).

Schema
------
``entities(id, label, type, embedding BLOB, sources_json, created_ts)``
``relations(src_id, dst_id, rel, weight, sources_json, created_ts)``
``communities(id, level, member_ids_json, summary, dirty, updated_ts)``

The ``dirty`` flag on ``communities`` is set when the underlying graph
changes; the background indexer (Phase 2.C.2) consumes it to decide when
to recompute the leiden clustering.

Why per-chat
------------
A KG that crosses chats would be powerful but complicates rollback /
delete-chat semantics. We start small and re-evaluate after the first
few research sessions.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Entity:
    id: str
    label: str
    type: str = ""
    embedding: list[float] | None = None
    sources: list[str] = field(default_factory=list)

    def to_row(self) -> tuple[Any, ...]:
        emb_blob: bytes | None = None
        if self.embedding:
            emb_blob = json.dumps(self.embedding, separators=(",", ":")).encode("utf-8")
        return (
            self.id,
            self.label,
            self.type,
            emb_blob,
            json.dumps(self.sources, ensure_ascii=False),
            time.time(),
        )


@dataclass
class Relation:
    src_id: str
    dst_id: str
    rel: str
    weight: float = 1.0
    sources: list[str] = field(default_factory=list)


@dataclass
class Community:
    id: str
    level: int
    member_ids: list[str]
    summary: str = ""


class KGStore:
    """File-backed KG. Safe for single-process append + many readers."""

    def __init__(self, chat_dir: Path) -> None:
        self.chat_dir = Path(chat_dir)
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.chat_dir / "kg.sqlite3"
        with sqlite3.connect(self.db_path) as conn:
            self._init(conn)

    # ── Schema ────────────────────────────────────────────────────────

    def _init(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            create table if not exists entities (
                id text primary key,
                label text not null,
                type text not null default '',
                embedding blob,
                sources_json text not null default '[]',
                created_ts real not null
            );
            create index if not exists idx_entities_label on entities(label);
            create table if not exists relations (
                src_id text not null,
                dst_id text not null,
                rel text not null,
                weight real not null default 1.0,
                sources_json text not null default '[]',
                created_ts real not null,
                primary key (src_id, dst_id, rel)
            );
            create index if not exists idx_relations_src on relations(src_id);
            create index if not exists idx_relations_dst on relations(dst_id);
            create table if not exists communities (
                id text primary key,
                level integer not null default 0,
                member_ids_json text not null default '[]',
                summary text not null default '',
                dirty integer not null default 1,
                updated_ts real not null
            );
            create table if not exists kg_meta (
                key text primary key,
                value text not null
            );
            """
        )
        conn.commit()

    # ── Mutations ─────────────────────────────────────────────────────

    def add_entity(self, entity: Entity) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                insert into entities(id,label,type,embedding,sources_json,created_ts)
                values (?,?,?,?,?,?)
                on conflict(id) do update set
                    label = excluded.label,
                    type = case when excluded.type != '' then excluded.type else entities.type end,
                    embedding = coalesce(excluded.embedding, entities.embedding),
                    sources_json = excluded.sources_json
                """,
                entity.to_row(),
            )
            self._mark_dirty(conn)
            conn.commit()

    def add_relation(self, rel: Relation) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                insert into relations(src_id,dst_id,rel,weight,sources_json,created_ts)
                values (?,?,?,?,?,?)
                on conflict(src_id,dst_id,rel) do update set
                    weight = relations.weight + excluded.weight,
                    sources_json = excluded.sources_json
                """,
                (
                    rel.src_id,
                    rel.dst_id,
                    rel.rel,
                    rel.weight,
                    json.dumps(rel.sources, ensure_ascii=False),
                    time.time(),
                ),
            )
            self._mark_dirty(conn)
            conn.commit()

    def upsert_community(self, community: Community) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                insert into communities(id,level,member_ids_json,summary,dirty,updated_ts)
                values (?,?,?,?,0,?)
                on conflict(id) do update set
                    level = excluded.level,
                    member_ids_json = excluded.member_ids_json,
                    summary = excluded.summary,
                    dirty = 0,
                    updated_ts = excluded.updated_ts
                """,
                (
                    community.id,
                    community.level,
                    json.dumps(community.member_ids, ensure_ascii=False),
                    community.summary,
                    time.time(),
                ),
            )
            conn.commit()

    def _mark_dirty(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            insert into kg_meta(key,value) values('graph_dirty', ?)
            on conflict(key) do update set value = excluded.value
            """,
            (str(int(time.time())),),
        )

    def is_dirty(self) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "select value from kg_meta where key='graph_dirty'"
            ).fetchone()
            indexed = conn.execute(
                "select value from kg_meta where key='last_indexed'"
            ).fetchone()
        if not row:
            return False
        if not indexed:
            return True
        try:
            return int(row[0]) > int(indexed[0])
        except ValueError:
            return True

    def mark_indexed(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                insert into kg_meta(key,value) values('last_indexed', ?)
                on conflict(key) do update set value = excluded.value
                """,
                (str(int(time.time())),),
            )
            conn.commit()

    # ── Queries ───────────────────────────────────────────────────────

    def get_entity(self, entity_id: str) -> Entity | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "select id,label,type,embedding,sources_json from entities where id=?",
                (entity_id,),
            ).fetchone()
        return _row_to_entity(row) if row else None

    def find_entity_by_label(self, label: str) -> Entity | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "select id,label,type,embedding,sources_json from entities where label=? limit 1",
                (label,),
            ).fetchone()
        return _row_to_entity(row) if row else None

    def neighbours(self, entity_id: str, *, depth: int = 1) -> list[dict[str, Any]]:
        seen: set[str] = {entity_id}
        frontier: list[str] = [entity_id]
        edges: list[dict[str, Any]] = []
        with sqlite3.connect(self.db_path) as conn:
            for _ in range(max(0, depth)):
                if not frontier:
                    break
                qmarks = ",".join("?" * len(frontier))
                rows = conn.execute(
                    f"""
                    select src_id,dst_id,rel,weight from relations
                    where src_id in ({qmarks}) or dst_id in ({qmarks})
                    """,
                    (*frontier, *frontier),
                ).fetchall()
                next_frontier: list[str] = []
                for src, dst, rel, weight in rows:
                    edges.append({"src": src, "dst": dst, "rel": rel, "weight": weight})
                    for nid in (src, dst):
                        if nid not in seen:
                            seen.add(nid)
                            next_frontier.append(nid)
                frontier = next_frontier
        return edges

    def list_communities(self, *, level: int | None = None) -> list[Community]:
        with sqlite3.connect(self.db_path) as conn:
            if level is None:
                rows = conn.execute(
                    "select id,level,member_ids_json,summary from communities"
                ).fetchall()
            else:
                rows = conn.execute(
                    "select id,level,member_ids_json,summary from communities where level=?",
                    (level,),
                ).fetchall()
        return [
            Community(
                id=row[0],
                level=int(row[1]),
                member_ids=json.loads(row[2]),
                summary=str(row[3]),
            )
            for row in rows
        ]

    def all_relations(self) -> Iterable[Relation]:
        with sqlite3.connect(self.db_path) as conn:
            for row in conn.execute(
                "select src_id,dst_id,rel,weight,sources_json from relations"
            ):
                yield Relation(
                    src_id=row[0],
                    dst_id=row[1],
                    rel=row[2],
                    weight=float(row[3]),
                    sources=json.loads(row[4] or "[]"),
                )

    def all_entities(self) -> list[Entity]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "select id,label,type,embedding,sources_json from entities"
            ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def detect_conflicts(self) -> list[dict[str, Any]]:
        """Return pairs of relations that disagree on the same (src, dst).

        A *conflict* is two relations between the same entity pair with
        different ``rel`` strings. This is the cheapest signal we can
        compute over a flat triplestore and matches the LightRAG
        "is/is-not" disambiguation use case.
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                select a.src_id, a.dst_id, a.rel, b.rel,
                       a.sources_json, b.sources_json
                from relations a join relations b
                  on a.src_id=b.src_id and a.dst_id=b.dst_id
                where a.rel < b.rel
                """,
            ).fetchall()
        out: list[dict[str, Any]] = []
        for src, dst, rel_a, rel_b, sa, sb in rows:
            out.append(
                {
                    "src_id": src,
                    "dst_id": dst,
                    "rel_a": rel_a,
                    "rel_b": rel_b,
                    "sources_a": json.loads(sa or "[]"),
                    "sources_b": json.loads(sb or "[]"),
                }
            )
        return out


def _row_to_entity(row: Any) -> Entity:
    embedding: list[float] | None = None
    if row[3]:
        try:
            embedding = json.loads(row[3].decode("utf-8") if isinstance(row[3], bytes) else row[3])
        except Exception:
            embedding = None
    return Entity(
        id=str(row[0]),
        label=str(row[1]),
        type=str(row[2] or ""),
        embedding=embedding,
        sources=json.loads(row[4] or "[]"),
    )


__all__ = [
    "Community",
    "Entity",
    "KGStore",
    "Relation",
]
