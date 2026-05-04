# Local Runtime Setup

The agent core talks to local models through an OpenAI-compatible API.

Default endpoint:

```bash
http://localhost:11434/v1
```

This matches Ollama's OpenAI-compatible endpoint. llama.cpp server and MLX wrappers can also be used if they expose compatible `/v1/chat/completions` and `/v1/models` routes.

## Ollama MVP

Start Ollama:

```bash
ollama serve
```

Pull candidate models:

```bash
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5:14b
ollama pull deepseek-r1:14b
```

Then update `config/models.toml` so model profile names match the ids returned by:

```bash
PYTHONPATH=src python3 -m codeagents.cli runtime
```

## llama.cpp Server

Example shape:

```bash
llama-server \
  --model /path/to/model.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 32768 \
  --jinja
```

Then set:

```toml
[runtime]
base_url = "http://localhost:8080/v1"
```

## MLX

Use MLX when a model has good MLX quantization and the server wrapper exposes an OpenAI-compatible API. Keep the agent-side config unchanged except for `base_url` and model ids.

## Runtime Commands

```bash
PYTHONPATH=src python3 -m codeagents.cli runtime
PYTHONPATH=src python3 -m codeagents.cli ask "Привет"
PYTHONPATH=src python3 -m codeagents.cli benchmark --models general code_fast
```

## Model Profile Rule

Model profile keys are stable agent names: `general`, `code`, `code_fast`, `reasoning`.

The `name` field is the actual runtime model id and can change per machine.
