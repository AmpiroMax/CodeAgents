from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeagents import __version__
from codeagents.chat_store import HARDCODED_CHATS_DIR, ChatStore, default_chats_dir


def test_default_chats_dir_uses_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "library"
    monkeypatch.setenv("CODEAGENTS_CHATS_DIR", str(target))
    assert default_chats_dir() == target.resolve()


def test_default_chats_dir_falls_back_to_hardcoded_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEAGENTS_CHATS_DIR", raising=False)
    assert default_chats_dir() == HARDCODED_CHATS_DIR.resolve()
    # Sanity: the constant is exactly the path the user pinned for dev/debug.
    assert HARDCODED_CHATS_DIR == Path(
        "/Users/ampiro/programs/CodeAgents/.codeagents/chats"
    )


def test_global_default_creates_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEAGENTS_CHATS_DIR", str(tmp_path / "lib"))
    store = ChatStore.global_default()
    assert store.root == (tmp_path / "lib").resolve()
    assert store.root.is_dir()


def test_create_stamps_codeagents_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEAGENTS_CHATS_DIR", str(tmp_path / "lib"))
    store = ChatStore.global_default()
    chat = store.create(title="t", meta={"client": "test"})
    assert chat.meta["codeagents_version"] == __version__
    assert chat.meta["client"] == "test"
    assert "created_at" in chat.meta


def test_save_stamps_last_codeagents_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEAGENTS_CHATS_DIR", str(tmp_path / "lib"))
    store = ChatStore.global_default()
    chat = store.create(title="t")
    # v3.1 layout: chats live in their own folder, with chat.json inside and a
    # plans/ sibling folder created on demand for any plans the chat owns.
    path = store.root / chat.id / "chat.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["meta"]["codeagents_version"] == __version__
    assert raw["meta"]["last_codeagents_version"] == __version__


def test_legacy_workspace_constructor_still_works(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    assert store.root == tmp_path.resolve() / ".codeagents" / "chats"
    chat = store.create(title="legacy")
    assert chat.meta["codeagents_version"] == __version__
