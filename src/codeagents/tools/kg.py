"""Knowledge-graph tools (Phase 2.C.3).

Three tools, all read-mostly and exposed only in ``mode=research``:

- ``kg_add({claim, source_url, entities[], relations[]})`` — usually
  invoked as a side-effect of ``extract_facts`` (Phase 2.C.4) but also
  callable directly. Idempotent on (entity_id, relation triple).
- ``kg_query(entity, depth=1)`` — return neighbours and any matching
  community summary. Used during ``draft_section`` to deduplicate
  claims and find context.
- ``kg_resolve_conflicts(report_id)`` — list disagreements between
  relations sharing the same (src, dst). Used by ``assemble_report``
  to render a "Conflicting claims" appendix.

The KG is gated behind the ``research.kg_enabled`` flag in
``config/models.toml``: when false the tools log a notice and return
empty results so the rest of the research flow keeps working.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from codeagents.stores.kg import Entity, KGStore, Relation
from codeagents.core.permissions import Permission
from codeagents.tools import ParamSpec, ToolRegistry, ToolSpec
from codeagents.core.workspace import Workspace


def _kg_enabled() -> bool:
    """Resolve the ``research.kg_enabled`` flag from config/models.toml.

    Defaults to True so the tool surface is alive out of the box. The
    flag is intended to disable KG ingestion when leiden deps aren't
    installed and the user wants minimal output noise.
    """
    try:
        from codeagents.core.config import PROJECT_ROOT, load_toml

        raw = load_toml(PROJECT_ROOT / "config" / "models.toml")
        return bool(raw.get("research", {}).get("kg_enabled", True))
    except Exception:
        return True


def _kg_dir(workspace: Workspace) -> "Any":
    chat_id = (workspace.chat_id or "").strip()
    if not chat_id:
        return None
    try:
        from codeagents.stores.chat import default_chats_dir

        return default_chats_dir() / chat_id
    except Exception:
        return None


def _slug(text: str) -> str:
    """Stable, slug-safe entity id derived from a label."""
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not base:
        base = "entity"
    if len(base) <= 32:
        return base
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{base[:24]}-{h}"


# ── Tool handlers ────────────────────────────────────────────────────


def kg_add(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    if not _kg_enabled():
        return {"status": "disabled", "added_entities": 0, "added_relations": 0}
    chat_dir = _kg_dir(workspace)
    if chat_dir is None:
        return {"error": "no active chat"}

    claim = str(args.get("claim", "")).strip()
    source_url = str(args.get("source_url", "")).strip()
    raw_entities = args.get("entities") or []
    raw_relations = args.get("relations") or []
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        return {"error": "entities and relations must be lists"}

    store = KGStore(chat_dir)
    added_e = 0
    seen: set[str] = set()
    label_to_id: dict[str, str] = {}
    for item in raw_entities:
        label: str = ""
        etype: str = ""
        eid: str = ""
        if isinstance(item, str):
            label = item
        elif isinstance(item, dict):
            label = str(item.get("label", item.get("name", ""))).strip()
            etype = str(item.get("type", "")).strip()
            eid = str(item.get("id", "")).strip()
        if not label:
            continue
        eid = eid or _slug(label)
        if eid in seen:
            continue
        seen.add(eid)
        label_to_id[label.lower()] = eid
        sources = [source_url] if source_url else []
        store.add_entity(Entity(id=eid, label=label, type=etype, sources=sources))
        added_e += 1

    added_r = 0
    for rel in raw_relations:
        if not isinstance(rel, dict):
            continue
        src = str(rel.get("src", rel.get("source", ""))).strip()
        dst = str(rel.get("dst", rel.get("target", ""))).strip()
        rname = str(rel.get("rel", rel.get("type", ""))).strip() or "related_to"
        if not src or not dst:
            continue
        sid = label_to_id.get(src.lower(), _slug(src))
        did = label_to_id.get(dst.lower(), _slug(dst))
        store.add_relation(
            Relation(
                src_id=sid,
                dst_id=did,
                rel=rname,
                weight=float(rel.get("weight", 1.0)),
                sources=[source_url] if source_url else [],
            )
        )
        added_r += 1

    return {
        "status": "ok",
        "claim": claim,
        "added_entities": added_e,
        "added_relations": added_r,
    }


def kg_query(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    if not _kg_enabled():
        return {"status": "disabled", "neighbours": [], "summary": ""}
    chat_dir = _kg_dir(workspace)
    if chat_dir is None:
        return {"error": "no active chat"}

    label = str(args.get("entity", "")).strip()
    depth = max(1, min(int(args.get("depth", 1)), 3))
    if not label:
        return {"error": "entity required"}

    store = KGStore(chat_dir)
    target = store.find_entity_by_label(label) or store.get_entity(_slug(label))
    if target is None:
        return {"neighbours": [], "summary": "", "note": "entity not found"}

    edges = store.neighbours(target.id, depth=depth)
    summary = ""
    for community in store.list_communities():
        if target.id in community.member_ids:
            summary = community.summary
            break
    return {
        "entity": target.label,
        "neighbours": edges,
        "summary": summary,
    }


def kg_resolve_conflicts(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    if not _kg_enabled():
        return {"status": "disabled", "conflicts": []}
    chat_dir = _kg_dir(workspace)
    if chat_dir is None:
        return {"error": "no active chat"}
    store = KGStore(chat_dir)
    return {"conflicts": store.detect_conflicts()}


# ── Side-effect helper used by ``extract_facts`` (Phase 2.C.4) ───────


def kg_ingest_facts(
    workspace: Workspace, *, report_id: str, facts: list[dict[str, Any]]
) -> int:
    """Best-effort fact ingestion. Returns number of triples added."""
    if not _kg_enabled():
        return 0
    chat_dir = _kg_dir(workspace)
    if chat_dir is None:
        return 0
    store = KGStore(chat_dir)
    added = 0
    for fact in facts:
        claim = str(fact.get("claim", "")).strip()
        if not claim:
            continue
        url = str(fact.get("source_url", "")).strip()
        # Cheap heuristic entity extraction: capitalised words >= 3 chars.
        entities = re.findall(r"\b[A-Z][A-Za-z0-9_+-]{2,}\b", claim)
        unique_entities = list({e: None for e in entities}.keys())[:6]
        for label in unique_entities:
            store.add_entity(
                Entity(id=_slug(label), label=label, sources=[url] if url else [])
            )
        # Pairwise relate the entities in claim order.
        for i in range(len(unique_entities) - 1):
            store.add_relation(
                Relation(
                    src_id=_slug(unique_entities[i]),
                    dst_id=_slug(unique_entities[i + 1]),
                    rel="co-mentioned",
                    sources=[url] if url else [],
                )
            )
            added += 1
    return added


# ── Registration ─────────────────────────────────────────────────────


def register_kg_tools(registry: ToolRegistry, workspace: Workspace) -> None:
    def _wrap(fn):
        return lambda args: fn(workspace, args)

    registry.register(
        ToolSpec(
            name="kg_add",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Add a claim with its entities and relations to the KG. "
                "Params: claim, source_url, entities=[{label,type}], "
                "relations=[{src,dst,rel,weight}]."
            ),
            params=(
                ParamSpec(name="claim", description="The claim text", required=True),
                ParamSpec(name="source_url", description="Source URL", required=False),
                ParamSpec(name="entities", type="array", description="Entities", required=False),
                ParamSpec(name="relations", type="array", description="Relations", required=False),
            ),
        ),
        handler=_wrap(kg_add),
    )
    registry.register(
        ToolSpec(
            name="kg_query",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Look up an entity in the KG. Returns neighbours and the "
                "community summary it belongs to. Params: entity (label), depth=1."
            ),
            params=(
                ParamSpec(name="entity", description="Entity label", required=True),
                ParamSpec(name="depth", type="integer", description="Hops 1-3", required=False),
            ),
        ),
        handler=_wrap(kg_query),
    )
    registry.register(
        ToolSpec(
            name="kg_resolve_conflicts",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "List relations that disagree on the same (src, dst) pair. "
                "Use during assemble_report to surface a 'Conflicting claims' block."
            ),
            params=(),
        ),
        handler=_wrap(kg_resolve_conflicts),
    )


__all__ = [
    "kg_add",
    "kg_ingest_facts",
    "kg_query",
    "kg_resolve_conflicts",
    "register_kg_tools",
]
