"""Per-chat deep-research report storage (Phase 2.B.1).

Layout
------
.codeagents/chats/<chat_id>/research/<report_id>/
    report.json     -- structured meta (outline, sections, sources, status,
                       clarify questions/answers, timestamps)
    report.md       -- final assembled markdown (written by assemble_report)
    notes.jsonl     -- extracted facts {claim, source_url, ts, ...} appended
                       by extract_facts; used by KG ingestion in 2.C

Status state machine
--------------------
created -> awaiting_clarify -> ready_to_plan -> planning -> researching ->
drafting -> assembled -> done
                     ^                                          |
                     +--------- cancelled / failed -------------+

The store deliberately stays I/O-only: validation of state transitions
lives in the research tools (``tools_native/research.py``) so we can keep
the persistence layer trivial and testable.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


REPORT_VERSION = 1


@dataclass
class ResearchSection:
    title: str
    questions: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    draft: str = ""
    status: str = "pending"  # pending | researching | drafted | done

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "questions": list(self.questions),
            "facts": list(self.facts),
            "draft": self.draft,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchSection":
        return cls(
            title=str(data.get("title", "")),
            questions=list(data.get("questions") or []),
            facts=list(data.get("facts") or []),
            draft=str(data.get("draft", "")),
            status=str(data.get("status", "pending")),
        )


@dataclass
class ResearchReport:
    id: str
    chat_id: str
    query: str
    status: str = "created"
    outline: list[ResearchSection] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    clarify_questions: list[str] = field(default_factory=list)
    clarify_answers: list[dict[str, str]] = field(default_factory=list)
    clarify_skipped: bool = False
    created_ts: float = 0.0
    updated_ts: float = 0.0

    def touch(self) -> None:
        self.updated_ts = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": REPORT_VERSION,
            "id": self.id,
            "chat_id": self.chat_id,
            "query": self.query,
            "status": self.status,
            "outline": [s.to_dict() for s in self.outline],
            "sources": list(self.sources),
            "clarify": {
                "questions": list(self.clarify_questions),
                "answers": list(self.clarify_answers),
                "skipped": self.clarify_skipped,
            },
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchReport":
        clarify = data.get("clarify") or {}
        return cls(
            id=str(data["id"]),
            chat_id=str(data.get("chat_id", "")),
            query=str(data.get("query", "")),
            status=str(data.get("status", "created")),
            outline=[ResearchSection.from_dict(s) for s in (data.get("outline") or [])],
            sources=list(data.get("sources") or []),
            clarify_questions=list(clarify.get("questions") or []),
            clarify_answers=list(clarify.get("answers") or []),
            clarify_skipped=bool(clarify.get("skipped", False)),
            created_ts=float(data.get("created_ts", 0.0)),
            updated_ts=float(data.get("updated_ts", 0.0)),
        )


class ResearchStore:
    """File-backed CRUD for ``ResearchReport``.

    One ``ResearchStore`` instance per workspace. ``chats_root`` is the
    directory containing ``<chat_id>/`` subfolders (typically the global
    ``default_chats_dir()``).
    """

    def __init__(self, chats_root: Path) -> None:
        self.chats_root = Path(chats_root)

    # ── path helpers ──────────────────────────────────────────────────

    def _chat_research_dir(self, chat_id: str) -> Path:
        if not chat_id:
            raise ValueError("chat_id required")
        return self.chats_root / chat_id / "research"

    def _report_dir(self, chat_id: str, report_id: str) -> Path:
        return self._chat_research_dir(chat_id) / report_id

    def report_md_path(self, chat_id: str, report_id: str) -> Path:
        return self._report_dir(chat_id, report_id) / "report.md"

    def notes_path(self, chat_id: str, report_id: str) -> Path:
        return self._report_dir(chat_id, report_id) / "notes.jsonl"

    # ── CRUD ──────────────────────────────────────────────────────────

    def create(self, *, chat_id: str, query: str) -> ResearchReport:
        rid = uuid.uuid4().hex[:12]
        now = time.time()
        report = ResearchReport(
            id=rid,
            chat_id=chat_id,
            query=query,
            status="created",
            created_ts=now,
            updated_ts=now,
        )
        self._save(report)
        return report

    def save(self, report: ResearchReport) -> None:
        self._save(report)

    def load(self, chat_id: str, report_id: str) -> ResearchReport:
        path = self._report_dir(chat_id, report_id) / "report.json"
        if not path.exists():
            raise FileNotFoundError(f"research report not found: {chat_id}/{report_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ResearchReport.from_dict(data)

    def list(self, chat_id: str) -> list[ResearchReport]:
        root = self._chat_research_dir(chat_id)
        if not root.is_dir():
            return []
        out: list[ResearchReport] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            f = child / "report.json"
            if not f.exists():
                continue
            try:
                out.append(ResearchReport.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except Exception:
                continue
        out.sort(key=lambda r: r.created_ts, reverse=True)
        return out

    def set_status(self, chat_id: str, report_id: str, status: str) -> ResearchReport:
        rep = self.load(chat_id, report_id)
        rep.status = status
        rep.touch()
        self._save(rep)
        return rep

    def save_section(
        self, chat_id: str, report_id: str, *, index: int, section: ResearchSection
    ) -> ResearchReport:
        rep = self.load(chat_id, report_id)
        if index < 0 or index >= len(rep.outline):
            raise IndexError(f"section index {index} out of range")
        rep.outline[index] = section
        rep.touch()
        self._save(rep)
        return rep

    def append_note(
        self,
        chat_id: str,
        report_id: str,
        note: dict[str, Any],
    ) -> None:
        path = self.notes_path(chat_id, report_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(note, ensure_ascii=False) + "\n")

    def iter_notes(self, chat_id: str, report_id: str) -> Iterator[dict[str, Any]]:
        path = self.notes_path(chat_id, report_id)
        if not path.exists():
            return iter(())
        return _iter_jsonl(path)

    def write_markdown(self, chat_id: str, report_id: str, markdown: str) -> Path:
        path = self.report_md_path(chat_id, report_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        return path

    def read_markdown(self, chat_id: str, report_id: str) -> str:
        path = self.report_md_path(chat_id, report_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # ── internals ─────────────────────────────────────────────────────

    def _save(self, report: ResearchReport) -> None:
        d = self._report_dir(report.chat_id, report.id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "report.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


__all__ = [
    "ResearchReport",
    "ResearchSection",
    "ResearchStore",
]
