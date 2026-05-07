"""Automatic chat recall (Phase 2.A.3 — MemGPT-style auto memory).

Before each turn we semantically search prior messages of the same chat and,
if a chunk is similar enough to the latest user message, we inject it as a
hidden ``system`` block so the model sees relevant earlier context that may
have rolled out of the prompt window.

Why automatic
-------------
The ``recall_chat`` tool is great when the model proactively remembers it
needs context, but in practice models often forget. Auto-recall fixes the
common failure mode "user said X 30 turns ago, now they say 'continue with
that'".

Design
------
- Triggered only after ``MIN_HISTORY`` turns (small chats fit fully).
- Top-k results are filtered by ``threshold`` (default 0.55). Anything
  below is treated as noise.
- Recall is *additive*: the existing ``messages`` list is left untouched
  and we return the system block to be inserted by the caller; this keeps
  persistence (chat_store) decoupled from runtime context shaping.
- All exceptions are swallowed - auto-recall must never break a turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


MIN_HISTORY = 6  # don't bother for short chats
DEFAULT_THRESHOLD = 0.55
DEFAULT_TOP_K = 3
PREVIEW_CHARS_PER_HIT = 320  # cap each hit's text


@dataclass
class AutoRecallResult:
    system_text: str  # rendered hidden system block ("" when empty)
    hit_count: int
    top_score: float
    estimate_chars: int  # quick proxy for tokens used by callers


def maybe_recall(
    *,
    chat_dir: Path | None,
    history_len: int,
    last_user_text: str,
    embedding_client: Any,
    embedding_model: str | None,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_THRESHOLD,
) -> AutoRecallResult:
    """Run auto-recall and return a ready-to-inject system block.

    ``embedding_client`` should expose ``.embed(texts, model=...)`` (matches
    ``OpenAICompatibleRuntime``). When the embedder is offline the underlying
    store falls back to lexical matching, so we can still provide *something*
    useful.
    """

    empty = AutoRecallResult(system_text="", hit_count=0, top_score=0.0, estimate_chars=0)

    if not last_user_text or not last_user_text.strip():
        return empty
    if history_len < MIN_HISTORY:
        return empty
    if chat_dir is None or not Path(chat_dir).is_dir():
        return empty
    if embedding_client is None:
        return empty

    try:
        from codeagents.chat_rag import ChatEmbeddingStore

        store = ChatEmbeddingStore(Path(chat_dir))
        hits = store.recall(
            last_user_text,
            embedding_client=embedding_client,
            embedding_model=embedding_model,
            k=top_k,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("auto_recall failed: %s", exc)
        return empty

    relevant = [h for h in hits if h.score >= threshold]
    if not relevant:
        return empty

    lines: list[str] = [
        "[auto-recall] Earlier in this chat you may want to remember:",
    ]
    for hit in relevant:
        snippet = (hit.text or "").strip()
        if len(snippet) > PREVIEW_CHARS_PER_HIT:
            snippet = snippet[: PREVIEW_CHARS_PER_HIT - 1] + "\u2026"
        lines.append(
            f"- ({hit.role}, msg {hit.message_index}, score {hit.score:.2f}) {snippet}"
        )
    text = "\n".join(lines)

    return AutoRecallResult(
        system_text=text,
        hit_count=len(relevant),
        top_score=relevant[0].score,
        estimate_chars=len(text),
    )


__all__ = ["AutoRecallResult", "maybe_recall", "MIN_HISTORY", "DEFAULT_THRESHOLD"]
