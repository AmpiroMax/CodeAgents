from __future__ import annotations

import ast
import hashlib
import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    "node_modules",
    "dist",
    "build",
    ".codeagents",
}

INDEX_DIR = ".codeagents"
INDEX_DB = "index.sqlite3"


@dataclass(frozen=True)
class FileRecord:
    path: str
    language: str
    size_bytes: int
    sha256: str
    mtime_ns: int = 0


@dataclass(frozen=True)
class WorkspaceIndex:
    root: str
    files: list[FileRecord]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class SymbolRecord:
    path: str
    name: str
    kind: str
    line: int
    end_line: int
    parent: str = ""


@dataclass(frozen=True)
class ChunkRecord:
    id: str
    path: str
    kind: str
    start_line: int
    end_line: int
    content_hash: str
    text: str
    preview: str


@dataclass(frozen=True)
class SearchResult:
    path: str
    kind: str
    score: float
    start_line: int = 0
    end_line: int = 0
    name: str = ""
    preview: str = ""


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        ...


class WorkspaceIndexer:
    def __init__(
        self,
        root: Path,
        *,
        db_path: Path | None = None,
        max_file_bytes: int = 1_000_000,
    ) -> None:
        self.root = root.resolve()
        self.db_path = db_path or self.root / INDEX_DIR / INDEX_DB
        self.max_file_bytes = max_file_bytes
        self.ignore_rules = IgnoreRules.from_workspace(self.root)

    def build(
        self,
        *,
        embeddings: bool = False,
        embedding_client: EmbeddingClient | None = None,
        embedding_model: str | None = None,
    ) -> WorkspaceIndex:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            _init_db(conn)
            records = self._scan_records()
            self._delete_removed(conn, {record.path for record in records})
            changed = [record for record in records if self._upsert_file_if_changed(conn, record)]
            for record in changed:
                self._replace_file_details(conn, record)
            if embeddings and embedding_client is not None:
                self._embed_missing(conn, embedding_client=embedding_client, model=embedding_model)
            conn.commit()
        return WorkspaceIndex(root=str(self.root), files=records)

    def summary(self, *, limit_dirs: int = 12) -> dict[str, object]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            _init_db(conn)
            total_files = conn.execute("select count(*) from files").fetchone()[0]
            total_symbols = conn.execute("select count(*) from symbols").fetchone()[0]
            total_chunks = conn.execute("select count(*) from chunks").fetchone()[0]
            embedded_chunks = conn.execute("select count(*) from embeddings").fetchone()[0]
            languages = {
                row["language"]: row["count"]
                for row in conn.execute(
                    "select language, count(*) as count from files group by language order by count desc"
                )
            }
            dirs: dict[str, int] = {}
            for row in conn.execute("select path from files"):
                top = str(row["path"]).split("/", 1)[0]
                dirs[top] = dirs.get(top, 0) + 1
            top_dirs = dict(sorted(dirs.items(), key=lambda item: (-item[1], item[0]))[:limit_dirs])
        return {
            "root": str(self.root),
            "files": total_files,
            "symbols": total_symbols,
            "chunks": total_chunks,
            "embedded_chunks": embedded_chunks,
            "languages": languages,
            "top_dirs": top_dirs,
        }

    def search(
        self,
        query: str,
        *,
        semantic: bool = False,
        embedding_client: EmbeddingClient | None = None,
        embedding_model: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        query = query.strip()
        if not query:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            _init_db(conn)
            results = self._symbol_search(conn, query, limit=limit)
            results.extend(self._chunk_lexical_search(conn, query, limit=limit))
            if semantic and embedding_client is not None:
                results.extend(
                    self._semantic_search(
                        conn,
                        query,
                        embedding_client=embedding_client,
                        model=embedding_model,
                        limit=limit,
                    )
                )
        return _dedupe_results(sorted(results, key=lambda item: item.score, reverse=True))[:limit]

    def _scan_records(self) -> list[FileRecord]:
        records: list[FileRecord] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or self.ignore_rules.ignores(path) or _is_excluded(self.root, path):
                continue
            stat = path.stat()
            if stat.st_size > self.max_file_bytes:
                continue
            content = _read_bytes_if_text(path)
            if content is None:
                continue
            records.append(
                FileRecord(
                    path=str(path.relative_to(self.root)),
                    language=_language_for(path),
                    size_bytes=stat.st_size,
                    sha256=hashlib.sha256(content).hexdigest(),
                    mtime_ns=stat.st_mtime_ns,
                )
            )
        return records

    def _delete_removed(self, conn: sqlite3.Connection, current_paths: set[str]) -> None:
        existing = {row[0] for row in conn.execute("select path from files")}
        for path in sorted(existing - current_paths):
            conn.execute("delete from files where path = ?", (path,))

    def _upsert_file_if_changed(self, conn: sqlite3.Connection, record: FileRecord) -> bool:
        row = conn.execute(
            "select sha256, size_bytes, mtime_ns from files where path = ?",
            (record.path,),
        ).fetchone()
        if row and row[0] == record.sha256 and row[1] == record.size_bytes and row[2] == record.mtime_ns:
            return False
        conn.execute(
            """
            insert into files(path, language, size_bytes, mtime_ns, sha256, indexed_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(path) do update set
              language = excluded.language,
              size_bytes = excluded.size_bytes,
              mtime_ns = excluded.mtime_ns,
              sha256 = excluded.sha256,
              indexed_at = excluded.indexed_at
            """,
            (record.path, record.language, record.size_bytes, record.mtime_ns, record.sha256, time.time()),
        )
        return True

    def _replace_file_details(self, conn: sqlite3.Connection, record: FileRecord) -> None:
        conn.execute("delete from symbols where path = ?", (record.path,))
        conn.execute("delete from chunks where path = ?", (record.path,))
        path = self.root / record.path
        text = path.read_text(encoding="utf-8")
        for symbol in extract_symbols(path, text=text, root=self.root):
            conn.execute(
                """
                insert into symbols(path, name, kind, line, end_line, parent)
                values (?, ?, ?, ?, ?, ?)
                """,
                (symbol.path, symbol.name, symbol.kind, symbol.line, symbol.end_line, symbol.parent),
            )
        for chunk in build_chunks(path, text=text, root=self.root):
            conn.execute(
                """
                insert into chunks(id, path, kind, start_line, end_line, content_hash, text, preview)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.id,
                    chunk.path,
                    chunk.kind,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.content_hash,
                    chunk.text,
                    chunk.preview,
                ),
            )

    def _embed_missing(
        self,
        conn: sqlite3.Connection,
        *,
        embedding_client: EmbeddingClient,
        model: str | None,
    ) -> None:
        rows = list(
            conn.execute(
                """
                select c.id, c.text
                from chunks c
                left join embeddings e on e.chunk_id = c.id and e.model = ?
                where e.chunk_id is null
                order by c.path, c.start_line
                """,
                (model or "",),
            )
        )
        batch_size = 32
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            vectors = embedding_client.embed([row[1] for row in batch], model=model)
            for row, vector in zip(batch, vectors, strict=True):
                conn.execute(
                    """
                    insert or replace into embeddings(chunk_id, model, dimensions, vector_json)
                    values (?, ?, ?, ?)
                    """,
                    (row[0], model or "", len(vector), json.dumps(vector, separators=(",", ":"))),
                )

    def _symbol_search(
        self, conn: sqlite3.Connection, query: str, *, limit: int
    ) -> list[SearchResult]:
        pattern = f"%{query}%"
        rows = conn.execute(
            """
            select path, name, kind, line, end_line
            from symbols
            where name like ? or path like ?
            order by case when name = ? then 0 else 1 end, path, line
            limit ?
            """,
            (pattern, pattern, query, limit),
        )
        return [
            SearchResult(
                path=row["path"],
                name=row["name"],
                kind=row["kind"],
                score=1.0 if row["name"] == query else 0.75,
                start_line=row["line"],
                end_line=row["end_line"],
                preview=f"{row['kind']} {row['name']}",
            )
            for row in rows
        ]

    def _chunk_lexical_search(
        self, conn: sqlite3.Connection, query: str, *, limit: int
    ) -> list[SearchResult]:
        pattern = f"%{query}%"
        exact_rows = conn.execute(
            """
            select path, kind, start_line, end_line, preview
            from chunks
            where text like ? or path like ?
            order by path, start_line
            limit ?
            """,
            (pattern, pattern, limit),
        )
        results = [
            SearchResult(
                path=row["path"],
                kind=row["kind"],
                score=0.6,
                start_line=row["start_line"],
                end_line=row["end_line"],
                preview=row["preview"],
            )
            for row in exact_rows
        ]
        tokens = [token.lower() for token in query.split() if len(token) >= 3]
        if len(results) >= limit or not tokens:
            return results
        rows = conn.execute(
            "select path, kind, start_line, end_line, preview, text from chunks order by path, start_line"
        )
        for row in rows:
            haystack = f"{row['path']}\n{row['preview']}\n{row['text']}".lower()
            matched = sum(1 for token in tokens if token in haystack)
            if matched == 0:
                continue
            results.append(
                SearchResult(
                    path=row["path"],
                    kind=row["kind"],
                    score=0.35 + (matched / len(tokens)) * 0.2,
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    preview=row["preview"],
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:limit]

    def _semantic_search(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        embedding_client: EmbeddingClient,
        model: str | None,
        limit: int,
    ) -> list[SearchResult]:
        query_vector = embedding_client.embed([query], model=model)[0]
        rows = conn.execute(
            """
            select c.path, c.kind, c.start_line, c.end_line, c.preview, e.vector_json
            from embeddings e
            join chunks c on c.id = e.chunk_id
            where e.model = ?
            """,
            (model or "",),
        )
        scored: list[SearchResult] = []
        for row in rows:
            vector = json.loads(row["vector_json"])
            score = cosine_similarity(query_vector, vector)
            scored.append(
                SearchResult(
                    path=row["path"],
                    kind=row["kind"],
                    score=score,
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    preview=row["preview"],
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


class IgnoreRules:
    def __init__(self, root: Path, patterns: list[str]) -> None:
        self.root = root
        self.patterns = patterns

    @classmethod
    def from_workspace(cls, root: Path) -> "IgnoreRules":
        patterns: list[str] = []
        for name in (".gitignore", ".cursorignore"):
            path = root / name
            if not path.exists():
                continue
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
        return cls(root, patterns)

    def ignores(self, path: Path) -> bool:
        rel = path.relative_to(self.root).as_posix()
        ignored = False
        for pattern in self.patterns:
            negated = pattern.startswith("!")
            clean = pattern[1:] if negated else pattern
            if _matches_ignore(clean, rel):
                ignored = not negated
        return ignored


def build_index(
    root: Path,
    *,
    max_file_bytes: int = 1_000_000,
    embeddings: bool = False,
    embedding_client: EmbeddingClient | None = None,
    embedding_model: str | None = None,
) -> WorkspaceIndex:
    root = root.resolve()
    indexer = WorkspaceIndexer(root, max_file_bytes=max_file_bytes)
    return indexer.build(
        embeddings=embeddings,
        embedding_client=embedding_client,
        embedding_model=embedding_model,
    )


def search_index(
    root: Path,
    query: str,
    *,
    semantic: bool = False,
    embedding_client: EmbeddingClient | None = None,
    embedding_model: str | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    return WorkspaceIndexer(root).search(
        query,
        semantic=semantic,
        embedding_client=embedding_client,
        embedding_model=embedding_model,
        limit=limit,
    )


def index_summary(root: Path) -> dict[str, object]:
    return WorkspaceIndexer(root).summary()


def extract_symbols(path: Path, *, text: str, root: Path | None = None) -> list[SymbolRecord]:
    if path.suffix.lower() != ".py":
        return []
    rel_path = str(path.relative_to(root)) if root else path.name
    records: list[SymbolRecord] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return records

    def visit_body(body: list[ast.stmt], parent: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                records.append(_symbol_from_node(rel_path, node.name, "class", node, parent))
                visit_body(node.body, parent=node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                records.append(_symbol_from_node(rel_path, node.name, kind, node, parent))
                visit_body(node.body, parent=node.name if not parent else f"{parent}.{node.name}")

    visit_body(tree.body)
    return records


def build_chunks(path: Path, *, text: str, root: Path) -> list[ChunkRecord]:
    rel = str(path.relative_to(root))
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _python_chunks(rel, text)
    if suffix in {".md", ".markdown"}:
        return _line_chunks(rel, "markdown", text, max_lines=80)
    return _line_chunks(rel, _language_for(path), text, max_lines=120)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("pragma foreign_keys = on")
    conn.execute(
        """
        create table if not exists files (
          path text primary key,
          language text not null,
          size_bytes integer not null,
          mtime_ns integer not null,
          sha256 text not null,
          indexed_at real not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists symbols (
          id integer primary key autoincrement,
          path text not null references files(path) on delete cascade,
          name text not null,
          kind text not null,
          line integer not null,
          end_line integer not null,
          parent text not null default ''
        )
        """
    )
    conn.execute("create index if not exists idx_symbols_name on symbols(name)")
    conn.execute("create index if not exists idx_symbols_path on symbols(path)")
    conn.execute(
        """
        create table if not exists chunks (
          id text primary key,
          path text not null references files(path) on delete cascade,
          kind text not null,
          start_line integer not null,
          end_line integer not null,
          content_hash text not null,
          text text not null,
          preview text not null
        )
        """
    )
    conn.execute("create index if not exists idx_chunks_path on chunks(path)")
    conn.execute(
        """
        create table if not exists embeddings (
          chunk_id text not null references chunks(id) on delete cascade,
          model text not null,
          dimensions integer not null,
          vector_json text not null,
          primary key(chunk_id, model)
        )
        """
    )


def _is_excluded(root: Path, path: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    return any(part in DEFAULT_EXCLUDES for part in relative_parts)


def _read_bytes_if_text(path: Path) -> bytes | None:
    content = path.read_bytes()
    if b"\x00" in content:
        return None
    return content


def _language_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".py": "python",
        ".rs": "rust",
        ".md": "markdown",
        ".toml": "toml",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix, "text")


def _matches_ignore(pattern: str, rel: str) -> bool:
    import fnmatch

    pattern = pattern.strip("/")
    if not pattern:
        return False
    if "/" in pattern:
        return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, f"{pattern}/**")
    return any(fnmatch.fnmatch(part, pattern) for part in rel.split("/"))


def _symbol_from_node(
    rel_path: str,
    name: str,
    kind: str,
    node: ast.AST,
    parent: str,
) -> SymbolRecord:
    line = getattr(node, "lineno", 1)
    end_line = getattr(node, "end_lineno", line)
    return SymbolRecord(
        path=rel_path,
        name=name,
        kind=kind,
        line=line,
        end_line=end_line,
        parent=parent,
    )


def _python_chunks(rel: str, text: str) -> list[ChunkRecord]:
    lines = text.splitlines()
    chunks: list[ChunkRecord] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _line_chunks(rel, "python", text, max_lines=120)
    nodes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for node in sorted(nodes, key=lambda item: getattr(item, "lineno", 0)):
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        chunk_text = "\n".join(lines[start - 1:end])
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        chunks.append(_make_chunk(rel, kind, start, end, chunk_text))
    if not chunks and text.strip():
        chunks.append(_make_chunk(rel, "module", 1, max(len(lines), 1), text))
    return chunks


def _line_chunks(rel: str, kind: str, text: str, *, max_lines: int) -> list[ChunkRecord]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[ChunkRecord] = []
    for start in range(0, len(lines), max_lines):
        selected = lines[start : start + max_lines]
        chunk_text = "\n".join(selected)
        chunks.append(_make_chunk(rel, kind, start + 1, start + len(selected), chunk_text))
    return chunks


def _make_chunk(rel: str, kind: str, start_line: int, end_line: int, text: str) -> ChunkRecord:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunk_id = hashlib.sha256(f"{rel}:{start_line}:{end_line}:{content_hash}".encode("utf-8")).hexdigest()
    preview = " ".join(line.strip() for line in text.splitlines() if line.strip())[:240]
    return ChunkRecord(
        id=chunk_id,
        path=rel,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
        content_hash=content_hash,
        text=text,
        preview=preview,
    )


def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[tuple[str, int, int, str, str]] = set()
    deduped: list[SearchResult] = []
    for result in results:
        key = (result.path, result.start_line, result.end_line, result.kind, result.name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped
