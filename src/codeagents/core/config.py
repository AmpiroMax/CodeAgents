from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    api_key: str | None
    default_model: str
    embedding_model: str | None = None


@dataclass(frozen=True)
class ModelProfile:
    key: str
    name: str
    role: str
    context_tokens: int
    temperature: float
    notes: str = ""


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    models: dict[str, ModelProfile]

    def model(self, key: str | None = None) -> ModelProfile:
        selected = key or self.runtime.default_model
        try:
            return self.models[selected]
        except KeyError as exc:
            available = ", ".join(sorted(self.models))
            raise ValueError(f"Unknown model profile '{selected}'. Available: {available}") from exc


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_app_config(path: Path | None = None) -> AppConfig:
    config_path = path or PROJECT_ROOT / "config" / "models.toml"
    raw = load_toml(config_path)

    runtime_raw = raw.get("runtime", {})
    api_key_env = runtime_raw.get("api_key_env", "CODEAGENTS_API_KEY")
    runtime = RuntimeConfig(
        base_url=runtime_raw.get("base_url", "http://localhost:11434/v1"),
        api_key=os.getenv(api_key_env),
        default_model=runtime_raw.get("default_model", "general"),
        embedding_model=runtime_raw.get("embedding_model"),
    )

    models: dict[str, ModelProfile] = {}
    for key, value in raw.get("models", {}).items():
        models[key] = ModelProfile(
            key=key,
            name=value["name"],
            role=value.get("role", key),
            context_tokens=int(value.get("context_tokens", 8192)),
            temperature=float(value.get("temperature", 0.2)),
            notes=value.get("notes", ""),
        )

    if not models:
        raise ValueError(f"No model profiles configured in {config_path}")

    return AppConfig(runtime=runtime, models=models)
