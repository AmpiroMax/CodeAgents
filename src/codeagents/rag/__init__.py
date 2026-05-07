"""Retrieval-augmented generation: workspace indexing + per-chat embeddings.

Stage 1 re-exports the existing flat modules. Stage 7 will collapse the
flat files into this package proper.
"""

from codeagents.chat_rag import (
    ChatEmbeddingStore,
    RecallHit,
    index_pending_chat_messages,
)
from codeagents.index_worker import WorkspaceIndexWorker
from codeagents.indexer import (
    ChunkRecord,
    EmbeddingClient,
    FileRecord,
    IgnoreRules,
    SearchResult,
    SymbolRecord,
    WorkspaceIndex,
    WorkspaceIndexer,
    build_index,
    cosine_similarity,
    extract_symbols,
    index_summary,
    search_index,
)

__all__ = [
    "ChatEmbeddingStore",
    "ChunkRecord",
    "EmbeddingClient",
    "FileRecord",
    "IgnoreRules",
    "WorkspaceIndexWorker",
    "RecallHit",
    "SearchResult",
    "SymbolRecord",
    "WorkspaceIndex",
    "WorkspaceIndexer",
    "build_index",
    "cosine_similarity",
    "extract_symbols",
    "index_pending_chat_messages",
    "index_summary",
    "search_index",
]
