"""Token budget estimation with EMA-calibration against ollama's prompt_eval_count.

Phase 2.A.1.

Pre-hoc token estimation is required for two upstream tasks:
  1. per-mode tool filtering (drop optional tools when the spec set is large);
  2. summarisation cap (compress old messages once estimated > 0.85 * ctx_window).

Strategy
--------
- Base estimate uses ``tiktoken``'s ``cl100k_base`` encoder. It is reasonably
  accurate (±10%) across modern open-source tokenizers (Llama-3, Qwen, Mistral,
  DeepSeek). When tiktoken is missing we fall back to a chars/3.5 heuristic.
- Each ollama turn returns ``prompt_eval_count`` (the *real* prompt size in
  the model's tokenizer). After every turn we update an EMA factor per model
  so the heuristic self-corrects over time.
- The factors are persisted to ``.codeagents/token_calibration.json`` so they
  survive restarts. The file is shared across workspaces (calibration is a
  property of the *model*, not the project).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# tiktoken is a hard dep in pyproject; guard against import errors so unit
# tests that skip it (e.g. minimal CI) still pass.
try:
    import tiktoken as _tiktoken
except Exception:  # pragma: no cover - environment-specific
    _tiktoken = None  # type: ignore[assignment]


# ── Configuration constants ───────────────────────────────────────────

# Per-model context windows (in tokens). Source: model cards / ollama docs.
# Used as a fallback when the runtime does not report ``context_window``.
# Fallback table — used only when no per-model TOML is present.
# Keys are matched case-insensitively, exactly first then by prefix
# (e.g. "qwen3:30b-instruct" → "qwen3:30b" → "qwen3"). All values are
# pulled from official model cards / Ollama library Parameters tab.
DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    # ── Qwen ────────────────────────────────────────────────────────
    "qwen3-coder": 262144,   # Qwen3-Coder, native 256k (extendable to 1M).
    "qwen3:30b": 262144,
    "qwen3:14b": 131072,
    "qwen3:8b": 131072,
    "qwen3": 131072,         # Qwen3 chat models default to 128k.
    "qwen2.5-coder": 32768,
    "qwen2.5": 131072,       # Qwen2.5 instruct supports 128k via YaRN.
    "qwen2": 32768,
    # ── DeepSeek ────────────────────────────────────────────────────
    "deepseek-coder-v2": 163840,
    "deepseek-coder": 16384,
    "deepseek-v3": 131072,
    "deepseek-v2": 131072,
    "deepseek-r1": 131072,
    # ── Llama ───────────────────────────────────────────────────────
    "llama3.3": 131072,
    "llama3.2": 131072,
    "llama3.1": 131072,
    "llama3": 8192,
    "llama2": 4096,
    # ── Mistral / Mixtral ───────────────────────────────────────────
    "mistral-nemo": 131072,
    "mistral-small": 131072,
    "mistral-large": 131072,
    "mixtral": 32768,
    "mistral": 32768,
    # ── Phi ─────────────────────────────────────────────────────────
    "phi4": 16384,
    "phi3.5": 131072,
    "phi3": 131072,
    "phi": 4096,
    # ── Gemma (Google) ──────────────────────────────────────────────
    # Gemma 3 / 4 (1B–27B): 128k native context per model card.
    # ``embeddinggemma`` is the embedding-only build with a 2k window.
    "gemma4": 131072,
    "gemma3": 131072,
    "gemma2": 8192,
    "gemma": 8192,
    "embeddinggemma": 2048,
    # ── GPT-OSS (OpenAI) ────────────────────────────────────────────
    "gpt-oss:120b": 131072,
    "gpt-oss:20b": 131072,
    "gpt-oss": 131072,
    # ── NVIDIA Nemotron ─────────────────────────────────────────────
    "nemotron-mini": 4096,
    "nemotron-h": 131072,
    "nemotron3": 131072,
    "nemotron": 131072,
    # ── IBM Granite ─────────────────────────────────────────────────
    "granite4": 131072,
    "granite3.3": 131072,
    "granite3.2": 131072,
    "granite-code": 131072,
    "granite": 8192,
    # ── Cohere Command-R / Aya ──────────────────────────────────────
    "command-r-plus": 131072,
    "command-r": 131072,
    "aya": 32768,
    # ── Yi (01.AI) ──────────────────────────────────────────────────
    "yi-coder": 131072,
    "yi": 32768,
    # ── Other code-tuned models ─────────────────────────────────────
    "codestral": 32768,
    "codellama": 16384,
    "starcoder2": 16384,
    "starcoder": 8192,
    "default": 8192,
}

EMA_ALPHA = 0.3  # weight of the new sample in EMA (0.7 history + 0.3 new).
MIN_CALIBRATION_SAMPLES = 3  # below this the factor stays at 1.0.

# Default chars-per-token when tiktoken is unavailable. Roughly matches
# ``cl100k_base`` for English; Russian/CJK lean closer to 2.5 — the EMA
# calibration narrows the gap automatically after the first turn.
FALLBACK_CHARS_PER_TOKEN = 3.5

# Per-message overhead introduced by chat-format markers (role, separator
# tokens). Mirrors OpenAI's "every message costs ~4 tokens of envelope".
PER_MESSAGE_OVERHEAD = 4

# Per-tool overhead for the JSON schema framing (``{"type":"function","function":{...}}``).
PER_TOOL_OVERHEAD = 8


# ── Calibration store ─────────────────────────────────────────────────


@dataclass
class _ModelCalibration:
    """Per-model calibration state."""

    factor: float = 1.0
    samples: int = 0
    last_real: int = 0  # last reported prompt_eval_count
    last_estimate: int = 0  # what we estimated for that turn

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor": round(self.factor, 4),
            "samples": self.samples,
            "last_real": self.last_real,
            "last_estimate": self.last_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_ModelCalibration":
        return cls(
            factor=float(data.get("factor", 1.0)),
            samples=int(data.get("samples", 0)),
            last_real=int(data.get("last_real", 0)),
            last_estimate=int(data.get("last_estimate", 0)),
        )


@dataclass
class TokenBudget:
    """Token budget estimator with self-calibration.

    Use one instance per workspace (or per process if calibration should be
    shared). ``estimate(messages, tools)`` is cheap; ``record(...)`` should
    be called once per turn after the runtime reports ``prompt_eval_count``.
    """

    storage_path: Path | None = None
    _calibrations: dict[str, _ModelCalibration] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _encoder: Any | None = None

    # ── Public API ────────────────────────────────────────────────────

    @classmethod
    def for_workspace(cls, workspace_root: Path | str) -> "TokenBudget":
        path = Path(workspace_root) / ".codeagents" / "token_calibration.json"
        budget = cls(storage_path=path)
        budget._load()
        return budget

    def context_window(self, model: str) -> int:
        """Return the configured context window for a model name.

        Resolution order:
          1. Per-model TOML at ``registry/model_params/<model>.toml``
             (``[generation].num_ctx``). This is authoritative because
             it is exactly the value the runtime sends to ollama.
          2. The ``DEFAULT_CONTEXT_WINDOWS`` table (exact / prefix
             match) — kept as a fallback for ad-hoc usage where no
             TOML has been generated yet.
          3. ``DEFAULT_CONTEXT_WINDOWS["default"]`` (8k).
        """
        if not model:
            return DEFAULT_CONTEXT_WINDOWS["default"]
        # 1) per-model TOML (single source of truth for num_ctx).
        try:
            from codeagents.model_params import params_path

            path = params_path(model)
            if path.exists():
                import tomllib

                with path.open("rb") as fh:
                    raw = tomllib.load(fh)
                gen = raw.get("generation", raw) if isinstance(raw, dict) else {}
                if isinstance(gen, dict):
                    n = gen.get("num_ctx")
                    if isinstance(n, int) and n > 0:
                        return n
        except Exception:
            # Fall through to the static table on any malformed TOML.
            pass

        m = model.lower()
        if m in DEFAULT_CONTEXT_WINDOWS:
            return DEFAULT_CONTEXT_WINDOWS[m]
        # Prefix match (e.g. "qwen3:30b-instruct" → "qwen3:30b").
        for key, ctx in DEFAULT_CONTEXT_WINDOWS.items():
            if key != "default" and m.startswith(key):
                return ctx
        return DEFAULT_CONTEXT_WINDOWS["default"]

    def estimate(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        extra_text: str = "",
    ) -> int:
        """Estimate prompt_tokens for the next call.

        ``messages`` and ``tools`` accept the same shape as OpenAI's chat
        completions API (``[{role,content}]`` and ``[{type,function:{...}}]``).
        """
        raw = 0
        for msg in messages or []:
            raw += PER_MESSAGE_OVERHEAD
            raw += self._count_tokens(self._stringify_message(msg))
        for tool in tools or []:
            raw += PER_TOOL_OVERHEAD
            raw += self._count_tokens(self._stringify_tool(tool))
        if extra_text:
            raw += self._count_tokens(extra_text)
        factor = self._factor(model)
        return max(0, int(raw * factor))

    def record(self, *, model: str, predicted: int, actual: int) -> None:
        """Update the EMA factor for ``model`` from one observed turn.

        ``predicted`` is what ``estimate(...)`` returned; ``actual`` is the
        ``prompt_eval_count`` reported by the runtime. We compute the
        correction factor ``actual/predicted`` and exponentially merge it
        into the stored factor.
        """
        if predicted <= 0 or actual <= 0:
            return
        with self._lock:
            cal = self._calibrations.setdefault(model, _ModelCalibration())
            sample_factor = actual / predicted
            # Bound the per-sample correction to avoid one outlier (e.g. a
            # truncated stream) wrecking the EMA.
            sample_factor = max(0.3, min(3.0, sample_factor))
            if cal.samples == 0:
                cal.factor = sample_factor
            else:
                cal.factor = (1 - EMA_ALPHA) * cal.factor + EMA_ALPHA * sample_factor
            cal.samples += 1
            cal.last_real = int(actual)
            cal.last_estimate = int(predicted)
            self._save()

    def calibration(self, model: str) -> dict[str, Any]:
        """Inspect the current calibration entry for ``model`` (debug/UX)."""
        cal = self._calibrations.get(model)
        if cal is None:
            return {"factor": 1.0, "samples": 0, "last_real": 0, "last_estimate": 0}
        return cal.to_dict()

    # ── Internals ─────────────────────────────────────────────────────

    def _factor(self, model: str) -> float:
        cal = self._calibrations.get(model)
        if cal is None or cal.samples < MIN_CALIBRATION_SAMPLES:
            return 1.0
        return cal.factor

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if _tiktoken is not None:
            if self._encoder is None:
                try:
                    self._encoder = _tiktoken.get_encoding("cl100k_base")
                except Exception:  # pragma: no cover
                    self._encoder = None
            if self._encoder is not None:
                try:
                    return len(self._encoder.encode(text, disallowed_special=()))
                except Exception:  # pragma: no cover - defensive
                    pass
        # Fallback: char-based heuristic.
        return max(1, int(len(text) / FALLBACK_CHARS_PER_TOKEN))

    @staticmethod
    def _stringify_message(msg: dict[str, Any]) -> str:
        parts: list[str] = []
        role = msg.get("role")
        if isinstance(role, str):
            parts.append(role)
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for chunk in content:
                if isinstance(chunk, dict):
                    parts.append(json.dumps(chunk, ensure_ascii=False))
                elif isinstance(chunk, str):
                    parts.append(chunk)
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                parts.append(json.dumps(tc, ensure_ascii=False))
        if msg.get("name"):
            parts.append(str(msg["name"]))
        if msg.get("tool_call_id"):
            parts.append(str(msg["tool_call_id"]))
        return "\n".join(parts)

    @staticmethod
    def _stringify_tool(tool: dict[str, Any]) -> str:
        return json.dumps(tool, ensure_ascii=False)

    # ── Persistence ───────────────────────────────────────────────────

    def _load(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        models = data.get("models")
        if not isinstance(models, dict):
            return
        for model, raw in models.items():
            if isinstance(raw, dict):
                self._calibrations[str(model)] = _ModelCalibration.from_dict(raw)

    def _save(self) -> None:
        if self.storage_path is None:
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "models": {m: cal.to_dict() for m, cal in self._calibrations.items()},
            }
            tmp = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.storage_path)
        except Exception:  # pragma: no cover - best-effort persistence
            pass
