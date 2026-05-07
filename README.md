# CodeAgents

Local agent platform for a MacBook M3 Max: a coding agent, a general local chat agent, and a future speech layer.

The first implementation target is a fast MVP:

- OpenAI-compatible local inference runtime: Ollama, llama.cpp server, or MLX wrapper.
- Python agent core: routing, tools, permissions, audit log, CLI.
- MCP as the standard integration surface for external tools.
- Rust reserved for fast/system tools and a future tool-host service.

## Current Scope

In scope now:

- CLI/TUI-oriented coding agent foundation.
- Local chat/API foundation.
- File/code indexing plan and initial scanner.
- Tool registry with permission classes.
- MCP-ready tool architecture.

Out of scope for the current MVP:

- ASR implementation.
- TTS implementation.
- Real-time speech-to-speech.

Speech plans are documented in `docs/speech-future-plan.md`.

## Quick Start

```bash
python -m codeagents.cli models
python -m codeagents.cli tools
python -m codeagents.cli index .
python -m codeagents.cli ask "Привет, что ты умеешь?"
```

By default the runtime expects an OpenAI-compatible server at `http://localhost:11434/v1`.

When `python -m codeagents.cli serve` (or equivalent) is running, **GET `/metrics/resources`** returns a JSON snapshot of Ollama model directories on disk, loaded models via Ollama `/api/ps`, and NVIDIA GPUs when `nvidia-smi` is available.

- **POST `/chat/upload`** — save a base64 file into `<workspace>/.codeagents/<subdir>/` (for GUI attachments). Body: `filename`, `content_base64`, optional `subdir` (single path segment).
- **MCP**: enable servers under `[mcp.*]` in `registry/mcp.toml`; set `CODEAGENTS_DISABLE_MCP=1` to skip discovery. External clients can run **`codeagents-mcp`** (stdio) to call CodeAgents workspace tools; set `CODEAGENTS_WORKSPACE`.
- **LSP**: optional `config/lsp.toml` — enable a server to register the `lsp_query` tool.

## Documents

- `docs/architecture.md` - system architecture and module boundaries.
- `docs/interaction.md` - how the user interacts with the agents and what access they get.
- `docs/indexing.md` - local code/file indexing strategy.
- `docs/local-inference-service.md` - shared local inference service for agents and autojudging.
- `docs/runtime.md` - Ollama, llama.cpp, and MLX runtime setup.
- `docs/services_manager.md` - separate `ca-services` app for launching, logging, and profiling inference services.
- `docs/speech-future-plan.md` - ASR and speech plans only, not current implementation.
- `docs/terminal_cli.md` - install and use the Rust `ca` terminal client.
- `docs/references/ollama/` - mirrored Ollama docs pages (tool calling, web search, integrations) plus `llms.txt` index.
- `docs/research/AGENT_SYSTEMS_READING_LIST.md` - curated links: MCP clones, papers, RAG/agents.
