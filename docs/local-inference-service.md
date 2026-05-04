# Local Inference Service

This project uses one codebase for agents and direct local inference. The HTTP API and CLI both use the same Pydantic chat format from `codeagents.schemas`.

## Chat Format

All structured inference requests use:

- `Chat`
- `SystemMessage`, `UserMessage`, `AssistantMessage`, `FunctionMessage`
- typed content blocks such as `TextContent`, `FunctionCallContent`, `FunctionContent`, `FileContent`
- `InferenceRequest` and `BatchInferenceRequest`

Example request:

```json
{
  "model": "qwen_general",
  "chat": {
    "messages": [
      {
        "role": "system",
        "index": 0,
        "content": [{"type": "text", "text": "Отвечай кратко"}]
      },
      {
        "role": "user",
        "index": 1,
        "content": [{"type": "text", "text": "Оцени лог агента"}]
      }
    ],
    "meta": {"task_id": "judge-001"}
  }
}
```

## CLI

List registered models:

```bash
.venv/bin/python -m codeagents.cli model-registry
```

Register a model after downloading weights:

```bash
.venv/bin/python -m codeagents.cli model-register my_model \
  --backend llama_cpp \
  --runtime-model my-model-runtime-id \
  --profile reasoning \
  --weights-path /models/my-model.gguf \
  --source https://example.com/my-model.gguf
```

Download through a configured registry source:

```bash
.venv/bin/python -m codeagents.cli model-download qwen_general
```

Start a backend wrapper:

```bash
.venv/bin/python -m codeagents.cli model-start qwen_general
```

Run one inference request:

```bash
.venv/bin/python -m codeagents.cli infer request.json
```

Run batch inference:

```bash
.venv/bin/python -m codeagents.cli infer-batch batch.json
```

## HTTP API With requests

Start the service:

```bash
.venv/bin/python -m codeagents.cli serve --port 8765
```

Use `requests` from another script:

```python
import requests

payload = {
    "model": "qwen_general",
    "chat": {
        "messages": [
            {
                "role": "user",
                "index": 0,
                "content": [{"type": "text", "text": "Привет"}],
            }
        ],
        "meta": {"source": "autojudge"},
    },
}

response = requests.post("http://127.0.0.1:8765/inference/chat", json=payload, timeout=120)
print(response.json())
```

Batch endpoint:

```python
requests.post(
    "http://127.0.0.1:8765/inference/batch",
    json={"requests": [payload]},
    timeout=600,
)
```

## Logging

Every direct structured inference call is written to:

```bash
.codeagents/inference.jsonl
```

This log stores the original structured request, the structured response, errors, timestamps, and metadata. It is intended as input for a future local autojudge.

Service-level requests handled by `ca serve` are written to:

```bash
.codeagents/service_requests.jsonl
```

Runtime requests sent from CodeAgents to the OpenAI-compatible local runtime are written to:

```bash
.codeagents/runtime_requests.jsonl
```

Use `ca-services logs` to inspect all service and inference logs together.
