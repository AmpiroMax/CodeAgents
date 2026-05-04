# Services Manager

`ca-services` is a separate terminal application for running and observing local inference services. It does not expose coding-agent features. Its job is to start services, show model inventory, monitor memory, and tail logs.

## Install

```bash
scripts/install.sh
```

This installs:

- `ca` - coding/chat terminal client.
- `ca-services` - service manager for inference runtime and API.

## Commands

Run as a long-lived background supervisor:

```bash
ca-services daemon
```

Start Ollama and the CodeAgents API in the background:

```bash
ca-services start
```

Show service ports, PIDs, and unified-memory RSS:

```bash
ca-services status
```

Stop both services:

```bash
ca-services stop
```

Restart both services:

```bash
ca-services restart
```

List installed Ollama models and registry models that can be installed quickly:

```bash
ca-services models
```

Install a registry model:

```bash
ca-services install qwen_code_fast
```

Send a simple chat request through `ca serve`:

```bash
ca-services chat "Привет, кто ты?" --task general
```

Tail logs:

```bash
ca-services logs --limit 50
```

## What It Shows

`ca-services models` shows:

- Ollama models directory, usually `~/.ollama/models` or `$OLLAMA_MODELS`.
- Total disk usage of the Ollama model store.
- Installed models from `ollama list`.
- Quick-install registry entries from `config/model_registry.toml`.

`ca-services status` shows:

- Whether Ollama listens on `:11434`.
- Whether CodeAgents API listens on `:8765`.
- Process RSS for both services. On Apple Silicon this is unified memory used by the processes; there is no separate user-visible VRAM pool for these local processes.
- A small `vm_stat` snapshot for system-level memory context.

## macOS App

Install `CodeAgents Services.app` directly into `/Applications` with an icon:

```bash
scripts/install_app.sh
```

This builds the `.app` bundle, generates `AppIcon.icns`, and copies it to `/Applications`. After install you can launch it from:

- Spotlight (`Cmd+Space`) → `CodeAgents Services`
- Launchpad search
- `open -a "CodeAgents Services"`
- Finder → `/Applications/CodeAgents Services.app`

If you prefer a DMG instead, run `scripts/package_dmg.sh` to get `dist/CodeAgents-Services.dmg`. The app opens a native macOS window focused on service operations and profiling.

Top-level actions:

- `Start Services`
- `Stop Services`
- `Restart Services`
- `Refresh`
- `Refresh Models`
- `Refresh Logs`

The window is split into tabs:

- `Overview` explains what each local service does, which port it owns, and where its logs live.
- `Models` shows installed Ollama models, quick-install registry models, model store path, and disk usage.
- `Profiling` shows process RSS for Ollama and CodeAgents API plus a short "who uses what" map.
- `Logs` groups API request logs, structured inference logs, runtime request logs, and service stdout/stderr logs.
- `Activity` shows commands triggered by the UI with timestamps.

When you close the app window or quit the app, it automatically runs `ca-services stop`. This stops `ollama serve`, `ca serve`, and any supervised service daemon, so loaded local models can release their memory.

The app keeps logs in:

```bash
.codeagents/services/app.log
.codeagents/services/daemon.log
```

You can also stop services from the repository:

```bash
ca-services --root /Users/ampiro/programs/CodeAgents stop
```

The generated app stores the project root path at build time. If you move the repository, rebuild the DMG with `scripts/package_dmg.sh`.

## Logs

The manager writes and tails:

```bash
.codeagents/services/ollama.log
.codeagents/services/ca-serve.log
.codeagents/service_requests.jsonl
.codeagents/runtime_requests.jsonl
.codeagents/inference.jsonl
```

`service_requests.jsonl` records HTTP requests handled by `ca serve`.

`runtime_requests.jsonl` records OpenAI-compatible requests sent from CodeAgents to the local runtime, including model name, payload, response/error, and elapsed time.

`inference.jsonl` records structured `/inference/chat` and `/inference/batch` calls in the Pydantic chat format.
