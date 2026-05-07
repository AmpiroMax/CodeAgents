"""RAG-flavoured tools (semantic code search + chat recall).

These were previously inlined in ``tools_native/code.py`` and only
exist on top of the Phase 1 indexer / chat-RAG infrastructure. Pulled
into a dedicated module so the main file shrinks and the dependency
graph here is honest: we depend on :mod:`codeagents.indexer`,
:mod:`codeagents.chat_rag`, :mod:`codeagents.runtime`, and nothing else.
"""

from __future__ import annotations

from typing import Any

from codeagents.workspace import Workspace


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required string field: {key}")
    return value


def search_code(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Run :class:`WorkspaceIndexer.search` against the agent's workspace.

    Falls back to lexical-only mode if no embedding runtime is available
    (e.g. the user hasn't pulled ``embeddinggemma:300m`` yet). Errors from
    the embedder are returned in ``embedding_error`` so the model can decide
    whether to retry without semantic mode.
    """

    from codeagents.config import load_app_config
    from codeagents.indexer import WorkspaceIndexer
    from codeagents.runtime import OpenAICompatibleRuntime, RuntimeErrorWithHint

    query = _require_str(args, "query")
    k = max(1, min(int(args.get("k", 8)), 50))
    scope = str(args.get("scope", "workspace")).lower()
    base_root = workspace.root
    cfg = load_app_config()
    embedding_model = cfg.runtime.embedding_model or None
    indexer = WorkspaceIndexer(base_root)
    embedder: OpenAICompatibleRuntime | None = None
    embedding_error: str | None = None
    try:
        embedder = OpenAICompatibleRuntime(cfg.runtime)
    except Exception as exc:
        embedding_error = f"runtime not available: {exc}"
    try:
        results = indexer.search(
            query,
            semantic=embedder is not None,
            embedding_client=embedder,
            embedding_model=embedding_model,
            limit=k,
        )
    except RuntimeErrorWithHint as exc:
        embedding_error = str(exc)
        results = indexer.search(query, semantic=False, limit=k)

    if scope == "current_dir" and workspace.cwd and workspace.cwd != workspace.root:
        try:
            sub = workspace.cwd.relative_to(workspace.root).as_posix()
        except ValueError:
            sub = ""
        if sub:
            results = [r for r in results if r.path.startswith(sub + "/") or r.path == sub]

    payload: dict[str, Any] = {
        "query": query,
        "scope": scope,
        "results": [
            {
                "path": r.path,
                "kind": r.kind,
                "score": round(r.score, 4),
                "start_line": r.start_line,
                "end_line": r.end_line,
                "name": r.name,
                "preview": r.preview,
            }
            for r in results
        ],
    }
    if embedding_error:
        payload["embedding_error"] = embedding_error
    return payload


def recall_chat(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Surface old messages from the active chat through ``ChatEmbeddingStore``.

    The chat directory is resolved from ``workspace.chat_id`` (set by the
    agent at the start of every turn) and the global chats root.
    """

    from codeagents.chat_rag import ChatEmbeddingStore
    from codeagents.chat_store import default_chats_dir
    from codeagents.config import load_app_config
    from codeagents.runtime import OpenAICompatibleRuntime

    query = _require_str(args, "query")
    k = max(1, min(int(args.get("k", 5)), 20))
    chat_id = (workspace.chat_id or "").strip()
    if not chat_id:
        return {"error": "no active chat", "hits": []}
    chat_dir = default_chats_dir() / chat_id
    if not chat_dir.is_dir():
        return {"hits": [], "note": "chat folder missing"}

    cfg = load_app_config()
    try:
        embedder = OpenAICompatibleRuntime(cfg.runtime)
    except Exception as exc:
        return {"hits": [], "embedding_error": str(exc)}

    store = ChatEmbeddingStore(chat_dir)
    hits = store.recall(
        query,
        embedding_client=embedder,
        embedding_model=cfg.runtime.embedding_model,
        k=k,
    )
    return {
        "query": query,
        "hits": [
            {
                "score": round(h.score, 4),
                "role": h.role,
                "message_index": h.message_index,
                "preview": h.preview,
                "text": h.text,
            }
            for h in hits
        ],
    }
