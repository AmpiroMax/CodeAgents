import { describe, expect, it } from "vitest";
import { nextMessageIndex, streamRequestBody, type WireMessage } from "./api";

describe("nextMessageIndex", () => {
  it("returns 0 for empty", () => {
    expect(nextMessageIndex([])).toBe(0);
  });

  it("returns max+1", () => {
    const m: WireMessage[] = [
      { role: "user", index: 0, content: [] },
      { role: "assistant", index: 2, content: [] },
    ];
    expect(nextMessageIndex(m)).toBe(3);
  });
});

describe("streamRequestBody", () => {
  it("includes mode when set", () => {
    const chat = {
      id: "abc",
      messages: [] as WireMessage[],
      meta: { title: "t" },
    };
    const body = streamRequestBody(chat, "code", "/ws", "plan");
    expect(body.mode).toBe("plan");
    expect(body.task).toBe("code");
    expect((body.chat as { id: string }).id).toBe("abc");
  });
});
