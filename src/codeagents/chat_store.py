from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeagents import __version__ as CODEAGENTS_VERSION
from codeagents.schemas import AssistantMessage, Chat, TextContent

_GLOBAL_DIR = Path.home() / ".codeagents"
_GLOBAL_REGISTRY = _GLOBAL_DIR / "chat_registry.jsonl"


# Hard-coded chat library used by every client (TUI / GUI / SDK).
# Pinned to the project repo for development / debugging convenience so the
# user can inspect `.json` files directly. The ``CODEAGENTS_CHATS_DIR`` env
# var still overrides this for tests and one-off setups.
HARDCODED_CHATS_DIR = Path("/Users/ampiro/programs/CodeAgents/.codeagents/chats")


def default_chats_dir() -> Path:
    """Resolve the canonical place where chats live.

    1. ``CODEAGENTS_CHATS_DIR`` env var (absolute or ``~`` accepted) — escape
       hatch for tests and special setups.
    2. :data:`HARDCODED_CHATS_DIR` — fixed location pinned to the dev repo so
       TUI, GUI and the SDK all see the same library while we iterate.
    """

    raw = os.environ.get("CODEAGENTS_CHATS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return HARDCODED_CHATS_DIR.resolve()


class ChatSummary(BaseModel):
    id: str
    title: str
    path: str
    updated_at: str
    message_count: int
    workspace: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class ChatStore:
    """File-backed chat library.

    By default chats live in a single global directory (see
    :func:`default_chats_dir`) so the same history is visible from every
    workspace and every client (TUI / GUI). The ``workspace`` argument is kept
    for back-compat — callers can still pin a chat library to a specific
    directory if they want — but new code should prefer
    :meth:`ChatStore.global_default`.
    """

    def __init__(self, workspace: Path | None = None, *, root: Path | None = None) -> None:
        if root is not None:
            self.root = Path(root).expanduser().resolve()
            self.workspace = self.root.parent
        elif workspace is not None:
            self.workspace = Path(workspace).resolve()
            self.root = self.workspace / ".codeagents" / "chats"
        else:
            self.root = default_chats_dir()
            self.workspace = self.root.parent
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def global_default(cls) -> "ChatStore":
        """Return a store rooted at :func:`default_chats_dir`."""
        return cls(root=default_chats_dir())

    def create(self, *, title: str = "New chat", meta: dict[str, Any] | None = None) -> Chat:
        import uuid
        unique_title = self._unique_title(title)
        merged_meta: dict[str, Any] = {
            "title": unique_title,
            "codeagents_version": CODEAGENTS_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            **(meta or {}),
        }
        chat = Chat(
            id=uuid.uuid4().hex[:24],
            messages=[],
            meta=merged_meta,
        )
        self.save(chat)
        _register_global(
            chat_id=chat.id,
            title=unique_title,
            workspace=str(self.workspace),
            path=str(self._path(chat.id)),
            message_count=0,
            event="created",
        )
        return chat

    def _unique_title(self, title: str) -> str:
        """Append _2, _3, … if a chat with the same title already exists."""
        existing = {(s.title or "").strip() for s in self.list()}
        if title.strip() not in existing:
            return title
        i = 2
        while f"{title}_{i}" in existing:
            i += 1
        return f"{title}_{i}"

    def save(self, chat: Chat) -> Path:
        payload = chat.model_dump(mode="json", exclude_none=True)
        now = datetime.now(UTC).isoformat()
        payload["updated_at"] = now
        # Always stamp the build that last touched this chat so old clients
        # don't silently mishandle new fields and so users can see in the file
        # which version produced/updated it.
        meta_payload = payload.setdefault("meta", {})
        if isinstance(meta_payload, dict):
            meta_payload.setdefault("codeagents_version", CODEAGENTS_VERSION)
            meta_payload["last_codeagents_version"] = CODEAGENTS_VERSION
        path = self._path(chat.id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = chat.meta or {}
        _register_global(
            chat_id=chat.id,
            title=meta.get("title", ""),
            workspace=str(self.workspace),
            path=str(path),
            message_count=len(chat.messages),
            event="updated",
        )
        return path

    def load(self, chat_id: str) -> Chat:
        path = self._path(chat_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Chat.model_validate(raw)

    def list(self) -> list[ChatSummary]:
        summaries: list[ChatSummary] = []
        for path in sorted(self._iter_chat_files(), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            meta = raw.get("meta", {})
            summaries.append(
                ChatSummary(
                    id=raw["id"],
                    title=meta.get("title") or self._title_from_chat(raw),
                    path=str(path),
                    updated_at=raw.get("updated_at", ""),
                    message_count=len(raw.get("messages", [])),
                    workspace=str(self.workspace),
                    meta=meta,
                )
            )
        return summaries

    def _iter_chat_files(self) -> list[Path]:
        """Yield every chat JSON, supporting both legacy flat layout (v3.0:
        ``<root>/<id>.json``) and the per-chat folder layout (v3.1+:
        ``<root>/<id>/<title-or-chat>.json``).

        Legacy files are migrated lazily on read — see :meth:`_path`.
        """
        files: list[Path] = []
        if not self.root.exists():
            return files
        for entry in self.root.iterdir():
            if entry.is_file() and entry.suffix == ".json":
                files.append(entry)  # legacy
            elif entry.is_dir():
                # New layout: one .json sitting next to a plans/ folder.
                for cand in entry.glob("*.json"):
                    if cand.is_file():
                        files.append(cand)
        return files

    def append_messages(self, chat: Chat) -> Chat:
        self.save(chat)
        return chat

    def delete(self, chat_id: str) -> bool:
        """Remove a chat. Drops the per-chat folder (and any sibling plans).

        Falls back to deleting a legacy flat file if the new folder layout is
        not present.
        """
        import shutil

        path = self._path(chat_id)
        existed = False
        if path.exists():
            existed = True
            path.unlink()
            # Drop the now-empty (or near-empty) per-chat dir + its plans.
            chat_dir = path.parent
            if chat_dir != self.root and chat_dir.is_dir():
                shutil.rmtree(chat_dir, ignore_errors=True)
        else:
            legacy = self.root / f"{chat_id}.json"
            if legacy.exists():
                existed = True
                legacy.unlink(missing_ok=True)
        if not existed:
            return False
        _register_global(
            chat_id=chat_id,
            title="",
            workspace=str(self.workspace),
            path=str(path),
            message_count=0,
            event="deleted",
        )
        return True

    def update_meta(
        self,
        chat_id: str,
        *,
        title: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Chat:
        """Patch ``meta`` (and optionally ``meta.title``) without rewriting messages."""
        chat = self.load(chat_id)
        merged = dict(chat.meta or {})
        if meta:
            merged.update(meta)
        if title is not None:
            merged["title"] = title
        patched = Chat(
            id=chat.id,
            messages=chat.messages,
            meta=merged,
            functions=chat.functions,
        )
        self.save(patched)
        return patched

    def save_assistant_reply(self, chat: Chat, answer: str) -> Chat:
        messages = [message for message in chat.messages if not _is_placeholder_message(message)]
        saved = Chat(
            id=chat.id,
            messages=[
                *messages,
                AssistantMessage(
                    index=len(messages),
                    content=[TextContent(text=answer)],
                ),
            ],
            meta=chat.meta,
            functions=chat.functions,
        )
        self.save(saved)
        return saved

    # Filename used inside the per-chat folder. The folder itself is named by
    # chat id, so this fixed inner name keeps the layout predictable while
    # still letting the user rename the chat (title lives in meta, not in the
    # filename — title-based filenames break on every rename).
    _INNER_FILENAME = "chat.json"

    def _path(self, chat_id: str) -> Path:
        """Resolve the on-disk path for ``chat_id``.

        New layout: ``<root>/<chat_id>/chat.json`` (with a sibling ``plans/``
        folder for any plans this chat owns).

        Backward compat: if a legacy flat file ``<root>/<chat_id>.json``
        exists and the new path doesn't, migrate it transparently so old
        installs keep working without a separate migration step.
        """
        new_dir = self.root / chat_id
        new_path = new_dir / self._INNER_FILENAME
        legacy_path = self.root / f"{chat_id}.json"
        if not new_path.exists() and legacy_path.exists():
            new_dir.mkdir(parents=True, exist_ok=True)
            try:
                legacy_path.replace(new_path)
            except OSError:
                # Fall back to copy if cross-device rename fails.
                new_path.write_bytes(legacy_path.read_bytes())
                legacy_path.unlink(missing_ok=True)
        new_dir.mkdir(parents=True, exist_ok=True)
        return new_path

    @staticmethod
    def _title_from_chat(raw: dict[str, Any]) -> str:
        for message in raw.get("messages", []):
            if message.get("role") == "user":
                content = message.get("content", [])
                if content and content[0].get("type") == "text":
                    return content[0].get("text", "Chat")[:80]
        return "Chat"


def _register_global(
    *,
    chat_id: str,
    title: str,
    workspace: str,
    path: str,
    message_count: int,
    event: str,
) -> None:
    """Append a line to the global chat registry (~/.codeagents/chat_registry.jsonl)."""
    try:
        _GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "event": event,
            "chat_id": chat_id,
            "title": title,
            "workspace": workspace,
            "path": path,
            "message_count": message_count,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        with _GLOBAL_REGISTRY.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def list_global_chats() -> list[ChatSummary]:
    """Read the global registry and return the latest state of every chat."""
    if not _GLOBAL_REGISTRY.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    for line in _GLOBAL_REGISTRY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        latest[entry["chat_id"]] = entry

    summaries = []
    for entry in sorted(latest.values(), key=lambda e: e.get("timestamp", ""), reverse=True):
        summaries.append(ChatSummary(
            id=entry["chat_id"],
            title=entry.get("title") or "Chat",
            path=entry.get("path", ""),
            updated_at=entry.get("timestamp", ""),
            message_count=entry.get("message_count", 0),
            workspace=entry.get("workspace", ""),
        ))
    return summaries


def _is_placeholder_message(message: Any) -> bool:
    if getattr(message, "role", None) != "assistant":
        return False
    text = "\n".join(item.as_text() for item in getattr(message, "content", []))
    return text.strip() in {"generating", "response..."}
