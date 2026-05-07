"""Per-model generation parameters.

Each model (gpt-oss:20b, qwen3.6:27b-coding-nvfp4, ...) gets its own TOML file
under ``registry/model_params/`` (was ``config/model_params/`` prior to v3.0;
the legacy path is still accepted as a fallback so older user setups keep
working without a migration step).

Files are auto-created with safe defaults the first time a model is used.
Existing files are never overwritten.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeagents.core.config import PROJECT_ROOT

PARAMS_DIR = PROJECT_ROOT / "registry" / "model_params"
LEGACY_PARAMS_DIR = PROJECT_ROOT / "config" / "model_params"


@dataclass
class ModelParams:
    """Generation params passed to the runtime.

    OpenAI-compatible: temperature, top_p, presence_penalty, frequency_penalty,
    seed, stop, max_tokens.

    Ollama-specific (passed via the `options` block, supported by Ollama's
    OpenAI-compatible endpoint): top_k, repeat_penalty, repeat_last_n,
    num_ctx, num_predict, mirostat, mirostat_eta, mirostat_tau, tfs_z.
    """

    temperature: float = 0.2
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.0
    repeat_penalty: float = 1.1
    repeat_last_n: int = 64
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    num_ctx: int = 8192
    num_predict: int = -1
    seed: int = 0
    stop: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModelParams":
        gen = raw.get("generation", raw)
        params = cls()
        for key in cls.__dataclass_fields__:
            if key in gen:
                setattr(params, key, gen[key])
        return params

    def openai_payload(self) -> dict[str, Any]:
        """Build the JSON payload fragment to merge into a chat/completions request.

        Standard OpenAI fields go top-level; Ollama-specific fields go into `options`,
        which Ollama's OpenAI-compatible endpoint forwards to the native runtime.
        """
        payload: dict[str, Any] = {
            "temperature": self.temperature,
            "top_p": self.top_p,
        }
        if self.presence_penalty:
            payload["presence_penalty"] = self.presence_penalty
        if self.frequency_penalty:
            payload["frequency_penalty"] = self.frequency_penalty
        if self.seed:
            payload["seed"] = self.seed
        if self.stop:
            payload["stop"] = self.stop
        if self.num_predict and self.num_predict > 0:
            payload["max_tokens"] = self.num_predict

        options: dict[str, Any] = {
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "repeat_last_n": self.repeat_last_n,
            "num_ctx": self.num_ctx,
        }
        if self.num_predict:
            options["num_predict"] = self.num_predict
        payload["options"] = options
        return payload


def _sanitize(model_name: str) -> str:
    """Convert e.g. 'gpt-oss:20b' to 'gpt-oss-20b' for safe filenames."""
    return model_name.replace(":", "-").replace("/", "_")


def params_path(model_name: str) -> Path:
    """Resolve the TOML for ``model_name``. Falls back to the legacy
    ``config/model_params/`` location if the file lives there from an older
    install, so users don't lose hand-tuned overrides on upgrade."""
    fname = f"{_sanitize(model_name)}.toml"
    new_path = PARAMS_DIR / fname
    if new_path.exists():
        return new_path
    legacy = LEGACY_PARAMS_DIR / fname
    if legacy.exists():
        return legacy
    return new_path


_DEFAULT_TEMPLATE = """\
# Generation parameters for {model}.
# Auto-generated with conservative defaults; edit freely. Never overwritten.
#
# Where to find recommended values for any model:
#   1. Hugging Face model card -> "Sampling Parameters" / generation_config.json
#      e.g. https://huggingface.co/<org>/<model>/blob/main/generation_config.json
#   2. Ollama library page -> Parameters tab, e.g. https://ollama.com/library/<model>
#   3. Original repo README on GitHub
#   4. https://docs.unsloth.ai/  (collects recommended params for popular models)
#
# Standard fields (passed directly to OpenAI-compatible API):
#   temperature      0.0..2.0   randomness; lower = more deterministic
#   top_p            0.0..1.0   nucleus sampling
#   presence_penalty -2.0..2.0  discourage repetition by topic
#   frequency_penalty -2.0..2.0 discourage exact-token repetition
#   seed                        deterministic sampling when non-zero
#   stop                        list of stop strings
#
# Ollama-only (forwarded via `options`):
#   top_k                       restrict sampling to top-K tokens
#   min_p            0.0..1.0   prune low-probability tail (Qwen3 recommends 0.0)
#   repeat_penalty   1.0..2.0   penalize repeated tokens (1.1 = Ollama default)
#   repeat_last_n               window of tokens checked by repeat_penalty
#   num_ctx                     context window in tokens
#   num_predict      -1=∞       max tokens to generate (-1 = until EOS)

[generation]
temperature = {temperature}
top_p = 0.9
top_k = 40
min_p = 0.0
repeat_penalty = 1.1
repeat_last_n = 256
presence_penalty = 0.0
frequency_penalty = 0.0
num_ctx = {num_ctx}
num_predict = -1
seed = 0
stop = []
"""


def _default_num_ctx_for(model_name: str, fallback: int) -> int:
    """Pick a sensible ``num_ctx`` for newly-created TOML files based on
    the model name. Uses the central context-window table from
    :mod:`codeagents.token_counter` so we don't duplicate the catalogue.
    """

    try:
        from codeagents.core.budget.token_counter import DEFAULT_CONTEXT_WINDOWS

        m = (model_name or "").lower()
        if m in DEFAULT_CONTEXT_WINDOWS:
            return DEFAULT_CONTEXT_WINDOWS[m]
        for key, ctx in DEFAULT_CONTEXT_WINDOWS.items():
            if key != "default" and m.startswith(key):
                return ctx
    except Exception:
        pass
    return fallback


def ensure_params_file(model_name: str, *, temperature: float = 0.2, num_ctx: int | None = None) -> Path:
    """Create a default params file for `model_name` if it doesn't exist. Never overwrites.

    When ``num_ctx`` isn't provided, falls back to the family-aware
    default from :mod:`codeagents.token_counter` (e.g. gemma3 -> 131072,
    qwen3-coder -> 262144) so freshly-pulled models don't get capped at
    the legacy 8k.
    """
    PARAMS_DIR.mkdir(parents=True, exist_ok=True)
    path = params_path(model_name)
    if not path.exists():
        if num_ctx is None:
            num_ctx = _default_num_ctx_for(model_name, fallback=8192)
        path.write_text(
            _DEFAULT_TEMPLATE.format(model=model_name, temperature=temperature, num_ctx=num_ctx),
            encoding="utf-8",
        )
    return path


def load_params(model_name: str, *, default_temperature: float = 0.2) -> ModelParams:
    """Load params from the per-model TOML; create default file if missing."""
    path = ensure_params_file(model_name, temperature=default_temperature)
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        return ModelParams.from_dict(raw)
    except Exception:
        # If the file is malformed, fall back to defaults rather than crash the stream.
        return ModelParams(temperature=default_temperature)


def list_param_files() -> list[Path]:
    seen: dict[str, Path] = {}
    for base in (PARAMS_DIR, LEGACY_PARAMS_DIR):
        if not base.exists():
            continue
        for path in base.glob("*.toml"):
            seen.setdefault(path.name, path)
    return [p for _, p in sorted(seen.items())]


def ensure_for_models(model_names: list[str]) -> list[Path]:
    """Pre-create param files for every supplied model name.

    Also performs a one-shot migration: when an existing TOML still
    carries the legacy ``num_ctx = 8192`` (the old default for ALL
    families) but the model is actually known to support more, the
    file is rewritten with the family-aware default. User-customised
    values are never touched — only the exact legacy default gets
    upgraded, and a comment is appended explaining the bump.
    """
    paths: list[Path] = []
    for name in model_names:
        path = ensure_params_file(name)
        try:
            _migrate_legacy_num_ctx(name, path)
        except Exception:
            # Migration is best-effort — never break startup.
            pass
        paths.append(path)
    return paths


def _migrate_legacy_num_ctx(model_name: str, path: Path) -> None:
    """Bump ``num_ctx`` from the legacy 8192 default to the family
    default if (and only if) the file still contains the legacy value.
    """

    if not path.exists():
        return
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except Exception:
        return
    gen = raw.get("generation", raw) if isinstance(raw, dict) else {}
    current = gen.get("num_ctx") if isinstance(gen, dict) else None
    if current != 8192:
        return  # custom value or already migrated
    target = _default_num_ctx_for(model_name, fallback=8192)
    if target <= 8192:
        return
    text = path.read_text(encoding="utf-8")
    new_text = text.replace(
        f"num_ctx = 8192",
        f"num_ctx = {target}  # auto-bumped from 8192 to family default",
        1,
    )
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")


__all__ = [
    "ModelParams",
    "PARAMS_DIR",
    "ensure_params_file",
    "ensure_for_models",
    "list_param_files",
    "load_params",
    "params_path",
]
