from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlretrieve

from codeagents.config import PROJECT_ROOT, ModelProfile, RuntimeConfig, load_app_config
from codeagents.inference_log import InferenceLogger
from codeagents.runtime import OpenAICompatibleRuntime
from codeagents.schemas import (
    BatchInferenceRequest,
    BatchInferenceResponse,
    InferenceRequest,
    InferenceResponse,
)


@dataclass(frozen=True)
class RegisteredModel:
    key: str
    display_name: str
    backend: str
    runtime_model: str
    profile: str
    weights_path: str = ""
    source: str = ""
    notes: str = ""

    @classmethod
    def from_raw(cls, key: str, raw: dict[str, Any]) -> "RegisteredModel":
        return cls(
            key=key,
            display_name=raw.get("display_name", key),
            backend=raw.get("backend", "ollama"),
            runtime_model=raw["runtime_model"],
            profile=raw.get("profile", key),
            weights_path=raw.get("weights_path", ""),
            source=raw.get("source", ""),
            notes=raw.get("notes", ""),
        )


class ModelRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PROJECT_ROOT / "config" / "model_registry.toml"
        self.models = self._load()

    def list(self) -> list[RegisteredModel]:
        return sorted(self.models.values(), key=lambda item: item.key)

    def get(self, key: str) -> RegisteredModel:
        try:
            return self.models[key]
        except KeyError as exc:
            available = ", ".join(sorted(self.models))
            raise ValueError(f"Unknown registered model '{key}'. Available: {available}") from exc

    def register(self, model: RegisteredModel) -> None:
        if model.key in self.models:
            raise ValueError(f"Model already exists in registry: {model.key}")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
            handle.write(f"[models.{_toml_string(model.key, bare=True)}]\n")
            handle.write(f"display_name = {_toml_string(model.display_name)}\n")
            handle.write(f"backend = {_toml_string(model.backend)}\n")
            handle.write(f"runtime_model = {_toml_string(model.runtime_model)}\n")
            handle.write(f"profile = {_toml_string(model.profile)}\n")
            handle.write(f"weights_path = {_toml_string(model.weights_path)}\n")
            handle.write(f"source = {_toml_string(model.source)}\n")
            handle.write(f"notes = {_toml_string(model.notes)}\n")
        self.models = self._load()

    def _load(self) -> dict[str, RegisteredModel]:
        with self.path.open("rb") as handle:
            raw = tomllib.load(handle)
        return {
            key: RegisteredModel.from_raw(key, value)
            for key, value in raw.get("models", {}).items()
        }


class LocalModelService:
    def __init__(
        self,
        *,
        registry: ModelRegistry | None = None,
        runtime_config: RuntimeConfig | None = None,
    ) -> None:
        self.registry = registry or ModelRegistry()
        app_config = load_app_config()
        self.runtime_config = runtime_config or app_config.runtime
        self.runtime = OpenAICompatibleRuntime(self.runtime_config)
        self.logger = InferenceLogger()

    def list_models(self) -> list[RegisteredModel]:
        return self.registry.list()

    def register_model(self, model: RegisteredModel) -> RegisteredModel:
        self.registry.register(model)
        return self.registry.get(model.key)

    def start(self, model_key: str) -> dict[str, Any]:
        model = self.registry.get(model_key)
        if model.backend == "ollama":
            command = ["ollama", "serve"]
            return _spawn_if_available(command)
        if model.backend == "llama_cpp":
            if not model.weights_path:
                raise ValueError("llama_cpp models require weights_path")
            command = ["llama-server", "--model", model.weights_path, "--port", "8080"]
            return _spawn_if_available(command)
        if model.backend == "mlx":
            command = ["python3", "-m", "mlx_lm.server"]
            return _spawn_if_available(command)
        raise ValueError(f"Unsupported backend: {model.backend}")

    def download(self, model_key: str, *, output_dir: Path | None = None) -> dict[str, Any]:
        model = self.registry.get(model_key)
        if model.source.startswith("ollama:"):
            return _run(["ollama", "pull", model.source.removeprefix("ollama:")])
        if not model.source:
            raise ValueError(f"Model '{model_key}' has no source configured")
        target_dir = output_dir or PROJECT_ROOT / ".codeagents" / "weights"
        target_dir.mkdir(parents=True, exist_ok=True)
        parsed = urlparse(model.source)
        filename = Path(parsed.path).name or f"{model.key}.weights"
        target = target_dir / filename
        urlretrieve(model.source, target)
        return {"exit_code": 0, "path": str(target)}

    def infer(self, request: InferenceRequest) -> InferenceResponse:
        model = self._profile_for(request.model)
        try:
            response = self.runtime.infer(
                model=model,
                chat=request.chat,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        except Exception as exc:
            self.logger.record_error(request=request, error=str(exc))
            raise
        self.logger.record_success(request=request, response=response)
        return response

    def batch(self, request: BatchInferenceRequest) -> BatchInferenceResponse:
        return BatchInferenceResponse(responses=[self.infer(item) for item in request.requests])

    def _profile_for(self, requested: str | None) -> ModelProfile:
        app_config = load_app_config()
        if requested is None:
            return app_config.model(None)
        if requested in app_config.models:
            return app_config.model(requested)
        registered = self.registry.get(requested)
        base = app_config.model(registered.profile if registered.profile in app_config.models else None)
        return ModelProfile(
            key=registered.key,
            name=registered.runtime_model,
            role=base.role,
            context_tokens=base.context_tokens,
            temperature=base.temperature,
            notes=registered.notes,
        )


def _spawn_if_available(command: list[str]) -> dict[str, Any]:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {
            "started": False,
            "command": command,
            "error": f"Executable not found: {command[0]}",
        }
    return {"started": True, "pid": process.pid, "command": command}


def _run(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=3600)
    except FileNotFoundError:
        return {
            "exit_code": 127,
            "stdout": "",
            "stderr": f"Executable not found: {command[0]}",
        }
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _toml_string(value: str, *, bare: bool = False) -> str:
    if bare:
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Model key is not safe for a TOML bare key: {value}")
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
