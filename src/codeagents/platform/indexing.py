from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from codeagents.indexer import SearchResult, WorkspaceIndexer


@runtime_checkable
class CodeIndexBackend(Protocol):
    """Abstraction over repository indexing (current SQLite implementation is the default)."""

    def workspace_root(self) -> Path: ...

    def build(self, *, embeddings: bool = False, **kwargs: Any) -> Any: ...

    def search(
        self,
        query: str,
        *,
        semantic: bool = False,
        limit: int = 10,
        embedding_client: Any = None,
        embedding_model: str | None = None,
    ) -> list[SearchResult]: ...


class SqliteCodeIndex(CodeIndexBackend):
    """Adapter wrapping `WorkspaceIndexer` for protocol typing."""

    def __init__(self, root: Path) -> None:
        self._indexer = WorkspaceIndexer(root)

    def workspace_root(self) -> Path:
        return self._indexer.root

    def build(self, *, embeddings: bool = False, **kwargs: Any) -> Any:
        from codeagents.indexer import build_index

        client = kwargs.get("embedding_client")
        model = kwargs.get("embedding_model")
        return build_index(
            self._indexer.root,
            embeddings=embeddings,
            embedding_client=client,
            embedding_model=model,
        )

    def search(
        self,
        query: str,
        *,
        semantic: bool = False,
        limit: int = 10,
        embedding_client: Any = None,
        embedding_model: str | None = None,
    ) -> list[SearchResult]:
        from codeagents.indexer import search_index

        return search_index(
            self._indexer.root,
            query,
            semantic=semantic,
            limit=limit,
            embedding_client=embedding_client,
            embedding_model=embedding_model,
        )
