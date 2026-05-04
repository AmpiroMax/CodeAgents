# Adding Models

В Ollama “руками” модели добавляются через `Modelfile`. Самый удобный сценарий: скачать `.gguf` с Hugging Face, создать `Modelfile`, затем `ollama create`.

## 1. Скачать GGUF

Например:

```bash
mkdir -p ~/models/qwen
cd ~/models/qwen

huggingface-cli download Qwen/Qwen2.5-Coder-3B-Instruct-GGUF \
  qwen2.5-coder-3b-instruct-q4_k_m.gguf \
  --local-dir . \
  --local-dir-use-symlinks False
```

Если нет `huggingface-cli`:

```bash
.venv/bin/python -m pip install huggingface_hub
```

## 2. Создать Modelfile

```bash
cat > Modelfile <<'EOF'
FROM ./qwen2.5-coder-3b-instruct-q4_k_m.gguf

PARAMETER temperature 0.2
PARAMETER num_ctx 32768

SYSTEM """
You are a helpful coding assistant.
"""
EOF
```

## 3. Зарегистрировать в Ollama

```bash
ollama create qwen-coder-local -f Modelfile
```

Проверить:

```bash
ollama list
ollama run qwen-coder-local
```

## 4. Использовать в проекте

В `config/model_registry.toml` добавить:

```toml
[models.qwen_coder_local]
display_name = "Qwen Coder Local"
backend = "ollama"
runtime_model = "qwen-coder-local"
profile = "code_fast"
weights_path = "/Users/ampiro/models/qwen/qwen2.5-coder-3b-instruct-q4_k_m.gguf"
source = "hf:Qwen/Qwen2.5-Coder-3B-Instruct-GGUF"
notes = "Local GGUF model registered in Ollama."
```

Потом:

```bash
.venv/bin/python -m codeagents.cli model-registry
```

## Важно

Ollama не ест любые HF safetensors напрямую. Для простого пути ищи именно **GGUF**. Запросы на HF: `model name GGUF`.

Для MacBook M3 Max 36 GB:

- `gemma` - ищи `Gemma 3 12B/27B GGUF`, лучше Q4/Q5.
- `deepseek-v4` - если это большая MoE, скорее всего локально не влезет. Ищи distilled/малые GGUF.
- `Qwen3-Coder-30B-A3B` - можно пробовать Q4, но это уже около 17-22 GB и может быть тяжеловато, зато по коду интересно.

Если модель непонятна по имени, лучше сохранить HF-ссылку или точный model id и отдельно проверить, есть ли для нее GGUF-квантизация.
