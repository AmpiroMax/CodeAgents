# Sources for `system_prompts.json`

The per-model overrides in `system_prompts.json` are distilled from the
following references. They're intentionally short — the goal is to nudge
each model toward the behaviour its authors / community report works best
for code-agent workflows, not to replicate full system prompts verbatim.

When adding a new model:

1. Check the model card on Hugging Face / Ollama Library for any
   recommended system prompt or chat template the authors ship.
2. Skim the upstream repo's README for usage advice (Qwen-Coder repo,
   GPT-OSS repo, etc.).
3. Look at how the model is wired up in well-known agent frameworks
   (Continue, Aider, Cursor's docs, OpenCode) — they often publish
   model-specific system prompts.

Then add an entry under `models["<family>"]` with separate `agent` /
`plan` / `ask` strings.

## Reference shelf

- Qwen 3 / 3.6 family — <https://huggingface.co/Qwen/Qwen3.6-35B-A3B>,
  <https://github.com/QwenLM/Qwen3-Coder>
- GPT-OSS — <https://github.com/openai/gpt-oss>,
  <https://ollama.com/library/gpt-oss>
- Gemma 4 — <https://ai.google.dev/gemma/docs>,
  <https://huggingface.co/google/gemma-2-27b-it>
- Llama 3.1 — <https://github.com/meta-llama/llama-models>,
  <https://www.llama.com/docs/how-to-guides/prompting/>
- Mistral — <https://docs.mistral.ai/guides/prompting_capabilities/>
- Phi-3/4 — <https://huggingface.co/microsoft/Phi-3-medium-4k-instruct>
- DeepSeek Coder — <https://github.com/deepseek-ai/DeepSeek-Coder>
- IBM Granite — <https://www.ibm.com/granite>,
  <https://huggingface.co/ibm-granite>
- NVIDIA Nemotron — <https://huggingface.co/nvidia/Nemotron-3-8B-Chat-RLHF>
