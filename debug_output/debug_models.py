"""
Debug script: call each Ollama model via raw HTTP and save every SSE chunk.
Saves one JSON file per model with all raw chunks + analysis.
"""
import json
import os
import time
import urllib.request

os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"

MODELS = [
    "gemma4:31b",
    "qwen3.6:27b-coding-nvfp4",
    "qwen2.5-coder:7b",
    "gpt-oss:20b",
]

PROMPT = "Сколько будет 15 * 7? Подумай пошагово, потом дай ответ."

SYSTEM = (
    "Wrap ALL your internal reasoning in <thinking>...</thinking> tags. "
    "Only text OUTSIDE these tags is shown to the user as your answer."
)


def call_model(model: str) -> dict:
    payload = json.dumps({
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT},
        ],
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    chunks = []
    full_content = ""
    full_reasoning = ""
    has_reasoning_field = False
    has_think_tag = False
    has_thinking_tag = False
    has_reason_tag = False

    print(f"\n{'='*60}")
    print(f"MODEL: {model}")
    print(f"{'='*60}")

    t0 = time.time()
    first_token_time = None

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    chunks.append({"raw": line, "parse_error": True})
                    continue

                chunks.append(chunk)

                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta", {})
                finish = choice.get("finish_reason")

                content = delta.get("content", "")
                reasoning = delta.get("reasoning_content", "") or delta.get("reasoning", "")

                if content and first_token_time is None:
                    first_token_time = time.time() - t0
                if reasoning and first_token_time is None:
                    first_token_time = time.time() - t0

                if reasoning:
                    has_reasoning_field = True
                    full_reasoning += reasoning
                    print(f"  [reasoning_field] {repr(reasoning[:80])}")

                if content:
                    full_content += content
                    if "<think>" in content:
                        has_think_tag = True
                    if "<thinking>" in content:
                        has_thinking_tag = True
                    if "<reason>" in content or "<reasoning>" in content:
                        has_reason_tag = True
                    print(f"  [content] {repr(content[:80])}")

                if finish:
                    print(f"  [finish_reason] {finish}")

    except Exception as e:
        print(f"  ERROR: {e}")
        chunks.append({"error": str(e)})

    elapsed = time.time() - t0

    print(f"\n--- Summary for {model} ---")
    print(f"  Chunks: {len(chunks)}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  First token: {first_token_time:.2f}s" if first_token_time else "  First token: N/A")
    print(f"  has reasoning_content field: {has_reasoning_field}")
    print(f"  has <think> tag: {has_think_tag}")
    print(f"  has <thinking> tag: {has_thinking_tag}")
    print(f"  has <reason>/<reasoning> tag: {has_reason_tag}")
    print(f"\n  FULL CONTENT ({len(full_content)} chars):")
    print(f"  {full_content[:500]}")
    if full_reasoning:
        print(f"\n  FULL REASONING ({len(full_reasoning)} chars):")
        print(f"  {full_reasoning[:500]}")

    return {
        "model": model,
        "prompt": PROMPT,
        "system": SYSTEM,
        "elapsed_s": round(elapsed, 2),
        "first_token_s": round(first_token_time, 2) if first_token_time else None,
        "chunk_count": len(chunks),
        "full_content": full_content,
        "full_reasoning": full_reasoning,
        "has_reasoning_field": has_reasoning_field,
        "has_think_tag": has_think_tag,
        "has_thinking_tag": has_thinking_tag,
        "has_reason_tag": has_reason_tag,
        "chunks": chunks,
    }


if __name__ == "__main__":
    for model in MODELS:
        try:
            result = call_model(model)
            out_name = model.replace(":", "_").replace("/", "_")
            path = f"debug_output/{out_name}.json"
            with open(path, "w") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\n  Saved to {path}")
        except Exception as e:
            print(f"\n  FAILED {model}: {e}")

    print("\n\nDone! Check debug_output/ for JSON files.")
