from __future__ import annotations

from codeagents.config import AppConfig, ModelProfile


class ModelRouter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def for_task(self, task: str | None) -> ModelProfile:
        if task in {None, "", "chat", "general"}:
            return self.config.model("general")
        if task in {"code", "coding", "edit"}:
            return self.config.model("code")
        if task in {"fast", "autocomplete", "small"}:
            return self.config.model("code_fast")
        if task in {"reason", "reasoning", "hard"}:
            return self.config.model("reasoning")
        if task in self.config.models:
            return self.config.model(task)
        # Direct Ollama model name (e.g. "qwen2.5:14b", "llama3:8b").
        # Build an ad-hoc profile so any installed model can be used.
        base = self.config.model(self.config.runtime.default_model)
        return ModelProfile(
            key=task,
            name=task,
            role=base.role,
            context_tokens=base.context_tokens,
            temperature=base.temperature,
            notes=f"Direct Ollama model: {task}",
        )
