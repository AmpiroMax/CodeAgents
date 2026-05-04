from __future__ import annotations

import argparse
import json
from pathlib import Path

from codeagents.agent import AgentCore
from codeagents.audit import AuditLog
from codeagents.benchmark import load_eval_cases, run_benchmark, write_benchmark_results
from codeagents.config import PROJECT_ROOT, load_app_config
from codeagents.indexer import build_index, index_summary, search_index
from codeagents.inference_log import InferenceLogger
from codeagents.model_service import LocalModelService, RegisteredModel
from codeagents.runtime import OpenAICompatibleRuntime, RuntimeErrorWithHint
from codeagents.schemas import BatchInferenceRequest, Chat, InferenceRequest
from codeagents.server import serve
from codeagents.tools import load_tool_registry


def main() -> None:
    parser = argparse.ArgumentParser(prog="codeagents")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("models", help="List configured model profiles.")
    subparsers.add_parser("runtime", help="Check the configured OpenAI-compatible runtime.")
    subparsers.add_parser("tools", help="List configured tools and MCP providers.")

    ask_parser = subparsers.add_parser("ask", help="Ask the local general agent.")
    ask_parser.add_argument("prompt", help="User prompt.")
    ask_parser.add_argument("--model", default=None, help="Model profile key.")

    chat_parser = subparsers.add_parser("chat", help="Alias for ask.")
    chat_parser.add_argument("prompt", help="User prompt.")
    chat_parser.add_argument("--model", default=None, help="Model profile key.")

    index_parser = subparsers.add_parser("index", help="Build a lightweight workspace index.")
    index_parser.add_argument("path", nargs="?", default=".", help="Workspace path.")
    index_parser.add_argument("--json", action="store_true", help="Print full JSON index.")
    index_parser.add_argument(
        "--embeddings",
        action="store_true",
        help="Also build semantic embeddings using the configured runtime.",
    )

    search_parser = subparsers.add_parser("search", help="Search the workspace index.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument("--workspace", default=".", help="Workspace root.")
    search_parser.add_argument("--semantic", action="store_true", help="Use semantic vector search.")
    search_parser.add_argument("--limit", type=int, default=10, help="Maximum results.")
    search_parser.add_argument("--json", action="store_true", help="Print JSON results.")

    tool_parser = subparsers.add_parser("tool", help="Call a local tool by name.")
    tool_parser.add_argument("name", help="Tool name.")
    tool_parser.add_argument(
        "arguments",
        nargs="?",
        default="{}",
        help="JSON object with tool arguments.",
    )
    tool_parser.add_argument("--workspace", default=".", help="Workspace root.")

    code_parser = subparsers.add_parser("code", help="Ask the coding model about a workspace.")
    code_parser.add_argument("prompt", help="Coding prompt.")
    code_parser.add_argument("--workspace", default=".", help="Workspace root.")
    code_parser.add_argument("--fast", action="store_true", help="Use the fast coding profile.")

    serve_parser = subparsers.add_parser("serve", help="Run the local chat/tool HTTP API.")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    serve_parser.add_argument("--port", type=int, default=8765, help="Bind port.")
    serve_parser.add_argument("--workspace", default=".", help="Workspace root.")

    infer_parser = subparsers.add_parser("infer", help="Run one structured inference request.")
    infer_parser.add_argument("request", help="JSON request or path to request JSON.")

    batch_parser = subparsers.add_parser("infer-batch", help="Run batch structured inference.")
    batch_parser.add_argument("request", help="JSON request or path to batch JSON.")

    registry_parser = subparsers.add_parser("model-registry", help="List registered local models.")
    registry_parser.add_argument("--json", action="store_true", help="Print JSON.")

    model_start_parser = subparsers.add_parser("model-start", help="Start a model backend wrapper.")
    model_start_parser.add_argument("model", help="Registered model key.")

    model_download_parser = subparsers.add_parser("model-download", help="Download model weights.")
    model_download_parser.add_argument("model", help="Registered model key.")
    model_download_parser.add_argument("--output-dir", default=None, help="Where to store weights.")

    model_register_parser = subparsers.add_parser("model-register", help="Add a model to registry.")
    model_register_parser.add_argument("key")
    model_register_parser.add_argument("--runtime-model", required=True)
    model_register_parser.add_argument("--display-name", default="")
    model_register_parser.add_argument("--backend", default="ollama")
    model_register_parser.add_argument("--profile", default="general")
    model_register_parser.add_argument("--weights-path", default="")
    model_register_parser.add_argument("--source", default="")
    model_register_parser.add_argument("--notes", default="")

    inference_logs_parser = subparsers.add_parser("inference-logs", help="Print inference log tail.")
    inference_logs_parser.add_argument("--limit", type=int, default=20)

    benchmark_parser = subparsers.add_parser("benchmark", help="Run local model eval prompts.")
    benchmark_parser.add_argument(
        "--evals",
        default=str(PROJECT_ROOT / "evals" / "local_agent_eval.jsonl"),
        help="Path to JSONL eval cases.",
    )
    benchmark_parser.add_argument(
        "--models",
        nargs="+",
        default=["general", "code", "code_fast", "reasoning"],
        help="Model profile keys to benchmark.",
    )

    args = parser.parse_args()

    if args.command == "models":
        list_models()
    elif args.command == "runtime":
        check_runtime()
    elif args.command == "tools":
        list_tools()
    elif args.command in {"ask", "chat"}:
        ask(args.prompt, model_key=args.model)
    elif args.command == "index":
        index_workspace(Path(args.path), print_json=args.json, embeddings=args.embeddings)
    elif args.command == "search":
        search_workspace(
            args.query,
            workspace=Path(args.workspace),
            semantic=args.semantic,
            limit=args.limit,
            print_json=args.json,
        )
    elif args.command == "tool":
        call_tool(args.name, args.arguments, workspace=Path(args.workspace))
    elif args.command == "code":
        ask_code(args.prompt, workspace=Path(args.workspace), fast=args.fast)
    elif args.command == "serve":
        serve(host=args.host, port=args.port, workspace=Path(args.workspace))
    elif args.command == "infer":
        infer(args.request)
    elif args.command == "infer-batch":
        infer_batch(args.request)
    elif args.command == "model-registry":
        model_registry(print_json=args.json)
    elif args.command == "model-start":
        print(json.dumps(LocalModelService().start(args.model), ensure_ascii=False, indent=2))
    elif args.command == "model-download":
        output_dir = Path(args.output_dir) if args.output_dir else None
        print(
            json.dumps(
                LocalModelService().download(args.model, output_dir=output_dir),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "model-register":
        model = RegisteredModel(
            key=args.key,
            display_name=args.display_name or args.key,
            backend=args.backend,
            runtime_model=args.runtime_model,
            profile=args.profile,
            weights_path=args.weights_path,
            source=args.source,
            notes=args.notes,
        )
        registered = LocalModelService().register_model(model)
        print(json.dumps(registered.__dict__, ensure_ascii=False, indent=2))
    elif args.command == "inference-logs":
        print(json.dumps(InferenceLogger().tail(args.limit), ensure_ascii=False, indent=2))
    elif args.command == "benchmark":
        benchmark(Path(args.evals), model_keys=args.models)


def list_models() -> None:
    config = load_app_config()
    for key, model in config.models.items():
        print(f"{key}: {model.name} [{model.role}], ctx={model.context_tokens}")
        if model.notes:
            print(f"  {model.notes}")


def check_runtime() -> None:
    config = load_app_config()
    runtime = OpenAICompatibleRuntime(config.runtime)
    print(f"Runtime URL: {config.runtime.base_url}")
    try:
        models = runtime.list_models()
    except RuntimeErrorWithHint as exc:
        print(str(exc))
        return
    if not models:
        print("Runtime is reachable, but it did not report any models.")
        return
    print("Runtime models:")
    for model in models:
        print(f"  {model}")


def list_tools() -> None:
    registry = load_tool_registry(PROJECT_ROOT / "config" / "tools.toml")
    for tool in registry.list(include_disabled=True):
        status = "enabled" if tool.enabled else "disabled"
        print(f"{tool.name}: {tool.kind}, {tool.permission}, {status}")
        if tool.description:
            print(f"  {tool.description}")


def ask(prompt: str, *, model_key: str | None) -> None:
    config = load_app_config()
    model = config.model(model_key)
    runtime = OpenAICompatibleRuntime(config.runtime)
    from codeagents.agent import SYSTEM_PROMPT
    chat = Chat.from_prompt(prompt, system=SYSTEM_PROMPT)
    try:
        answer = runtime.chat(model=model, chat=chat)
    except RuntimeErrorWithHint as exc:
        print(str(exc))
        return
    print(answer)


def ask_code(prompt: str, *, workspace: Path, fast: bool) -> None:
    agent = AgentCore.from_workspace(workspace)
    task = "fast" if fast else "code"
    try:
        answer = agent.chat(prompt, task=task)
    except RuntimeErrorWithHint as exc:
        print(str(exc))
        return
    print(answer)


def call_tool(name: str, arguments_json: str, *, workspace: Path) -> None:
    arguments = json.loads(arguments_json)
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    agent = AgentCore.from_workspace(workspace)
    result = agent.call_tool(name, arguments)
    print(json.dumps(result.result, ensure_ascii=False, indent=2))


def index_workspace(path: Path, *, print_json: bool, embeddings: bool = False) -> None:
    config = load_app_config()
    runtime = OpenAICompatibleRuntime(config.runtime) if embeddings else None
    try:
        index = build_index(
            path,
            embeddings=embeddings,
            embedding_client=runtime,
            embedding_model=config.runtime.embedding_model if embeddings else None,
        )
    except RuntimeErrorWithHint as exc:
        if not embeddings:
            raise
        print(f"Embedding index skipped: {exc}")
        index = build_index(path)
    AuditLog(Path(index.root) / ".codeagents" / "audit.jsonl").record(
        tool_name="index",
        permission="read_only",
        arguments={"path": str(path)},
        result_summary=f"Indexed {len(index.files)} files",
        confirmation_required=False,
    )
    if print_json:
        print(index.to_json())
    else:
        summary = index_summary(Path(index.root))
        languages = summary.get("languages", {})
        print(f"Indexed {len(index.files)} files under {index.root}")
        for language, count in sorted(languages.items()):
            print(f"  {language}: {count}")
        print(f"  symbols: {summary.get('symbols', 0)}")
        print(f"  chunks: {summary.get('chunks', 0)}")
        print(f"  embedded_chunks: {summary.get('embedded_chunks', 0)}")


def search_workspace(
    query: str,
    *,
    workspace: Path,
    semantic: bool,
    limit: int,
    print_json: bool,
) -> None:
    config = load_app_config()
    runtime = OpenAICompatibleRuntime(config.runtime) if semantic else None
    try:
        results = search_index(
            workspace,
            query,
            semantic=semantic,
            embedding_client=runtime,
            embedding_model=config.runtime.embedding_model if semantic else None,
            limit=limit,
        )
    except RuntimeErrorWithHint as exc:
        if not semantic:
            raise
        print(f"Semantic search unavailable: {exc}")
        results = search_index(workspace, query, semantic=False, limit=limit)
    payload = [result.__dict__ for result in results]
    if print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for result in results:
        location = f"{result.path}:{result.start_line}-{result.end_line}"
        label = f" {result.name}" if result.name else ""
        print(f"{location} [{result.kind}{label}] score={result.score:.3f}")
        if result.preview:
            print(f"  {result.preview}")


def benchmark(evals_path: Path, *, model_keys: list[str]) -> None:
    config = load_app_config()
    cases = load_eval_cases(evals_path)
    results = run_benchmark(config=config, eval_cases=cases, model_keys=model_keys)
    output_path = write_benchmark_results(PROJECT_ROOT, results)
    ok_count = sum(1 for result in results if result.ok)
    print(f"Benchmark results: {ok_count}/{len(results)} successful calls")
    print(f"Wrote: {output_path}")
    for result in results:
        status = "ok" if result.ok else "failed"
        print(
            f"{result.model_profile}/{result.eval_id}: {status}, "
            f"{result.elapsed_seconds:.2f}s, {result.chars_per_second:.1f} chars/s"
        )
        if result.error:
            print(f"  {result.error}")


def infer(request_ref: str) -> None:
    request = InferenceRequest.model_validate(_load_json_arg(request_ref))
    response = LocalModelService().infer(request)
    print(response.model_dump_json(indent=2, exclude_none=True))


def infer_batch(request_ref: str) -> None:
    request = BatchInferenceRequest.model_validate(_load_json_arg(request_ref))
    response = LocalModelService().batch(request)
    print(response.model_dump_json(indent=2, exclude_none=True))


def model_registry(*, print_json: bool) -> None:
    models = LocalModelService().list_models()
    if print_json:
        print(json.dumps([model.__dict__ for model in models], ensure_ascii=False, indent=2))
        return
    for model in models:
        print(f"{model.key}: {model.runtime_model} [{model.backend}] profile={model.profile}")
        if model.notes:
            print(f"  {model.notes}")


def _load_json_arg(value: str) -> dict:
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


if __name__ == "__main__":
    main()
