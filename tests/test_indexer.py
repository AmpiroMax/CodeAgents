from __future__ import annotations

import sqlite3
from pathlib import Path

from codeagents.indexer import (
    WorkspaceIndexer,
    build_index,
    index_summary,
    search_index,
)


class FakeEmbeddingClient:
    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vectors.append([
                float(lowered.count("auth")),
                float(lowered.count("payment")),
                1.0,
            ])
        return vectors


def test_build_index_respects_ignore_and_persists(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("def ignored():\n    pass\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")

    index = build_index(tmp_path)

    paths = {record.path for record in index.files}
    assert "keep.py" in paths
    assert "ignored.py" not in paths
    assert (tmp_path / ".codeagents" / "index.sqlite3").exists()


def test_build_index_updates_changed_files_and_symbols(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text("class Service:\n    def run(self):\n        return 1\n", encoding="utf-8")

    build_index(tmp_path)
    summary = index_summary(tmp_path)
    assert summary["symbols"] == 2

    source.write_text(
        "class Service:\n    def run(self):\n        return 1\n\ndef helper():\n    return 2\n",
        encoding="utf-8",
    )
    build_index(tmp_path)

    results = search_index(tmp_path, "helper")
    assert any(result.name == "helper" and result.path == "service.py" for result in results)


def test_python_chunks_have_line_ranges(tmp_path: Path) -> None:
    source = tmp_path / "mod.py"
    source.write_text(
        "def first():\n    return 'first'\n\n"
        "async def second():\n    return 'second'\n",
        encoding="utf-8",
    )

    build_index(tmp_path)

    with sqlite3.connect(tmp_path / ".codeagents" / "index.sqlite3") as conn:
        rows = conn.execute(
            "select kind, start_line, end_line, preview from chunks order by start_line"
        ).fetchall()

    assert rows[0][0] == "function"
    assert rows[0][1] == 1
    assert rows[0][2] == 2
    assert rows[1][1] == 4
    assert "second" in rows[1][3]


def test_semantic_search_uses_fake_embeddings(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text("def login():\n    return 'auth auth'\n", encoding="utf-8")
    (tmp_path / "billing.py").write_text(
        "def charge():\n    return 'payment payment'\n",
        encoding="utf-8",
    )

    indexer = WorkspaceIndexer(tmp_path)
    indexer.build(embeddings=True, embedding_client=FakeEmbeddingClient(), embedding_model="fake")

    results = search_index(
        tmp_path,
        "auth login",
        semantic=True,
        embedding_client=FakeEmbeddingClient(),
        embedding_model="fake",
        limit=1,
    )

    assert results
    assert results[0].path == "auth.py"
