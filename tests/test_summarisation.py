from __future__ import annotations

from typing import Any

import pytest

from codeagents.core.conversation.summarisation import (
    KEEP_TAIL,
    SUMMARY_TAG_PREFIX,
    collapse_messages,
    is_summary_block,
    needs_summary,
)


def _msgs(n: int) -> list[dict[str, Any]]:
    """Make a small synthetic chat: 1 system + n user/assistant pairs."""
    out: list[dict[str, Any]] = [{"role": "system", "content": "You are an assistant."}]
    for i in range(n):
        out.append({"role": "user", "content": f"user message {i} " + "x" * 40})
        out.append({"role": "assistant", "content": f"assistant reply {i} " + "y" * 40})
    return out


def _summarise(corpus: str) -> str:
    return f"DENSE-SUMMARY({len(corpus)} chars)"


def test_needs_summary_threshold() -> None:
    assert needs_summary(estimated_tokens=900, ctx_window=1000)
    assert not needs_summary(estimated_tokens=800, ctx_window=1000)
    assert not needs_summary(estimated_tokens=900, ctx_window=0)


def test_collapse_keeps_tail_and_first_system() -> None:
    msgs = _msgs(20)  # 1 system + 40 chat msgs
    res = collapse_messages(msgs, summarise=_summarise)
    assert res.applied
    # head system + summary + KEEP_TAIL recent.
    assert len(res.new_messages) == 1 + 1 + KEEP_TAIL
    assert res.new_messages[0]["role"] == "system"
    assert "You are an assistant" in res.new_messages[0]["content"]
    assert is_summary_block(res.new_messages[1])
    assert "DENSE-SUMMARY" in res.new_messages[1]["content"]
    # Last message in result == last message of input.
    assert res.new_messages[-1] == msgs[-1]
    # Dropped count = total middle (everything except head system + tail).
    assert res.dropped == len(msgs) - 1 - KEEP_TAIL


def test_collapse_skips_when_chat_short() -> None:
    msgs = _msgs(2)  # 1 + 4 = 5 messages, way under KEEP_TAIL+1
    res = collapse_messages(msgs, summarise=_summarise)
    assert not res.applied
    assert res.new_messages is msgs


def test_idempotent_when_only_summary_already_present() -> None:
    msgs = _msgs(20)
    first = collapse_messages(msgs, summarise=_summarise)
    assert first.applied

    # Second call WITHOUT new old turns: head + summary + same tail.
    second = collapse_messages(first.new_messages, summarise=_summarise)
    assert not second.applied, "no new old content -> no re-summary"


def test_resummary_extends_with_new_old_turns() -> None:
    msgs = _msgs(20)
    first = collapse_messages(msgs, summarise=_summarise)
    new_messages = first.new_messages
    # Push 8 new turns onto the chat - they slide out of the tail next round.
    for i in range(8):
        new_messages.append({"role": "user", "content": f"new-user-{i} " + "z" * 40})
        new_messages.append({"role": "assistant", "content": f"new-asst-{i} " + "w" * 40})

    second = collapse_messages(new_messages, summarise=_summarise)
    assert second.applied
    # Summary version should bump v1 -> v2.
    summary_block = next(m for m in second.new_messages if is_summary_block(m))
    assert summary_block["content"].startswith(SUMMARY_TAG_PREFIX + "2]")


def test_fallback_when_summarise_returns_empty() -> None:
    msgs = _msgs(20)
    res = collapse_messages(msgs, summarise=lambda corpus: "")
    assert res.applied
    summary = next(m for m in res.new_messages if is_summary_block(m))
    assert "[summary v1]" in summary["content"]
    # Fallback contains bullet list of dropped turns.
    assert "- [user]" in summary["content"] or "- [assistant]" in summary["content"]


def test_fallback_when_summarise_raises() -> None:
    msgs = _msgs(20)

    def boom(corpus: str) -> str:
        raise RuntimeError("model offline")

    res = collapse_messages(msgs, summarise=boom)
    assert res.applied
    # Still produced a summary block via the fallback path.
    assert any(is_summary_block(m) for m in res.new_messages)
