"""Debug a single model's raw SSE output."""
import json
import os
import sys
import time
import urllib.request

os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"

SYSTEM = (
    "Wrap ALL your internal reasoning in <thinking>...</thinking> tags. "
    "Only text OUTSIDE these tags is shown to the user as your answer."
)
PROMPT = "Сколько будет 15 * 7? Подумай пошагово, потом дай ответ."

model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5-coder:7b"

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

print(f"MODEL: {model}")
print(f"{'='*60}")
t0 = time.time()

with urllib.request.urlopen(req, timeout=180) as resp:
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line == "data: [DONE]":
            continue
        if line.startswith("data: "):
            line = line[6:]
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            print(f"PARSE ERROR: {line[:100]}")
            continue

        chunks.append(chunk)
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta", {})
        finish = choice.get("finish_reason")

        content = delta.get("content", "")
        reasoning = delta.get("reasoning_content", "") or delta.get("reasoning", "")

        if reasoning:
            full_reasoning += reasoning
            print(f"R: {repr(reasoning)}")
        if content:
            full_content += content
            print(f"C: {repr(content)}")
        if finish:
            print(f"FINISH: {finish}")

elapsed = time.time() - t0

print(f"\n{'='*60}")
print(f"Elapsed: {elapsed:.1f}s, Chunks: {len(chunks)}")
print(f"\nFULL CONTENT:\n{full_content}")
if full_reasoning:
    print(f"\nFULL REASONING:\n{full_reasoning}")

# Check all delta keys across all chunks
all_delta_keys = set()
for c in chunks:
    ch = (c.get("choices") or [{}])[0]
    d = ch.get("delta", {})
    all_delta_keys.update(d.keys())
print(f"\nAll delta keys seen: {all_delta_keys}")

out_name = model.replace(":", "_").replace("/", "_")
path = f"debug_output/{out_name}.json"
with open(path, "w") as f:
    json.dump({
        "model": model,
        "elapsed_s": round(elapsed, 2),
        "chunk_count": len(chunks),
        "full_content": full_content,
        "full_reasoning": full_reasoning,
        "all_delta_keys": list(all_delta_keys),
        "chunks": chunks,
    }, f, ensure_ascii=False, indent=2)
print(f"Saved to {path}")
