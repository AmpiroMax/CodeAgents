"""Save chat file uploads under workspace .codeagents (used by HTTP API and tests)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def save_chat_base64_upload(
    workspace_root: Path,
    *,
    filename: str,
    content_base64: str,
    subdir: str = "uploads",
) -> dict[str, Any]:
    """Decode base64 and write to ``<workspace>/.codeagents/<subdir>/<basename>``.

    Returns ``{"saved": relative_path_str, "bytes": n}``. Raises ``ValueError`` on bad input.
    """
    if not isinstance(content_base64, str):
        raise ValueError("content_base64 must be a string")
    data = base64.b64decode(content_base64, validate=False)
    sub = str(subdir)
    if not sub or any(sep in sub for sep in ("/", "\\")) or sub.startswith("."):
        raise ValueError("subdir must be a single path segment")
    ws = workspace_root.resolve()
    dest_dir = (ws / ".codeagents" / sub).resolve()
    if not str(dest_dir).startswith(str(ws)):
        raise ValueError("invalid destination")
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError("invalid filename")
    dest = dest_dir / safe_name
    dest.write_bytes(data)
    return {"saved": str(dest.relative_to(ws)), "bytes": len(data)}
