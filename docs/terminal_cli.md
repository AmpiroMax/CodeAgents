# Terminal CLI

`ca` is the terminal-first CodeAgents client. It is a clean-room terminal UI inspired by agentic coding CLIs, but it uses local CodeAgents models and tools.

## Install

From the project root:

```bash
./scripts/install.sh
```

By default it symlinks the binary to:

```bash
~/.local/bin/ca
```

If needed, add this to `~/.zshrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Start Backend

`ca` talks to the Python CodeAgents API. Start it with:

```bash
ca serve
```

If the port is already busy, `ca serve` asks whether to stop the existing process and restart the backend. To skip the prompt:

```bash
ca serve --yes
```

Explicit restart:

```bash
ca restart
```

Equivalent direct command:

```bash
.venv/bin/python -m codeagents.cli serve --port 8765
```

## Commands

```bash
ca health
ca models
ca inference-models
ca tools
ca chats
ca new "Feature work"
ca open <chat_id>
ca ask "Привет, ты работаешь?"
ca code "Посмотри проект и предложи следующий шаг"
ca index .
ca tool read_file '{"path":"README.md","limit":5}'
```

Open the interactive TUI:

```bash
ca
```

or:

```bash
ca tui --task code
```

## TUI Commands

Inside the TUI:

- `/help` - show commands.
- `/health` - check backend.
- `/models` - show registered inference models.
- `/tools` - show backend tools.
- `/model general|code|fast|reasoning` - switch task profile.
- `/index .` - index a workspace path.
- `/tool <name> <json>` - stage a tool call for approval.
- `/quit` - exit.

Tool calls typed through `/tool` are staged first. Press `Enter` to approve or `Esc` to reject.

## Local Models

The TUI uses the same backend as the rest of the project:

- model registry: `config/model_registry.toml`
- runtime profiles: `config/models.toml`
- inference logs: `.codeagents/inference.jsonl`
- audit logs: `.codeagents/audit.jsonl`

For Ollama, keep `ollama serve` running separately or use a system service.
