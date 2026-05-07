from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codeagents.auto_recall import (
    DEFAULT_THRESHOLD,
    MIN_HISTORY,
    AutoRecallResult,
    maybe_recall,
)
from codeagents.chat_rag import ChatEmbeddingStore


class _FakeEmbedder:
    """Deterministic embedder for tests.

    Maps each text to a 4-d vector based on keyword presence; cosine over
    these gives strong signal for matching keywords and ~0 otherwise.
    """

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts, model: str | None = None):
        self.calls += 1
        out: list[list[float]] = []
        for t in texts:
            tl = t.lower()
            out.append(
                [
                    1.0 if "fastapi" in tl else 0.0,
                    1.0 if "django" in tl else 0.0,
                    1.0 if "flask" in tl else 0.0,
                    1.0 if "ruby" in tl else 0.0,
                ]
            )
        return out


def _seed_chat_store(chat_dir: Path, messages: list[tuple[int, str, str]]) -> None:
    embedder = _FakeEmbedder()
    store = ChatEmbeddingStore(chat_dir)
    for idx, role, text in messages:
        store.index_message(
            message_index=idx,
            role=role,
            text=text,
            embedding_client=embedder,
            embedding_model="fake",
        )


def test_returns_empty_when_chat_too_short(tmp_path: Path) -> None:
    res = maybe_recall(
        chat_dir=tmp_path,
        history_len=2,
        last_user_text="hello",
        embedding_client=_FakeEmbedder(),
        embedding_model="fake",
    )
    assert res.system_text == ""
    assert res.hit_count == 0


def test_returns_empty_when_query_blank(tmp_path: Path) -> None:
    res = maybe_recall(
        chat_dir=tmp_path,
        history_len=20,
        last_user_text="   ",
        embedding_client=_FakeEmbedder(),
        embedding_model="fake",
    )
    assert res.system_text == ""


def test_recalls_relevant_message(tmp_path: Path) -> None:
    _seed_chat_store(
        tmp_path,
        [
            (1, "user", "I want to migrate our API from Django to FastAPI"),
            (2, "assistant", "Here are some tradeoffs of FastAPI vs Django..."),
            (3, "user", "I also like Ruby on Rails"),
        ],
    )
    res = maybe_recall(
        chat_dir=tmp_path,
        history_len=MIN_HISTORY + 5,
        last_user_text="continue with the FastAPI migration",
        embedding_client=_FakeEmbedder(),
        embedding_model="fake",
    )
    assert res.hit_count >= 1
    assert "FastAPI" in res.system_text or "fastapi" in res.system_text.lower()
    assert res.top_score >= DEFAULT_THRESHOLD


def test_drops_below_threshold(tmp_path: Path) -> None:
    _seed_chat_store(
        tmp_path,
        [
            (1, "user", "Ruby on Rails routing"),
            (2, "assistant", "Rails uses convention over configuration"),
        ],
    )
    res = maybe_recall(
        chat_dir=tmp_path,
        history_len=MIN_HISTORY + 1,
        last_user_text="how to deploy Spring Boot to Kubernetes",
        embedding_client=_FakeEmbedder(),
        embedding_model="fake",
        threshold=0.99,
    )
    assert res.system_text == ""


def test_swallows_embedder_errors(tmp_path: Path) -> None:
    class _Boom:
        def embed(self, *a, **k):
            raise RuntimeError("nope")

    _seed_chat_store(
        tmp_path,
        [(1, "user", "fastapi please"), (2, "assistant", "ok")],
    )
    # _Boom raises during query embedding -> ChatEmbeddingStore falls back
    # to lexical recall, which DOES match. So the result may be non-empty;
    # the contract here is "no exception escapes".
    res = maybe_recall(
        chat_dir=tmp_path,
        history_len=MIN_HISTORY + 1,
        last_user_text="fastapi",
        embedding_client=_Boom(),
        embedding_model="fake",
    )
    assert isinstance(res, AutoRecallResult)


def test_skips_when_chat_dir_missing(tmp_path: Path) -> None:
    res = maybe_recall(
        chat_dir=tmp_path / "does-not-exist",
        history_len=10,
        last_user_text="anything",
        embedding_client=_FakeEmbedder(),
        embedding_model="fake",
    )
    assert res.system_text == ""
