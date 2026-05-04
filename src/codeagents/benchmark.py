from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from codeagents.config import AppConfig
from codeagents.runtime import OpenAICompatibleRuntime, RuntimeErrorWithHint
from codeagents.schemas import Chat


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    prompt: str
    expected_traits: list[str]


@dataclass(frozen=True)
class BenchmarkResult:
    eval_id: str
    model_profile: str
    model_name: str
    ok: bool
    elapsed_seconds: float
    chars_per_second: float
    response_chars: int
    error: str | None
    response_preview: str


def load_eval_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            cases.append(
                EvalCase(
                    id=raw["id"],
                    category=raw["category"],
                    prompt=raw["prompt"],
                    expected_traits=list(raw.get("expected_traits", [])),
                )
            )
    return cases


def run_benchmark(
    *,
    config: AppConfig,
    eval_cases: list[EvalCase],
    model_keys: list[str],
) -> list[BenchmarkResult]:
    runtime = OpenAICompatibleRuntime(config.runtime)
    results: list[BenchmarkResult] = []

    for model_key in model_keys:
        model = config.model(model_key)
        for case in eval_cases:
            chat = Chat.from_prompt(
                case.prompt,
                system=(
                    "You are evaluating a local agent model. Answer directly and "
                    "use Russian when the prompt is in Russian."
                ),
                meta={"eval_id": case.id, "category": case.category},
            )
            try:
                result = runtime.chat_with_metrics(model=model, chat=chat)
                response_chars = len(result.content)
                chars_per_second = response_chars / max(result.elapsed_seconds, 0.001)
                results.append(
                    BenchmarkResult(
                        eval_id=case.id,
                        model_profile=model_key,
                        model_name=model.name,
                        ok=True,
                        elapsed_seconds=result.elapsed_seconds,
                        chars_per_second=chars_per_second,
                        response_chars=response_chars,
                        error=None,
                        response_preview=result.content[:500],
                    )
                )
            except RuntimeErrorWithHint as exc:
                results.append(
                    BenchmarkResult(
                        eval_id=case.id,
                        model_profile=model_key,
                        model_name=model.name,
                        ok=False,
                        elapsed_seconds=0.0,
                        chars_per_second=0.0,
                        response_chars=0,
                        error=str(exc),
                        response_preview="",
                    )
                )
    return results


def write_benchmark_results(root: Path, results: list[BenchmarkResult]) -> Path:
    output_dir = root / ".codeagents" / "benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"benchmark-{stamp}.jsonl"
    with output_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
    return output_path
