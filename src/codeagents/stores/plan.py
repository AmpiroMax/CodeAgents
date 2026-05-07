"""File-backed store for agent-authored plans, scoped per-chat.

Layout (since v3.1):

    <chats_dir>/
        <chat_id>/
            <chat-file>.json          # owned by ChatStore
            plans/
                <plan_id>.json        # the authoritative plan document
                <plan_id>.md          # best-effort human-readable rendering
        _orphans/
            plans/                    # plans created without a chat context

``<chats_dir>`` defaults to :func:`codeagents.stores.chat.default_chats_dir`,
which itself honours ``CODEAGENTS_CHATS_DIR``. Tests can also override via
``CODEAGENTS_PLANS_DIR`` — when set, plans collapse back to a single flat
directory at that path (legacy mode), which keeps the unit tests trivial.

Lifecycle:

    draft      → just created, not yet built
    building   → user pressed Build / Continue, agent is executing
    completed  → all steps done
    rejected   → user dismissed the plan via the X button
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from codeagents import __version__ as CODEAGENTS_VERSION

PlanStatus = Literal["draft", "building", "completed", "rejected"]
StepStatus = Literal["pending", "in_progress", "done", "skipped"]

ACTIVE_STATUSES: frozenset[str] = frozenset({"draft", "building"})
MAX_ACTIVE_PLANS = 3

ORPHAN_BUCKET = "_orphans"


def default_plans_dir() -> Path | None:
    """Legacy flat-dir override.

    Returns the path pointed to by ``CODEAGENTS_PLANS_DIR`` when set, else
    ``None``. ``None`` means "use the modern per-chat layout under the chats
    directory". Kept around so tests can still pin a tmp directory and so
    downstream tooling that wrote to the v3.0 location keeps working.
    """

    raw = os.environ.get("CODEAGENTS_PLANS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return None


@dataclass
class PlanStep:
    n: int
    title: str
    detail: str = ""
    status: StepStatus = "pending"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "title": self.title,
            "detail": self.detail,
            "status": self.status,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PlanStep":
        return cls(
            n=int(raw.get("n", 0)),
            title=str(raw.get("title", "")),
            detail=str(raw.get("detail", "")),
            status=str(raw.get("status", "pending")),  # type: ignore[arg-type]
            note=str(raw.get("note", "")),
        )


@dataclass
class Plan:
    id: str
    title: str
    summary: str
    steps: list[PlanStep]
    status: PlanStatus = "draft"
    workspace: str = ""
    chat_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    codeagents_version: str = CODEAGENTS_VERSION

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def done_steps(self) -> int:
        return sum(1 for s in self.steps if s.status in {"done", "skipped"})

    @property
    def is_complete(self) -> bool:
        return self.total_steps > 0 and self.done_steps >= self.total_steps

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "workspace": self.workspace,
            "chat_id": self.chat_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "codeagents_version": self.codeagents_version,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Plan":
        return cls(
            id=str(raw["id"]),
            title=str(raw.get("title", "")),
            summary=str(raw.get("summary", "")),
            steps=[PlanStep.from_dict(s) for s in raw.get("steps", [])],
            status=str(raw.get("status", "draft")),  # type: ignore[arg-type]
            workspace=str(raw.get("workspace", "")),
            chat_id=str(raw.get("chat_id", "")),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
            codeagents_version=str(raw.get("codeagents_version", CODEAGENTS_VERSION)),
        )

    def to_markdown(self) -> str:
        lines: list[str] = [f"# {self.title or 'Untitled plan'}", ""]
        if self.summary:
            lines.extend([self.summary.strip(), ""])
        lines.append(f"_status: {self.status} · {self.done_steps}/{self.total_steps} done_")
        lines.append("")
        lines.append("## Steps")
        lines.append("")
        for step in self.steps:
            mark = {
                "done": "[x]",
                "skipped": "[~]",
                "in_progress": "[>]",
                "pending": "[ ]",
            }.get(step.status, "[ ]")
            lines.append(f"{step.n}. {mark} **{step.title}**")
            if step.detail:
                for piece in step.detail.strip().splitlines():
                    lines.append(f"    {piece}")
            if step.note:
                lines.append(f"    _note: {step.note}_")
        lines.append("")
        return "\n".join(lines)


@dataclass
class PlanSummary:
    id: str
    title: str
    status: PlanStatus
    total_steps: int
    done_steps: int
    workspace: str
    chat_id: str
    updated_at: str

    @classmethod
    def from_plan(cls, plan: Plan) -> "PlanSummary":
        return cls(
            id=plan.id,
            title=plan.title,
            status=plan.status,
            total_steps=plan.total_steps,
            done_steps=plan.done_steps,
            workspace=plan.workspace,
            chat_id=plan.chat_id,
            updated_at=plan.updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "total_steps": self.total_steps,
            "done_steps": self.done_steps,
            "workspace": self.workspace,
            "chat_id": self.chat_id,
            "updated_at": self.updated_at,
        }


class PlanLimitError(RuntimeError):
    """Raised when the user already has the maximum number of active plans."""


class PlanNotFoundError(KeyError):
    pass


class PlanStore:
    """Per-chat plan storage.

    Two layout modes:

    * **Legacy flat** — when constructed with an explicit flat ``root`` (or
      via ``CODEAGENTS_PLANS_DIR``), all plans live directly under that
      single directory. Used by tests; preserves v3.0 on-disk format.
    * **Per-chat** — when constructed with ``chats_dir``, each plan lives at
      ``chats_dir/<chat_id>/plans/<plan_id>.json``. Plans without a chat_id
      land in ``chats_dir/_orphans/plans/``.
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        chats_dir: Path | None = None,
    ) -> None:
        if root is not None and chats_dir is not None:
            raise ValueError("PlanStore: pass either root (flat) or chats_dir (per-chat).")
        if root is not None:
            self.flat_root: Path | None = Path(root).expanduser().resolve()
            self.chats_dir: Path | None = None
            self.flat_root.mkdir(parents=True, exist_ok=True)
        else:
            self.flat_root = None
            if chats_dir is None:
                # Resolve lazily to avoid a circular import at module load.
                from codeagents.stores.chat import default_chats_dir

                chats_dir = default_chats_dir()
            self.chats_dir = Path(chats_dir).expanduser().resolve()
            self.chats_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def global_default(cls) -> "PlanStore":
        legacy = default_plans_dir()
        if legacy is not None:
            return cls(root=legacy)
        return cls()

    # ----- CRUD --------------------------------------------------------

    def create(
        self,
        *,
        title: str,
        summary: str,
        steps: Iterable[PlanStep | dict[str, Any]],
        workspace: str = "",
        chat_id: str = "",
    ) -> Plan:
        # Active-plan cap is *per chat*, not global: every chat can carry up
        # to MAX_ACTIVE_PLANS draft/building plans of its own. Plans without a
        # chat (orphan bucket) share their own slot pool.
        active_for_chat = [
            p
            for p in self.list()
            if p.status in ACTIVE_STATUSES and (p.chat_id or "") == (chat_id or "")
        ]
        if len(active_for_chat) >= MAX_ACTIVE_PLANS:
            raise PlanLimitError(
                f"Already have {MAX_ACTIVE_PLANS} active plans in this chat; "
                "reject one before creating more."
            )
        normalized: list[PlanStep] = []
        for idx, step in enumerate(steps, start=1):
            if isinstance(step, PlanStep):
                normalized.append(PlanStep(n=idx, title=step.title, detail=step.detail))
            else:
                normalized.append(
                    PlanStep(
                        n=idx,
                        title=str(step.get("title") or step.get("name") or f"Step {idx}"),
                        detail=str(step.get("detail") or step.get("description") or ""),
                    )
                )
        if not normalized:
            raise ValueError("Plan must contain at least one step.")
        now = datetime.now(UTC).isoformat()
        plan = Plan(
            id=uuid.uuid4().hex[:24],
            title=title.strip() or "Untitled plan",
            summary=summary.strip(),
            steps=normalized,
            status="draft",
            workspace=workspace,
            chat_id=chat_id,
            created_at=now,
            updated_at=now,
        )
        self._write(plan)
        return plan

    def load(self, plan_id: str) -> Plan:
        path = self._find_path(plan_id)
        if path is None:
            raise PlanNotFoundError(plan_id)
        return Plan.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[Plan]:
        out: list[Plan] = []
        for path in sorted(
            self._iter_plan_files(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                out.append(Plan.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, KeyError):
                continue
        return out

    def summaries(self) -> list[PlanSummary]:
        return [PlanSummary.from_plan(p) for p in self.list()]

    def active(self) -> list[Plan]:
        return [p for p in self.list() if p.status in ACTIVE_STATUSES]

    def delete(self, plan_id: str) -> bool:
        path = self._find_path(plan_id)
        if path is None:
            return False
        md = path.with_suffix(".md")
        path.unlink(missing_ok=True)
        md.unlink(missing_ok=True)
        return True

    # ----- Mutations ---------------------------------------------------

    def patch(
        self,
        plan_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        status: PlanStatus | None = None,
    ) -> Plan:
        plan = self.load(plan_id)
        if title is not None:
            plan.title = title.strip() or plan.title
        if summary is not None:
            plan.summary = summary.strip()
        if steps is not None:
            new_steps: list[PlanStep] = []
            for idx, step in enumerate(steps, start=1):
                base = PlanStep.from_dict({**step, "n": idx})
                existing = next(
                    (s for s in plan.steps if s.title == base.title),
                    None,
                )
                if existing:
                    base.status = existing.status
                    base.note = base.note or existing.note
                new_steps.append(base)
            if not new_steps:
                raise ValueError("Plan must keep at least one step.")
            plan.steps = new_steps
        if status is not None:
            plan.status = status
        plan.updated_at = datetime.now(UTC).isoformat()
        self._write(plan)
        return plan

    def mark_step(
        self,
        plan_id: str,
        step_n: int,
        status: StepStatus,
        *,
        note: str = "",
    ) -> Plan:
        plan = self.load(plan_id)
        target = next((s for s in plan.steps if s.n == step_n), None)
        if target is None:
            raise ValueError(f"step {step_n} not found in plan {plan_id}")
        target.status = status
        if note:
            target.note = note
        if plan.status == "draft" and any(s.status != "pending" for s in plan.steps):
            plan.status = "building"
        if plan.is_complete:
            plan.status = "completed"
        plan.updated_at = datetime.now(UTC).isoformat()
        self._write(plan)
        return plan

    def reject(self, plan_id: str) -> Plan:
        plan = self.load(plan_id)
        plan.status = "rejected"
        plan.updated_at = datetime.now(UTC).isoformat()
        self._write(plan)
        return plan

    # ----- Internals ---------------------------------------------------

    def _bucket_for(self, chat_id: str) -> Path:
        """Directory where plans for ``chat_id`` live (per-chat layout)."""
        if self.flat_root is not None:
            return self.flat_root
        assert self.chats_dir is not None
        bucket_id = chat_id.strip() or ORPHAN_BUCKET
        bucket = self.chats_dir / bucket_id / "plans"
        bucket.mkdir(parents=True, exist_ok=True)
        return bucket

    def _path(self, plan: Plan) -> Path:
        bucket = self._bucket_for(plan.chat_id)
        return bucket / f"{plan.id}.json"

    def _find_path(self, plan_id: str) -> Path | None:
        """Locate an existing plan file across chat buckets."""
        if self.flat_root is not None:
            candidate = self.flat_root / f"{plan_id}.json"
            return candidate if candidate.exists() else None
        for path in self._iter_plan_files():
            if path.stem == plan_id:
                return path
        return None

    def _iter_plan_files(self) -> Iterable[Path]:
        if self.flat_root is not None:
            yield from self.flat_root.glob("*.json")
            return
        assert self.chats_dir is not None
        if not self.chats_dir.exists():
            return
        for chat_dir in self.chats_dir.iterdir():
            if not chat_dir.is_dir():
                continue
            plans_dir = chat_dir / "plans"
            if not plans_dir.is_dir():
                continue
            yield from plans_dir.glob("*.json")

    def _write(self, plan: Plan) -> None:
        plan.codeagents_version = CODEAGENTS_VERSION
        path = self._path(plan)
        path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            path.with_suffix(".md").write_text(plan.to_markdown(), encoding="utf-8")
        except OSError:
            pass
