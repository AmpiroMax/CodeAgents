from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeagents.schemas import AssistantMessage, Chat, TextContent

_GLOBAL_DIR = Path.home() / ".codeagents"
_GLOBAL_REGISTRY = _GLOBAL_DIR / "chat_registry.jsonl"


class ChatSummary(BaseModel):
    id: str
    title: str
    path: str
    updated_at: str
    message_count: int
    workspace: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class ChatStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / ".codeagents" / "chats"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, *, title: str = "New chat", meta: dict[str, Any] | None = None) -> Chat:
        import uuid
        unique_title = self._unique_title(title)
        chat = Chat(
            id=uuid.uuid4().hex[:24],
            messages=[],
            meta={"title": unique_title, **(meta or {})},
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
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            raw = json.loads(path.read_text(encoding="utf-8"))
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

    def append_messages(self, chat: Chat) -> Chat:
        self.save(chat)
        return chat

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

    def _path(self, chat_id: str) -> Path:
        return self.root / f"{chat_id}.json"

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
