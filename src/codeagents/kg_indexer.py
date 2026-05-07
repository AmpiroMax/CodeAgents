"""Background community-detection worker for the KG (Phase 2.C.2).

When ``KGStore.is_dirty()`` is true, recompute community structure and
write the resulting ``Community`` rows. We try ``python-igraph`` +
``leidenalg`` (proper Leiden, ships in ``[kg]`` extras); when those
aren't installed we fall back to undirected connected components — far
weaker but enough to keep the rest of the research pipeline functional.

Public surface
--------------
- :func:`reindex_kg` — synchronous; safe to call from tests.
- :func:`start_kg_worker` — launches a daemon thread that polls for
  dirty graphs and reindexes every ``interval`` seconds.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from pathlib import Path

from codeagents.kg_store import Community, KGStore

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 60.0


def reindex_kg(store: KGStore) -> int:
    """Recompute communities. Returns the number of communities written."""
    entities = {e.id for e in store.all_entities()}
    edges = [(r.src_id, r.dst_id, r.weight) for r in store.all_relations()]
    if not entities:
        store.mark_indexed()
        return 0

    communities = _leiden(entities, edges)
    if communities is None:
        communities = _connected_components(entities, edges)

    for idx, members in enumerate(communities):
        cid = f"c{idx}-{len(members)}"
        summary = _summarise_members(members)
        store.upsert_community(
            Community(id=cid, level=0, member_ids=sorted(members), summary=summary)
        )
    store.mark_indexed()
    return len(communities)


def _summarise_members(members: list[str] | set[str]) -> str:
    items = sorted(members)
    if not items:
        return ""
    head = ", ".join(items[:8])
    if len(items) > 8:
        head += f", … (+{len(items) - 8} more)"
    return f"Cluster of {len(items)} entities: {head}"


def _leiden(
    entity_ids: set[str], edges: list[tuple[str, str, float]]
) -> list[list[str]] | None:
    """Run Leiden community detection. Returns None if deps missing."""
    try:
        import igraph as ig  # type: ignore[import-untyped]
        import leidenalg  # type: ignore[import-untyped]
    except Exception:
        return None
    try:
        nodes = sorted(entity_ids)
        idx = {n: i for i, n in enumerate(nodes)}
        graph = ig.Graph()
        graph.add_vertices(len(nodes))
        graph.vs["name"] = nodes
        if edges:
            graph.add_edges([(idx[s], idx[d]) for s, d, _w in edges if s in idx and d in idx])
            graph.es["weight"] = [w for s, d, w in edges if s in idx and d in idx]
        partition = leidenalg.find_partition(graph, leidenalg.RBConfigurationVertexPartition)
        out: list[list[str]] = []
        for membership in partition:
            out.append([nodes[i] for i in membership])
        return out
    except Exception as exc:  # pragma: no cover - depends on env
        logger.debug("leiden failed, falling back to CC: %s", exc)
        return None


def _connected_components(
    entity_ids: set[str], edges: list[tuple[str, str, float]]
) -> list[list[str]]:
    """Plain undirected connected components via union-find."""
    parent: dict[str, str] = {n: n for n in entity_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for s, d, _w in edges:
        if s in parent and d in parent:
            union(s, d)
    groups: dict[str, list[str]] = defaultdict(list)
    for n in entity_ids:
        groups[find(n)].append(n)
    return [sorted(members) for members in groups.values()]


# ── Daemon loop ──────────────────────────────────────────────────────


def start_kg_worker(
    *,
    chat_dirs: list[Path] | None = None,
    interval: float = DEFAULT_INTERVAL_SEC,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Spawn a daemon thread that reindexes any dirty KGs.

    ``chat_dirs`` is optional: when None, the worker walks the chats root
    on each tick. Tests pass an explicit list and a ``stop_event`` so
    they can shut the worker down deterministically.
    """

    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.wait(interval):
            try:
                dirs = chat_dirs or _discover_chat_dirs()
            except Exception:
                continue
            for d in dirs:
                try:
                    store = KGStore(d)
                    if store.is_dirty():
                        reindex_kg(store)
                except Exception as exc:  # pragma: no cover
                    logger.debug("reindex_kg failed for %s: %s", d, exc)

    thread = threading.Thread(target=_loop, name="kg-indexer", daemon=True)
    thread.start()
    return thread


def _discover_chat_dirs() -> list[Path]:
    try:
        from codeagents.chat_store import default_chats_dir

        root = default_chats_dir()
    except Exception:
        return []
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir()]


__all__ = [
    "DEFAULT_INTERVAL_SEC",
    "reindex_kg",
    "start_kg_worker",
]
