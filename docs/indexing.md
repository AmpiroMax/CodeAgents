# Code And File Indexing

## Principles

The agent should not solve repository understanding by dumping the whole repository into the prompt. It should build a local index, retrieve selectively, and read exact files before editing.

Indexing is local-first and incremental.

## Pipeline

1. Workspace scan
   - Walk the project root.
   - Respect `.gitignore` where possible.
   - Exclude generated and heavy directories such as `.git`, `.venv`, `target`, `node_modules`, `dist`, `build`, and cache folders.

2. File metadata
   - Track path, extension, language, size, modified time, and content hash.
   - Skip binary files and oversized files by default.

3. Symbol map
   - Python: use `ast` first, tree-sitter later if needed.
   - Rust: use tree-sitter or `rust-analyzer` data later.
   - Store classes, functions, modules, exported items, and entrypoints.

4. Lexical search
   - Use `rg` directly for exact search in the MVP.
   - Add a persistent lexical index only if startup latency becomes painful.

5. Embedding index
   - Chunk code and markdown documents.
   - Store embeddings locally in SQLite-vec, LanceDB, or Qdrant.
   - Use this for semantic search, not as a replacement for reading the source file.

6. Repo summaries
   - Generate short summaries for directories and key files.
   - Keep these stable so prompt caching can work.

7. Watch mode
   - Watch file changes and update changed records only.
   - Full reindex is reserved for first run or large project moves.

## Query Flow

For code Q&A:

1. Use repo map to identify likely directories.
2. Run lexical search for exact names and errors.
3. Run semantic retrieval when exact search is insufficient.
4. Read the minimal relevant files.
5. Answer with file references.

For code edits:

1. Gather minimal context.
2. Draft a patch.
3. Explain the patch and risk.
4. Apply only after confirmation or in `workspace-write` mode.
5. Run targeted checks.
