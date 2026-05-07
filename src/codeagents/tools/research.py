"""Deep-research tools (Phase 2.B.0–2.B.2).

These are exposed to the model only when ``mode=research`` (see
``mode_tools.MODE_TOOLS``). They orchestrate a 4-phase pipeline:

    clarify_research --> submit_clarify_answers --> plan_research
                                                         |
                                                         v
                                              expand_query --> web_search
                                                         |        |
                                                         v        v
                                                 extract_facts <--+
                                                         |
                                                         v
                                                 draft_section -> assemble_report

All persistence goes through :mod:`codeagents.stores.research`. LLM calls
go through ``OpenAICompatibleRuntime`` (constructed from ``AppConfig``
just like ``recall_chat`` does), so the tools are stateless and easy to
unit-test by patching ``_runtime_factory``.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from codeagents.core.permissions import Permission
from codeagents.stores.research import ResearchReport, ResearchSection, ResearchStore
from codeagents.tools import ParamSpec, ToolRegistry, ToolSpec
from codeagents.core.workspace import Workspace


# ── Runtime factory (overridable from tests) ─────────────────────────


def _default_runtime():
    from codeagents.core.config import load_app_config
    from codeagents.core.runtime.openai_client import OpenAICompatibleRuntime

    cfg = load_app_config()
    return OpenAICompatibleRuntime(cfg.runtime), cfg


# Tests can monkeypatch this to inject a fake runtime + config.
_runtime_factory: Callable[[], tuple[Any, Any]] = _default_runtime


def set_runtime_factory(factory: Callable[[], tuple[Any, Any]]) -> None:
    """Override the runtime factory (used by tests)."""
    global _runtime_factory
    _runtime_factory = factory


def reset_runtime_factory() -> None:
    global _runtime_factory
    _runtime_factory = _default_runtime


# ── Helpers ──────────────────────────────────────────────────────────


def _store(workspace: Workspace) -> ResearchStore:
    from codeagents.stores.chat import default_chats_dir

    return ResearchStore(default_chats_dir())


def _require_chat_id(workspace: Workspace) -> str:
    cid = (workspace.chat_id or "").strip()
    if not cid:
        raise ValueError("no active chat - cannot run research tools")
    return cid


def _require_str(args: dict[str, Any], key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"missing required string field: {key}")
    return v.strip()


def _llm_text(messages: list[dict[str, Any]]) -> str:
    """Call the runtime once and return concatenated assistant text."""
    runtime, cfg = _runtime_factory()
    profile_name = getattr(cfg.runtime, "model", None) or "qwen3:30b"
    try:
        from codeagents.core.runtime.router import ModelRouter

        model = ModelRouter(cfg).for_task("general")
    except Exception:
        from codeagents.core.config import ModelProfile

        model = ModelProfile(
            key="general",
            name=profile_name,
            role="general",
            context_tokens=8192,
            temperature=0.2,
        )
    buf: list[str] = []
    try:
        for ev in runtime.chat_stream(model=model, messages=messages):
            if ev.get("type") == "delta":
                buf.append(str(ev.get("content", "")))
    except Exception as exc:
        return f"[llm-error] {exc}"
    return "".join(buf).strip()


def _extract_json_block(text: str) -> Any | None:
    """Find and parse the first JSON block in ``text``.

    Supports both fenced ```json ... ``` and bare ``[...]`` / ``{...}``.
    Returns ``None`` if nothing parses.
    """
    if not text:
        return None
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    # Try ``{...}`` before ``[...]`` so an outer object isn't mis-extracted
    # as its first inner array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                continue
    return None


# ── Tool handlers ────────────────────────────────────────────────────


def clarify_research(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Generate 3-4 clarifying questions and create the report record.

    Always called first in research mode. The returned ``report_id`` must
    be passed to ``submit_clarify_answers``/``plan_research``.
    """
    chat_id = _require_chat_id(workspace)
    query = _require_str(args, "query")
    store = _store(workspace)
    rep = store.create(chat_id=chat_id, query=query)
    rep.status = "awaiting_clarify"

    prompt = (
        "You are a research assistant. Ask the user 3 to 4 SHORT clarifying "
        "questions to nail down scope, audience, timeframe, and depth of the "
        "research below. Do NOT ask yes/no questions. Do NOT add filler. "
        "Output STRICT JSON: {\"questions\": [\"...\", \"...\", \"...\"]}.\n\n"
        f"User query: {query}"
    )
    raw = _llm_text(
        [
            {"role": "system", "content": "Return strict JSON only."},
            {"role": "user", "content": prompt},
        ]
    )
    data = _extract_json_block(raw) or {}
    questions: list[str] = []
    if isinstance(data, dict):
        for q in data.get("questions") or []:
            if isinstance(q, str) and q.strip():
                questions.append(q.strip())
    if not questions:
        # Fallback: deterministic generic prompts so the flow doesn't stall.
        questions = [
            f"What's the audience and depth you need for: '{query}'?",
            "What timeframe / publication date range do you care about?",
            "Are there specific tools, frameworks, or competitors to compare?",
        ]
    rep.clarify_questions = questions[:4]
    store.save(rep)
    return {"report_id": rep.id, "questions": rep.clarify_questions, "status": rep.status}


def submit_clarify_answers(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Record answers (or ``skipped=true``) and unblock ``plan_research``."""
    chat_id = _require_chat_id(workspace)
    report_id = _require_str(args, "report_id")
    store = _store(workspace)
    rep = store.load(chat_id, report_id)

    if bool(args.get("skipped", False)):
        rep.clarify_skipped = True
        rep.clarify_answers = []
    else:
        raw_answers = args.get("answers") or []
        if not isinstance(raw_answers, list):
            raise ValueError("answers must be a list of {question, answer} objects")
        normalised: list[dict[str, str]] = []
        for item in raw_answers:
            if isinstance(item, dict):
                q = str(item.get("question", "")).strip()
                a = str(item.get("answer", "")).strip()
                if q and a:
                    normalised.append({"question": q, "answer": a})
        rep.clarify_answers = normalised
        rep.clarify_skipped = False
    rep.status = "ready_to_plan"
    store.save(rep)
    return {"report_id": rep.id, "status": rep.status, "answers": rep.clarify_answers}


def plan_research(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Build the report outline.

    The chat-first workflow: the agent asks the user for clarifications
    in plain chat (no popup tools). When the user replies, the agent
    calls ``plan_research`` with either:
      - ``query``: the synthesised query (original + clarifications);
        a fresh report record is created automatically.
      - ``report_id``: an existing report (legacy path).
    """
    chat_id = _require_chat_id(workspace)
    store = _store(workspace)

    report_id = str(args.get("report_id") or "").strip()
    query_arg = str(args.get("query") or "").strip()
    if report_id:
        rep = store.load(chat_id, report_id)
    elif query_arg:
        rep = store.create(chat_id=chat_id, query=query_arg)
        rep.status = "ready_to_plan"
        store.save(rep)
    else:
        return {
            "error": "missing argument",
            "hint": "pass either query=<text> (new report) or report_id=<id>",
        }

    rep.status = "planning"
    rep.touch()
    store.save(rep)

    context_lines: list[str] = [f"User query: {rep.query}"]
    if rep.clarify_answers:
        context_lines.append("Clarifying answers:")
        for pair in rep.clarify_answers:
            context_lines.append(f"  - Q: {pair['question']}\n    A: {pair['answer']}")

    prompt = (
        "You are an expert research planner. Design an outline of 3-6 sections "
        "for a deep-research report. Each section needs 1-3 concrete questions "
        "to investigate via web search. Output STRICT JSON: "
        "{\"sections\": [{\"title\": \"...\", \"questions\": [\"...\", ...]}, ...]}.\n\n"
        + "\n".join(context_lines)
    )
    raw = _llm_text(
        [
            {"role": "system", "content": "Return strict JSON only."},
            {"role": "user", "content": prompt},
        ]
    )
    data = _extract_json_block(raw) or {}
    sections_raw = (data.get("sections") if isinstance(data, dict) else None) or []
    outline: list[ResearchSection] = []
    for s in sections_raw:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).strip()
        if not title:
            continue
        questions = [str(q).strip() for q in (s.get("questions") or []) if str(q).strip()]
        outline.append(ResearchSection(title=title, questions=questions[:3]))

    if not outline:
        # Fallback: single section so the loop can still run.
        outline = [ResearchSection(title="Findings", questions=[rep.query])]
    rep.outline = outline[:6]
    rep.status = "researching"
    store.save(rep)
    return {
        "report_id": rep.id,
        "status": rep.status,
        "outline": [{"title": s.title, "questions": s.questions} for s in rep.outline],
    }


def expand_query(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Generate ``n`` diverse search queries for a subgoal."""
    subgoal = _require_str(args, "subgoal")
    n = max(1, min(int(args.get("n", 3)), 6))
    prompt = (
        f"Rewrite the research subgoal below into {n} diverse, concrete web "
        "search queries. Use varied phrasings/keywords. Output STRICT JSON: "
        "{\"queries\": [\"...\", ...]}.\n\n"
        f"Subgoal: {subgoal}"
    )
    raw = _llm_text(
        [
            {"role": "system", "content": "Return strict JSON only."},
            {"role": "user", "content": prompt},
        ]
    )
    data = _extract_json_block(raw) or {}
    queries = []
    if isinstance(data, dict):
        for q in data.get("queries") or []:
            if isinstance(q, str) and q.strip():
                queries.append(q.strip())
    if not queries:
        queries = [subgoal]
    return {"queries": queries[:n]}


def extract_facts(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Pull discrete claims out of fetched text and record them.

    Each fact is appended to ``notes.jsonl`` and (when ``report_id`` is
    provided) attached to the named section for ``draft_section``.
    """
    text = _require_str(args, "text")
    source_url = str(args.get("source_url", "")).strip()
    report_id = str(args.get("report_id", "")).strip()
    section_idx = int(args.get("section_idx", -1))

    prompt = (
        "Extract 3-7 atomic factual claims from the passage below. Each claim "
        "must be self-contained, specific, and supported by the text. Output "
        "STRICT JSON: {\"facts\": [{\"claim\": \"...\", \"span\": \"...\"}, ...]}. "
        "``span`` is the shortest verbatim text snippet from the source that "
        "supports the claim.\n\n"
        f"Passage:\n{text[:8000]}"
    )
    raw = _llm_text(
        [
            {"role": "system", "content": "Return strict JSON only."},
            {"role": "user", "content": prompt},
        ]
    )
    data = _extract_json_block(raw) or {}
    facts_raw = data.get("facts") if isinstance(data, dict) else None
    facts: list[dict[str, Any]] = []
    for item in facts_raw or []:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim", "")).strip()
        if not claim:
            continue
        facts.append(
            {
                "claim": claim,
                "span": str(item.get("span", "")).strip(),
                "source_url": source_url,
                "ts": time.time(),
            }
        )

    chat_id = (workspace.chat_id or "").strip()
    if chat_id and report_id and facts:
        store = _store(workspace)
        try:
            rep = store.load(chat_id, report_id)
            for f in facts:
                store.append_note(chat_id, report_id, f)
            if 0 <= section_idx < len(rep.outline):
                section = rep.outline[section_idx]
                section.facts.extend(facts)
                store.save_section(chat_id, report_id, index=section_idx, section=section)
            # Best-effort KG ingestion (no-op until Phase 2.C is wired).
            try:
                from codeagents.tools.kg import kg_ingest_facts

                kg_ingest_facts(workspace, report_id=report_id, facts=facts)
            except Exception:
                pass
            # Source bookkeeping.
            if source_url and not any(s.get("url") == source_url for s in rep.sources):
                rep.sources.append({"url": source_url, "ts": time.time()})
                store.save(rep)
        except FileNotFoundError:
            pass

    return {"facts": facts, "count": len(facts)}


def draft_section(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Render section markdown using collected facts and citation indices."""
    chat_id = _require_chat_id(workspace)
    report_id = _require_str(args, "report_id")
    section_idx = int(args.get("section_idx", -1))
    store = _store(workspace)
    rep = store.load(chat_id, report_id)
    if not (0 <= section_idx < len(rep.outline)):
        return {"error": f"section_idx out of range (0..{len(rep.outline) - 1})"}
    section = rep.outline[section_idx]

    # Build a stable [n] citation map across the report (URL -> index).
    url_to_idx: dict[str, int] = {}
    for i, src in enumerate(rep.sources, start=1):
        url = str(src.get("url", ""))
        if url:
            url_to_idx[url] = i

    facts_lines = []
    for f in section.facts:
        url = str(f.get("source_url", ""))
        idx = url_to_idx.get(url)
        cite = f"[{idx}]" if idx else "[?]"
        facts_lines.append(f"- {f.get('claim', '').strip()} {cite}")
    facts_block = "\n".join(facts_lines) if facts_lines else "(no facts gathered)"

    prompt = (
        "Write a SECTION of a research report in markdown. Section title and "
        "guiding questions are given. Each factual statement MUST be followed "
        "by its citation tag exactly as listed (e.g. [1]). Do NOT invent new "
        "facts. Do NOT include a heading - just paragraphs. Output markdown only.\n\n"
        f"Section title: {section.title}\n"
        f"Guiding questions:\n- " + "\n- ".join(section.questions or ["(none)"]) + "\n\n"
        f"Facts (use exactly these and their citations):\n{facts_block}"
    )
    body = _llm_text(
        [
            {"role": "system", "content": "Return markdown only. No preamble."},
            {"role": "user", "content": prompt},
        ]
    )
    if not body or body.startswith("[llm-error]"):
        # Lossy fallback: assemble bullets directly.
        body = facts_block

    section.draft = body
    section.status = "drafted"
    store.save_section(chat_id, report_id, index=section_idx, section=section)
    return {"section_idx": section_idx, "title": section.title, "draft": body}


def assemble_report(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Concatenate drafted sections + sources table into a final report.md."""
    chat_id = _require_chat_id(workspace)
    report_id = _require_str(args, "report_id")
    store = _store(workspace)
    rep = store.load(chat_id, report_id)

    parts: list[str] = []
    parts.append(f"# Research report: {rep.query}\n")
    if rep.clarify_answers:
        parts.append("## Scope\n")
        for pair in rep.clarify_answers:
            parts.append(f"- **{pair['question']}** {pair['answer']}")
        parts.append("")
    for i, section in enumerate(rep.outline, start=1):
        parts.append(f"## {i}. {section.title}\n")
        if section.draft.strip():
            parts.append(section.draft.strip())
        else:
            parts.append("_(no draft)_")
        parts.append("")

    # Phase 2.C.4: surface KG conflicts (best-effort).
    try:
        from codeagents.tools.kg import kg_resolve_conflicts

        kg_res = kg_resolve_conflicts(workspace, {})
        conflicts = kg_res.get("conflicts") if isinstance(kg_res, dict) else []
        if conflicts:
            parts.append("## Conflicting claims\n")
            for c in conflicts:
                parts.append(
                    f"- `{c.get('src_id')}` -> `{c.get('dst_id')}`: "
                    f"`{c.get('rel_a')}` vs `{c.get('rel_b')}` "
                    f"(sources: {c.get('sources_a')}, {c.get('sources_b')})"
                )
            parts.append("")
    except Exception:
        pass

    if rep.sources:
        parts.append("## Sources\n")
        for i, src in enumerate(rep.sources, start=1):
            url = str(src.get("url", ""))
            parts.append(f"[{i}] {url}")

    markdown = "\n".join(parts).rstrip() + "\n"
    store.write_markdown(chat_id, report_id, markdown)
    rep.status = "done"
    store.save(rep)
    return {
        "report_id": rep.id,
        "status": rep.status,
        "markdown": markdown,
        "sections": len(rep.outline),
        "sources": len(rep.sources),
    }


# ── Registration ─────────────────────────────────────────────────────


def register_research_tools(registry: ToolRegistry, workspace: Workspace) -> None:
    """Register all 7 research tools (visible only via mode=research)."""

    def _wrap(fn):
        return lambda args: fn(workspace, args)

    registry.register(
        ToolSpec(
            name="clarify_research",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Start a deep-research report. Generates 3-4 clarifying "
                "questions for the user. Always call this FIRST in research mode. "
                "Params: query (required)."
            ),
            params=(ParamSpec(name="query", description="Research question", required=True),),
        ),
        handler=_wrap(clarify_research),
    )
    registry.register(
        ToolSpec(
            name="submit_clarify_answers",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Record user answers to clarifying questions, OR mark them as "
                "skipped. Params: report_id, answers=[{question,answer}], skipped=false."
            ),
            params=(
                ParamSpec(name="report_id", description="Report id from clarify_research", required=True),
                ParamSpec(name="answers", type="array", description="[{question,answer}]", required=False),
                ParamSpec(name="skipped", type="boolean", description="True if user skipped", required=False),
            ),
        ),
        handler=_wrap(submit_clarify_answers),
    )
    registry.register(
        ToolSpec(
            name="plan_research",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Build a 3-6 section outline for a deep-research report. "
                "Pass either query=<full text> (creates a new report and "
                "returns its report_id) or report_id=<existing id>. "
                "ALWAYS ask the user 3-4 clarifying questions in chat first "
                "and only call this tool after they reply."
            ),
            params=(
                ParamSpec(
                    name="query",
                    description=(
                        "Full research query, ideally including a 1-2 line "
                        "synthesis of the user's clarifying answers."
                    ),
                    required=False,
                ),
                ParamSpec(name="report_id", description="Existing report id (optional)", required=False),
            ),
        ),
        handler=_wrap(plan_research),
    )
    registry.register(
        ToolSpec(
            name="expand_query",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Rewrite a research subgoal into N diverse web search queries. "
                "Params: subgoal (required), n=3."
            ),
            params=(
                ParamSpec(name="subgoal", description="Subgoal text", required=True),
                ParamSpec(name="n", type="integer", description="Number of queries (1-6)", required=False),
            ),
        ),
        handler=_wrap(expand_query),
    )
    registry.register(
        ToolSpec(
            name="extract_facts",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Extract atomic claims from a passage and append them to the "
                "report. Params: text (required), source_url, report_id, section_idx."
            ),
            params=(
                ParamSpec(name="text", description="Source passage", required=True),
                ParamSpec(name="source_url", description="URL it came from", required=False),
                ParamSpec(name="report_id", description="Report id", required=False),
                ParamSpec(name="section_idx", type="integer", description="Section index", required=False),
            ),
        ),
        handler=_wrap(extract_facts),
    )
    registry.register(
        ToolSpec(
            name="draft_section",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Write the markdown body of a section using its collected facts "
                "with [n] citations. Params: report_id, section_idx."
            ),
            params=(
                ParamSpec(name="report_id", description="Report id", required=True),
                ParamSpec(name="section_idx", type="integer", description="Index in outline", required=True),
            ),
        ),
        handler=_wrap(draft_section),
    )
    registry.register(
        ToolSpec(
            name="assemble_report",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Concatenate drafted sections into report.md and finalise the "
                "report. Params: report_id."
            ),
            params=(ParamSpec(name="report_id", description="Report id", required=True),),
        ),
        handler=_wrap(assemble_report),
    )


__all__ = [
    "assemble_report",
    "clarify_research",
    "draft_section",
    "expand_query",
    "extract_facts",
    "plan_research",
    "register_research_tools",
    "reset_runtime_factory",
    "set_runtime_factory",
    "submit_clarify_answers",
]
