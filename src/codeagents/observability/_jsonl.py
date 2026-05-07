"""Shared JSONL append helper for observability loggers.

Stage-6 carve-out: each logger (audit, inference, runtime, service request)
used to do its own ``self.path.open("a", encoding="utf-8")`` dance. Now
they all funnel through :func:`append_line` which makes parent-dir
creation, encoding and locking semantics consistent.

Intentionally accepts a pre-serialised JSON string rather than an
arbitrary object: each logger has slightly different serialisation needs
(pydantic ``model_dump_json(exclude_none=True)`` vs.
``json.dumps(asdict(event), ensure_ascii=False)`` vs. plain dict), so we
keep the choice on the caller side.
"""

from __future__ import annotations

from pathlib import Path


def append_line(path: Path, json_line: str) -> None:
    """Append ``json_line`` (without trailing newline) to ``path``.

    Creates the parent directory if missing and writes a trailing ``\\n``
    so the file remains a valid JSONL stream.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_line)
        if not json_line.endswith("\n"):
            handle.write("\n")


__all__ = ["append_line"]
