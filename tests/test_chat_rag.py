from __future__ import annotations

from pathlib import Path

from codeagents.rag.chat_embeddings import ChatEmbeddingStore, index_pending_chat_messages


class FakeEmbedder:
    """Returns a 3-dim vector based on word counts. Deterministic."""

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            low = text.lower()
            out.append(
                [float(low.count("auth")), float(low.count("billing")), 1.0]
            )
        return out


def test_index_message_idempotent_on_message_index(tmp_path: Path) -> None:
    store = ChatEmbeddingStore(tmp_path)
    n1 = store.index_message(
        message_index=1,
        role="user",
        text="Please add auth auth flow to the app",
        embedding_client=FakeEmbedder(),
        embedding_model="fake",
    )
    n2 = store.index_message(
        message_index=1,
        role="user",
        text="Different text but same idx",
        embedding_client=FakeEmbedder(),
        embedding_model="fake",
    )
    assert n1 >= 1
    assert n2 == 0


def test_recall_returns_relevant_hit_first(tmp_path: Path) -> None:
    pending = [
        (1, "user", "I want to discuss auth auth and login flow"),
        (2, "assistant", "Sure, here's how billing billing works"),
        (3, "user", "Thanks for explaining"),
    ]
    n = index_pending_chat_messages(
        chat_dir=tmp_path,
        messages=pending,
        embedding_client=FakeEmbedder(),
        embedding_model="fake",
    )
    assert n >= 3

    store = ChatEmbeddingStore(tmp_path)
    hits = store.recall(
        "auth login",
        embedding_client=FakeEmbedder(),
        embedding_model="fake",
        k=2,
    )
    assert hits
    assert hits[0].message_index == 1
    assert hits[0].role == "user"


def test_recall_falls_back_to_lexical_when_embedder_fails(tmp_path: Path) -> None:
    class BrokenEmbedder:
        def embed(self, texts, *, model=None):
            raise RuntimeError("ollama offline")

    pending = [(1, "user", "secret recipe lemon cake")]
    index_pending_chat_messages(
        chat_dir=tmp_path,
        messages=pending,
        embedding_client=BrokenEmbedder(),
        embedding_model="fake",
    )
    store = ChatEmbeddingStore(tmp_path)
    hits = store.recall(
        "lemon",
        embedding_client=BrokenEmbedder(),
        embedding_model="fake",
        k=3,
    )
    assert hits
    assert "lemon" in hits[0].text
