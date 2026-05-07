from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from codeagents.core.config import ModelProfile, RuntimeConfig
from codeagents.core.budget.params import load_params
from codeagents.observability.runtime_log import RuntimeRequestLogger, RuntimeRequestLogEntry
from codeagents.core.schemas import AssistantMessage, Chat, FunctionSpec, InferenceResponse, TextContent


class RuntimeErrorWithHint(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatResult:
    content: str
    elapsed_seconds: float
    raw: dict[str, Any]


class OpenAICompatibleRuntime:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        # Local model runtimes should not be routed through HTTP(S)_PROXY.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.logger = RuntimeRequestLogger()

    def chat(
        self,
        *,
        model: ModelProfile,
        chat: Chat,
        reasoning_effort: str | None = None,
    ) -> str:
        return self.chat_with_metrics(
            model=model, chat=chat, reasoning_effort=reasoning_effort
        ).content

    def chat_with_metrics(
        self,
        *,
        model: ModelProfile,
        chat: Chat,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        params = load_params(model.name, default_temperature=model.temperature)
        payload: dict[str, Any] = {
            "model": model.name,
            "messages": chat.to_openai_messages(),
        }
        payload.update(params.openai_payload())
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if reasoning_effort:
            # Ollama OpenAI-compat field for thinking models. Accepts
            # "none" / "low" / "medium" / "high"; non-thinking models ignore it.
            payload["reasoning_effort"] = reasoning_effort
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers=self._headers(),
            method="POST",
        )

        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=120) as response:
                raw = json.loads(response.read().decode("utf-8"))
            elapsed = time.perf_counter() - started
        except urllib.error.HTTPError as exc:
            elapsed = time.perf_counter() - started
            detail = exc.read().decode("utf-8", errors="replace")
            self.logger.record(
                RuntimeRequestLogEntry(
                    runtime_url=self.config.base_url,
                    model=model.name,
                    payload=payload,
                    error=f"HTTP {exc.code}: {detail}",
                    elapsed_ms=elapsed * 1000,
                )
            )
            raise RuntimeErrorWithHint(
                f"Local runtime returned HTTP {exc.code} for model '{model.name}': {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            elapsed = time.perf_counter() - started
            self.logger.record(
                RuntimeRequestLogEntry(
                    runtime_url=self.config.base_url,
                    model=model.name,
                    payload=payload,
                    error=str(exc),
                    elapsed_ms=elapsed * 1000,
                )
            )
            raise RuntimeErrorWithHint(
                "Local runtime is unavailable. Start Ollama, llama.cpp server, or an MLX "
                f"OpenAI-compatible server at {self.config.base_url}."
            ) from exc
        self.logger.record(
            RuntimeRequestLogEntry(
                runtime_url=self.config.base_url,
                model=model.name,
                payload=payload,
                response=raw,
                elapsed_ms=elapsed * 1000,
            )
        )

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeErrorWithHint(f"Unexpected runtime response: {raw}") from exc
        return ChatResult(content=content, elapsed_seconds=elapsed, raw=raw)

    def chat_stream(
        self,
        *,
        model: ModelProfile,
        chat: Chat | None = None,
        messages: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        tools: list[FunctionSpec] | None = None,
        tool_schemas: list[dict[str, Any]] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Stream tokens from the LLM. Yields event dicts:
        {"type":"delta","content":"..."} | {"type":"thinking","content":"..."}
        {"type":"tool_call","name":"...","arguments":"..."} | {"type":"done"}
        {"type":"error","message":"..."}

        Pass either `chat` or `messages` (raw OpenAI-format dicts).
        Pass either `tools` (FunctionSpec list) or `tool_schemas` (raw JSON).
        """
        if messages is None:
            if chat is None:
                raise ValueError("Provide either chat or messages")
            messages = chat.to_openai_messages()
        resolved_schemas = tool_schemas
        if resolved_schemas is None and tools:
            resolved_schemas = [t.to_json_schema() for t in tools]

        params = load_params(model.name, default_temperature=model.temperature)
        payload: dict[str, Any] = {
            "model": model.name,
            "messages": messages,
            "stream": True,
            # Ask the OpenAI-compatible endpoint to include a final usage block
            # so we can surface real prompt/completion token counts to the GUI.
            "stream_options": {"include_usage": True},
        }
        payload.update(params.openai_payload())
        if temperature is not None:
            payload["temperature"] = temperature
        if resolved_schemas:
            payload["tools"] = resolved_schemas
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers=self._headers(),
            method="POST",
        )

        started = time.perf_counter()
        try:
            # 30 min total: matches the client side. We never close the
            # request mid-stream; reads keep ticking as long as Ollama emits
            # any byte (delta token, thinking token, tool args).
            response = self.opener.open(request, timeout=1800)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            yield {"type": "error", "message": _humanize_runtime_error(exc.code, detail)}
            return
        except urllib.error.URLError as exc:
            yield {"type": "error", "message": f"Runtime unavailable: {exc}"}
            return

        accumulated_tool_calls: dict[int, dict[str, Any]] = {}
        full_content = ""
        usage_block: dict[str, Any] | None = None

        try:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Final chunk in streaming mode carries usage but no choices.
                if isinstance(chunk.get("usage"), dict):
                    usage_block = chunk["usage"]

                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta", {})
                finish = choice.get("finish_reason")

                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if reasoning:
                    yield {"type": "thinking", "content": reasoning}

                content = delta.get("content") or ""
                if content:
                    full_content += content
                    yield {"type": "delta", "content": content}

                for tc in delta.get("tool_calls", []):
                    i = tc.get("index", 0)
                    fn = tc.get("function", {})
                    is_new = i not in accumulated_tool_calls
                    if is_new:
                        accumulated_tool_calls[i] = {
                            "name": fn.get("name", ""),
                            "arguments": "",
                        }
                    if "name" in fn and fn["name"]:
                        accumulated_tool_calls[i]["name"] = fn["name"]
                    arg_chunk = fn.get("arguments", "") or ""
                    accumulated_tool_calls[i]["arguments"] += arg_chunk

                    if is_new:
                        yield {
                            "type": "tool_call_start",
                            "index": i,
                            "name": accumulated_tool_calls[i]["name"],
                        }
                    if arg_chunk:
                        # Some runtimes (Ollama harmony parser for gpt-oss) hand us
                        # the entire tool-call arguments in one chunk after the model
                        # finishes generating. Re-chunk into smaller pieces so the
                        # client sees a typing animation instead of a sudden blob.
                        nm = accumulated_tool_calls[i]["name"]
                        for sub in _rechunk_for_animation(arg_chunk):
                            yield {
                                "type": "tool_call_delta",
                                "index": i,
                                "delta": sub,
                                "name": nm,
                            }

                if finish == "tool_calls":
                    for _, tc_data in sorted(accumulated_tool_calls.items()):
                        yield {
                            "type": "tool_call",
                            "name": tc_data["name"],
                            "arguments": tc_data["arguments"],
                        }
                    accumulated_tool_calls.clear()

        finally:
            response.close()
            elapsed = time.perf_counter() - started
            self.logger.record(
                RuntimeRequestLogEntry(
                    runtime_url=self.config.base_url,
                    model=model.name,
                    payload={k: v for k, v in payload.items() if k != "stream"},
                    response={"streamed": True, "content_length": len(full_content)},
                    elapsed_ms=elapsed * 1000,
                )
            )

        if usage_block is not None:
            yield {
                "type": "context_usage",
                "prompt_tokens": int(usage_block.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage_block.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage_block.get("total_tokens", 0) or 0),
                "context_window": int(getattr(params, "num_ctx", 0) or 0),
            }

        yield {"type": "done"}

    def infer(
        self,
        *,
        model: ModelProfile,
        chat: Chat,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> InferenceResponse:
        result = self.chat_with_metrics(
            model=model,
            chat=chat,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        assistant = AssistantMessage(
            index=len(chat.messages),
            content=[TextContent(text=result.content)],
        )
        return InferenceResponse(
            chat_id=chat.id,
            model=model.name,
            assistant=assistant,
            elapsed_seconds=result.elapsed_seconds,
            raw=result.raw,
        )

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        selected_model = model or self.config.embedding_model
        if not selected_model:
            raise RuntimeErrorWithHint(
                "No embedding model configured. Set [runtime].embedding_model in config/models.toml."
            )
        payload: dict[str, Any] = {
            "model": selected_model,
            "input": texts,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/embeddings",
            data=body,
            headers=self._headers(),
            method="POST",
        )
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=120) as response:
                raw = json.loads(response.read().decode("utf-8"))
            elapsed = time.perf_counter() - started
        except urllib.error.HTTPError as exc:
            elapsed = time.perf_counter() - started
            detail = exc.read().decode("utf-8", errors="replace")
            self.logger.record(
                RuntimeRequestLogEntry(
                    runtime_url=self.config.base_url,
                    model=selected_model,
                    payload=payload,
                    error=f"HTTP {exc.code}: {detail}",
                    elapsed_ms=elapsed * 1000,
                )
            )
            raise RuntimeErrorWithHint(
                f"Embedding runtime returned HTTP {exc.code} for model '{selected_model}': {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            elapsed = time.perf_counter() - started
            self.logger.record(
                RuntimeRequestLogEntry(
                    runtime_url=self.config.base_url,
                    model=selected_model,
                    payload=payload,
                    error=str(exc),
                    elapsed_ms=elapsed * 1000,
                )
            )
            raise RuntimeErrorWithHint(
                f"Embedding runtime is unavailable at {self.config.base_url}."
            ) from exc

        vectors = _parse_embedding_response(raw)
        if len(vectors) != len(texts):
            raise RuntimeErrorWithHint(
                f"Embedding runtime returned {len(vectors)} vectors for {len(texts)} inputs."
            )
        self.logger.record(
            RuntimeRequestLogEntry(
                runtime_url=self.config.base_url,
                model=selected_model,
                payload={**payload, "input": f"{len(texts)} texts"},
                response={"embeddings": len(vectors), "dimensions": len(vectors[0]) if vectors else 0},
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        )
        return vectors

    def list_models(self) -> list[str]:
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/models",
            headers=self._headers(),
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeErrorWithHint(
                f"Local runtime returned HTTP {exc.code} at {self.config.base_url}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeErrorWithHint(
                f"Local runtime is unavailable at {self.config.base_url}."
            ) from exc

        models = raw.get("data", [])
        names = [item.get("id") for item in models if isinstance(item, dict) and item.get("id")]
        return sorted(names)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers


def _rechunk_for_animation(text: str, target: int = 16) -> list[str]:
    """Split a chunk into ~target-char pieces on word/punctuation boundaries.

    Used to animate tool-call argument streaming when the upstream runtime
    delivers the whole JSON in one go (e.g. Ollama's harmony parser does this
    for gpt-oss). Keeping the chunk small lets the TUI redraw between pieces.
    """
    if len(text) <= target:
        return [text]
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + target, n)
        if end < n:
            # Walk forward to a natural boundary (space, punctuation) within +12 chars.
            limit = min(end + 12, n)
            j = end
            while j < limit and text[j] not in " ,.\n\":}]\t":
                j += 1
            end = j if j < limit else end
        chunks.append(text[i:end])
        i = end
    return chunks


def _humanize_runtime_error(code: int, body: str) -> str:
    """Turn a verbose Ollama 500 body into a short, actionable message."""
    try:
        parsed = json.loads(body)
        msg = parsed.get("error", {}).get("message", body) if isinstance(parsed, dict) else body
    except (ValueError, TypeError):
        msg = body
    if isinstance(msg, str) and "error parsing tool call" in msg:
        return (
            f"HTTP {code}: model produced invalid JSON in a tool call. "
            "This usually means an unescaped backslash or quote in arguments "
            '(e.g. Windows paths must be \\\\, quotes must be \\"). '
            "Ask the model to retry the tool call with properly escaped JSON."
        )
    short = msg if isinstance(msg, str) else json.dumps(msg)
    if len(short) > 400:
        short = short[:400] + "…"
    return f"HTTP {code}: {short}"


def _parse_embedding_response(raw: dict[str, Any]) -> list[list[float]]:
    data = raw.get("data")
    if isinstance(data, list):
        vectors: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            embedding = item.get("embedding")
            if isinstance(embedding, list):
                vectors.append([float(value) for value in embedding])
        return vectors
    embedding = raw.get("embedding")
    if isinstance(embedding, list):
        return [[float(value) for value in embedding]]
    embeddings = raw.get("embeddings")
    if isinstance(embeddings, list):
        return [[float(value) for value in vector] for vector in embeddings if isinstance(vector, list)]
    raise RuntimeErrorWithHint(f"Unexpected embedding response: {raw}")
