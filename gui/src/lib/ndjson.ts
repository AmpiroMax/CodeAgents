/** NDJSON lines from ``POST /chat/stream`` (same wire format as the Rust TUI). */

export type StreamRow = { type: string } & Record<string, unknown>;

export function parseNdjsonLine(line: string): StreamRow | null {
  const t = line.trim();
  if (!t) {
    return null;
  }
  try {
    return JSON.parse(t) as StreamRow;
  } catch {
    return null;
  }
}

/**
 * Incrementally decode newline-delimited JSON from a fetch body.
 * Handles chunks that split mid-line.
 */
export async function* readNdjsonStream(
  body: ReadableStream<Uint8Array> | null,
): AsyncGenerator<StreamRow> {
  if (!body) {
    return;
  }
  const decoder = new TextDecoder();
  const reader = body.getReader();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const nl = buffer.indexOf("\n");
        if (nl < 0) {
          break;
        }
        const line = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 1);
        const row = parseNdjsonLine(line);
        if (row) {
          yield row;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
  const tail = buffer.trim();
  if (tail) {
    const row = parseNdjsonLine(tail);
    if (row) {
      yield row;
    }
  }
}
