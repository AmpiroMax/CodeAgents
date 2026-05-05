# Model generation parameters

Per-model TOML configs that drive sampling for every chat call. Auto-created on
`ca serve` startup for any model in `ollama list`. Existing files are **never**
overwritten, so feel free to tune.

Layout:

```
config/model_params/<sanitized-name>.toml      # ":" -> "-",  "/" -> "_"
```

Reload: edits are read on every request — no restart needed.

## What each field means

| Field | Where it goes | Notes |
| --- | --- | --- |
| `temperature` | top-level | 0 = greedy, 1 = neutral, >1 = creative |
| `top_p` | top-level | nucleus sampling (0.8–0.95 typical) |
| `presence_penalty` | top-level | OpenAI-style: discourages topic repetition |
| `frequency_penalty` | top-level | OpenAI-style: discourages exact-token repetition |
| `seed` | top-level | non-zero → deterministic sampling |
| `stop` | top-level | list of stop strings |
| `num_predict` | top-level + options | `max_tokens`; -1 = until EOS |
| `top_k` | options | restrict sampling to top-K tokens |
| `min_p` | options | prune low-probability tail (Qwen3 recommends 0.0) |
| `repeat_penalty` | options | **best knob to fix loops**; 1.1 default, push to 1.2-1.3 |
| `repeat_last_n` | options | window for `repeat_penalty` |
| `num_ctx` | options | context window in tokens |

`top-level` fields go straight into the OpenAI-compatible payload. `options`
fields are wrapped in `{"options": {...}}` which Ollama forwards to its native
runtime.

## Where to find official recommended values

- **Hugging Face model card → `generation_config.json`**
  Most reliable source. e.g. <https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/blob/main/generation_config.json>
- **Ollama library** → click the model → **Parameters** tab.
  e.g. <https://ollama.com/library/gemma4>
- **Original repo README on GitHub** → "Recommended Sampling Parameters" or "Best Practices".
- **Unsloth docs** → curated recommended params per popular model:
  <https://docs.unsloth.ai/>
- **Qwen team posts** → "Advanced Generation Parameters" wiki pages on DeepWiki.

## Quick reference for the bundled models (May 2026)

| Model | temp | top_p | top_k | repeat_penalty | Source |
| --- | --- | --- | --- | --- | --- |
| `gpt-oss:20b` | 1.0 | 1.0 | 0 (off) | 1.1 | [openai/gpt-oss](https://github.com/openai/gpt-oss/blob/main/README.md) |
| `qwen2.5-coder:7b` | 0.7 | 0.8 | 20 | 1.1 | [HF generation_config.json](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/blob/main/generation_config.json) |
| `qwen3.6:27b-coding-nvfp4` | 0.6 | 0.95 | 20 | 1.0 | [Qwen3.6 model card (precise coding profile)](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) |
| `gemma4:31b` | 1.0 | 0.95 | 64 | 1.0 | [Google AI Gemma 4 card](https://ai.google.dev/gemma/docs/core/model_card_4) |

## Fighting loops

Order to try:

1. Bump `repeat_penalty` from 1.1 → 1.2 → 1.3 (don't go above 1.4).
2. Widen `repeat_last_n` to 256–512.
3. Add `presence_penalty = 0.5..1.5` (especially for Qwen3 family).
4. As a last resort, raise `temperature` slightly so sampling can escape the loop.

## CLI

```
ca params                    # list all per-model files
ca params gpt-oss:20b        # show one config
```
