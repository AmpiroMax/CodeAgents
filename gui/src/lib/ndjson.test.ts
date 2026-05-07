import { describe, expect, it } from "vitest";
import { parseNdjsonLine, readNdjsonStream } from "./ndjson";

describe("parseNdjsonLine", () => {
  it("parses a stream row", () => {
    const row = parseNdjsonLine('{"type":"delta","content":"hi"}');
    expect(row).toEqual({ type: "delta", content: "hi" });
  });

  it("returns null for empty", () => {
    expect(parseNdjsonLine("  ")).toBeNull();
  });

  it("returns null for invalid json", () => {
    expect(parseNdjsonLine("{")).toBeNull();
  });
});

describe("readNdjsonStream", () => {
  it("yields rows split across chunks", async () => {
    const enc = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(enc.encode('{"type":"a"}\n{"type":'));
        controller.enqueue(enc.encode('"b","x":1}\n'));
        controller.close();
      },
    });
    const rows: { type: string }[] = [];
    for await (const r of readNdjsonStream(stream)) {
      rows.push(r);
    }
    expect(rows).toEqual([{ type: "a" }, { type: "b", x: 1 }]);
  });
});
