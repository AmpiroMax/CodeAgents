# Launcher and `ca-services`

## macOS app: `CodeAgents.app`

Primary way to run the stack without the terminal:

```bash
scripts/install_app.sh
```

This builds **`/Applications/CodeAgents.app`** (override with `CODEAGENTS_APP_INSTALL_DIR`), bundles:

- `ca-services` (Rust) in `Contents/Resources/`
- built web UI (`gui/dist`) in `Contents/Resources/gui/`
- native shell (`CodeAgents`) with **WebKit** for the chat tab

Behavior:

- On launch, **starts Ollama** (if not listening on `:11434`) and **`codeagents serve`** on `:8765` with:
  - `--workspace` = `~/Documents` by default, or the folder chosen via **«Папка workspace…»** (also written to `~/.codeagents/launcher.toml` for CLI defaults).
  - `--gui-dir` = bundled `Resources/gui` so the API serves **`http://127.0.0.1:8765/ui/`**.
- **Чат** tab loads that URL inside the app (no need to run `npm run dev` for daily use).
- Quitting the app runs **`ca-services stop`** (Ollama + API on the managed ports).

`Info.plist` key **`CodeAgentsRoot`** must point at the repository clone (contains `pyproject.toml`, `.venv`, `config/`). If you move the repo, reinstall the app with `scripts/install_app.sh`.

DMG packaging:

```bash
scripts/package_dmg.sh
```

→ `dist/CodeAgents.dmg`.

## CLI: `ca-services`

Same binary the app uses; useful from a shell or scripts.

Global flags:

| Flag | Meaning |
|------|---------|
| `--root` | CodeAgents repository (sets cwd for venv + `python -m codeagents.cli`). |
| `--workspace` | Agent workspace passed to `codeagents serve` (default: `~/.codeagents/launcher.toml` or `~/Documents`). |
| `--gui-dir` | Directory with `index.html` for `/ui/` (default: `<root>/gui/dist` if present). |

Commands: `start`, `stop`, `restart`, `status`, `models`, `install`, `chat`, `logs`, `chats`, `daemon` (same semantics as before).

Example:

```bash
ca-services --root /path/to/CodeAgents --workspace /path/to/project --gui-dir /path/to/CodeAgents/gui/dist start
```

TUI **`ca`** talks to `http://127.0.0.1:8765` by default; if the app (or `ca-services start`) has started the API, **`ca` works without a separate `serve` command**.

## Logs

Under the **repository** (not the user workspace):

```text
.codeagents/services/ollama.log
.codeagents/services/ca-serve.log
```

Plus JSONL logs under `.codeagents/` as documented in the architecture doc.
