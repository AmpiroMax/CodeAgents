"""Per-chat embedding store backing the ``recall_chat`` tool.

Each chat owns its own SQLite db at ``<chat_dir>/embeddings.sqlite3`` so:

* ratchet/rotation of an old chat doesn't pollute other chats' search;
* the global code index stays small (~10k chunks) and isn't bloated by
  conversation history that's only relevant to a single thread.

Schema is intentionally minimal — we don't need cross-chat search yet:

    chunks(id, role, message_index, start_line, end_line, text, preview)
    embeddings(chunk_id, model, vector_json)

When ``sqlite-vec`` is loaded, embeddings are mirrored into ``vec_msgs``
for fast k-NN. The Phase 1 plan keeps this strictly recall-only: no
auto-substitution into the live context, the agent must explicitly call
``recall_chat`` when it wants old turns back.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from codeagents.indexer import _line_chunks, _maybe_load_vec, cosine_similarity


CHUNK_LINES_CHAT = 20
CHUNK_OVERLAP_CHAT = 4


@dataclass(frozen=True)
class RecallHit:
    score: float
    role: str
    message_index: int
    start_line: int
    end_line: int
    preview: str
    text: str


class ChatEmbeddingStore:
    def __init__(self, chat_dir: Path) -> None:
        self.chat_dir = Path(chat_dir)
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.chat_dir / "embeddings.sqlite3"

    # --- public API --------------------------------------------------

    def index_message(
        self,
        *,
        message_index: int,
        role: str,
        text: str,
        embedding_client,
        embedding_model: str | None,
    ) -> int:
        """Embed ``text`` (already-rendered plain text) into the chat store.

        Returns the number of new chunks inserted; 0 if this message was
        already indexed (idempotent on ``message_index``).
        """

        text = (text or "").strip()
        if not text:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            _init_chat_db(conn)
            existing = conn.execute(
                "select 1 from chunks where message_index = ? limit 1",
                (message_index,),
            ).fetchone()
            if existing:
                return 0
            chunks = _line_chunks(
                rel=f"msg-{message_index}",
                kind=role,
                text=text,
                max_lines=CHUNK_LINES_CHAT,
                overlap=CHUNK_OVERLAP_CHAT,
            )
            if not chunks:
                return 0
            ids: list[str] = []
            for chunk in chunks:
                cid = _chunk_id(message_index, chunk.start_line, chunk.text)
                ids.append(cid)
                conn.execute(
                    """
                    insert or replace into chunks
                    (id, role, message_index, start_line, end_line, text, preview)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid,
                        role,
                        message_index,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.text,
                        chunk.preview,
                    ),
                )
            try:
                vectors = embedding_client.embed(
                    [c.text for c in chunks], model=embedding_model
                )
            except Exception:
                # Embedder offline — keep the chunks; recall stays lexical.
                conn.commit()
                return len(chunks)
            for cid, vector in zip(ids, vectors, strict=False):
                conn.execute(
                    """
                    insert or replace into embeddings(chunk_id, model, vector_json)
                    values (?, ?, ?)
                    """,
                    (cid, embedding_model or "", json.dumps(vector, separators=(",", ":"))),
                )
            conn.commit()
            return len(chunks)

    def recall(
        self,
        query: str,
        *,
        embedding_client,
        embedding_model: str | None,
        k: int = 5,
    ) -> list[RecallHit]:
        query = (query or "").strip()
        if not query or not self.db_path.exists():
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            _init_chat_db(conn)
            try:
                qvec = embedding_client.embed([query], model=embedding_model)[0]
            except Exception:
                return self._lexical_recall(conn, query, k=k)
            rows = conn.execute(
                """
                select c.id, c.role, c.message_index, c.start_line, c.end_line,
                       c.text, c.preview, e.vector_json
                from embeddings e
                join chunks c on c.id = e.chunk_id
                where e.model = ?
                """,
                (embedding_model or "",),
            ).fetchall()
            scored: list[RecallHit] = []
            for row in rows:
                vec = json.loads(row["vector_json"])
                scored.append(
                    RecallHit(
                        score=cosine_similarity(qvec, vec),
                        role=row["role"],
                        message_index=row["message_index"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        preview=row["preview"],
                        text=row["text"],
                    )
                )
            scored.sort(key=lambda h: h.score, reverse=True)
            return scored[:k]

    def _lexical_recall(
        self, conn: sqlite3.Connection, query: str, *, k: int
    ) -> list[RecallHit]:
        pattern = f"%{query}%"
        rows = conn.execute(
            """
            select role, message_index, start_line, end_line, text, preview
            from chunks
            where text like ?
            order by message_index desc
            limit ?
            """,
            (pattern, k),
        ).fetchall()
        return [
            RecallHit(
                score=0.5,
                role=row["role"],
                message_index=row["message_index"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                preview=row["preview"],
                text=row["text"],
            )
            for row in rows
        ]


def _init_chat_db(conn: sqlite3.Connection) -> None:
    _maybe_load_vec(conn)
    conn.execute(
        """
        create table if not exists chunks (
          id text primary key,
          role text not null,
          message_index integer not null,
          start_line integer not null,
          end_line integer not null,
          text text not null,
          preview text not null
        )
        """
    )
    conn.execute(
        "create index if not exists idx_chunks_msg on chunks(message_index)"
    )
    conn.execute(
        """
        create table if not exists embeddings (
          chunk_id text not null references chunks(id) on delete cascade,
          model text not null,
          vector_json text not null,
          primary key(chunk_id, model)
        )
        """
    )


def _chunk_id(message_index: int, start_line: int, text: str) -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"m{message_index}-{start_line}-{h}"


def index_pending_chat_messages(
    *,
    chat_dir: Path,
    messages: Iterable[tuple[int, str, str]],
    embedding_client,
    embedding_model: str | None,
) -> int:
    """Bulk-embed ``[(message_index, role, text), ...]`` into the chat store."""

    store = ChatEmbeddingStore(chat_dir)
    total = 0
    for idx, role, text in messages:
        total += store.index_message(
            message_index=idx,
            role=role,
            text=text,
            embedding_client=embedding_client,
            embedding_model=embedding_model,
        )
    return total
