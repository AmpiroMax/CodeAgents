"""Retrieval-augmented generation: workspace indexing + per-chat embeddings.

Modules:

* :mod:`codeagents.rag.workspace_index` — sqlite-vec backed code/symbol/chunk index.
* :mod:`codeagents.rag.chat_embeddings` — per-chat message embeddings + recall.
* :mod:`codeagents.rag.background_worker` — async indexer driving the workspace index.
* :mod:`codeagents.rag.kg_indexer` — knowledge-graph community indexer.
"""

from codeagents.rag.background_worker import WorkspaceIndexWorker
from codeagents.rag.chat_embeddings import (
    ChatEmbeddingStore,
    RecallHit,
    index_pending_chat_messages,
)
from codeagents.rag.workspace_index import (
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
    "RecallHit",
    "SearchResult",
    "SymbolRecord",
    "WorkspaceIndex",
    "WorkspaceIndexWorker",
    "WorkspaceIndexer",
    "build_index",
    "cosine_similarity",
    "extract_symbols",
    "index_pending_chat_messages",
    "index_summary",
    "search_index",
]
