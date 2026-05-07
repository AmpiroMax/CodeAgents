import { describe, expect, it } from "vitest";
import {
  appendStreamRow,
  contextUsageFromRow,
  extractDiffSummary,
  summarizeToolArgs,
  wireMessagesToTimeline,
} from "./timeline";
import type { WireMessage } from "./api";

describe("wireMessagesToTimeline", () => {
  it("splits assistant thinking and text into separate timeline items", () => {
    const messages: WireMessage[] = [
      {
        role: "assistant",
        index: 0,
        content: [
          { type: "thinking", thinking: "Let me check..." },
          { type: "text", text: "Final answer" },
        ],
      },
    ];
    const items = wireMessagesToTimeline(messages);
    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({ kind: "thinking", content: "Let me check..." });
    expect(items[1]).toMatchObject({
      kind: "message",
      role: "assistant",
      text: "Final answer",
    });
  });

  it("extracts message text and attachments", () => {
    const messages: WireMessage[] = [
      {
        role: "user",
        index: 0,
        content: [
          { type: "text", text: "hello" },
          { type: "file", file: { path: ".codeagents/uploads/a.txt" } },
        ],
      },
    ];
    expect(wireMessagesToTimeline(messages)[0]).toMatchObject({
      kind: "message",
      text: "hello",
      attachments: [".codeagents/uploads/a.txt"],
    });
  });

  it("tags user messages with their per-message mode from chat meta", () => {
    const messages: WireMessage[] = [
      { role: "user", index: 0, content: [{ type: "text", text: "first" }] },
      { role: "user", index: 2, content: [{ type: "text", text: "second" }] },
    ];
    const items = wireMessagesToTimeline(messages, {
      message_modes: { "0": "ask", "2": "plan" },
    });
    expect(items[0]).toMatchObject({ kind: "message", text: "first", mode: "ask" });
    expect(items[1]).toMatchObject({ kind: "message", text: "second", mode: "plan" });
  });

  it("pairs assistant function_call with the function-role result into a tool card", () => {
    const messages: WireMessage[] = [
      {
        role: "assistant",
        index: 0,
        content: [
          {
            type: "function_call",
            function_call: {
              id: "call_1",
              name: "read_file",
              arguments: '{"path":"README.md"}',
            },
          },
        ],
      },
      {
        role: "function",
        index: 1,
        // BaseMessage carries name/function_call_id at the top level.
        name: "read_file",
        function_call_id: "call_1",
        content: [{ type: "function", function: "file contents here" }],
      } as unknown as WireMessage,
    ];
    const items = wireMessagesToTimeline(messages);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: "tool",
      name: "read_file",
      status: "done",
      args: '{"path":"README.md"}',
      output: "file contents here",
    });
  });

  it("pairs by name when function_call_id is missing", () => {
    const messages: WireMessage[] = [
      {
        role: "assistant",
        index: 0,
        content: [
          {
            type: "function_call",
            function_call: { name: "ls", arguments: '{"path":"."}' },
          },
        ],
      },
      {
        role: "function",
        index: 1,
        name: "ls",
        content: [{ type: "function", function: "a\nb\nc" }],
      } as unknown as WireMessage,
    ];
    const items = wireMessagesToTimeline(messages);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: "tool",
      name: "ls",
      status: "done",
      output: "a\nb\nc",
    });
  });
});

describe("appendStreamRow", () => {
  it("groups tool start, delta, call and result", () => {
    let items = appendStreamRow([], { type: "tool_call_start", name: "shell", index: 0 }, 10);
    items = appendStreamRow(items, { type: "tool_call_delta", name: "shell", delta: "ls" }, 20);
    items = appendStreamRow(items, { type: "tool_call", name: "shell", arguments: "{\"cmd\":\"ls\"}" }, 30);
    items = appendStreamRow(items, { type: "tool_result", name: "shell", result: "ok" }, 40);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: "tool",
      name: "shell",
      status: "done",
      deltas: "ls",
      output: "ok",
    });
  });

  it("appends terminal output", () => {
    const items = appendStreamRow([], {
      type: "terminal_output",
      session_id: "s",
      chunk: "line",
    });
    expect(items[0]).toMatchObject({ kind: "terminal", sessionId: "s", chunk: "line" });
  });
});

describe("contextUsageFromRow", () => {
  it("parses a context_usage row", () => {
    const usage = contextUsageFromRow({
      type: "context_usage",
      prompt_tokens: 120,
      completion_tokens: 30,
      total_tokens: 150,
      context_window: 8192,
    });
    expect(usage).toEqual({
      prompt_tokens: 120,
      completion_tokens: 30,
      total_tokens: 150,
      context_window: 8192,
    });
  });

  it("returns null for unrelated rows", () => {
    expect(contextUsageFromRow({ type: "delta", content: "x" })).toBeNull();
  });

  it("does not push context_usage into the timeline", () => {
    const items = appendStreamRow(
      [],
      { type: "context_usage", prompt_tokens: 1, total_tokens: 2, context_window: 8 },
    );
    expect(items).toHaveLength(0);
  });
});

describe("appendStreamRow tool streaming", () => {
  it("creates a separate tool card for every tool_call_start (no cross-turn dedup)", () => {
    let items = appendStreamRow([], { type: "tool_call_start", index: 0, name: "ls" });
    items = appendStreamRow(items, {
      type: "tool_result",
      name: "ls",
      result: '{"entries":[]}',
    });
    items = appendStreamRow(items, {
      type: "tool_call_start",
      index: 0,
      name: "create_plan",
    });
    const tools = items.filter((it) => it.kind === "tool");
    expect(tools.map((t) => (t.kind === "tool" ? t.name : ""))).toEqual([
      "ls",
      "create_plan",
    ]);
    expect(tools[1]).toMatchObject({ status: "running" });
  });

  it("marks tool result as error when payload looks like a rejection", () => {
    let items = appendStreamRow([], {
      type: "tool_call_start",
      index: 0,
      name: "create_plan",
    });
    items = appendStreamRow(items, {
      type: "tool_result",
      name: "create_plan",
      result:
        '{"error":"invalid_tool_arguments","status":"rejected_invalid_arguments"}',
    });
    const tool = items.find((it) => it.kind === "tool");
    expect(tool && tool.kind === "tool" ? tool.status : "").toBe("error");
  });
});

describe("wireMessagesToTimeline error detection", () => {
  it("paints persisted failed tool result with error status", () => {
    const messages: WireMessage[] = [
      {
        role: "assistant",
        index: 0,
        content: [
          {
            type: "function_call",
            function_call: { id: "c1", name: "create_plan", arguments: "{}" },
          },
        ],
      },
      {
        role: "function",
        index: 1,
        name: "create_plan",
        function_call_id: "c1",
        content: [
          {
            type: "function",
            function:
              '{"error":"invalid_tool_arguments","status":"rejected_invalid_arguments"}',
          },
        ],
      } as WireMessage,
    ];
    const items = wireMessagesToTimeline(messages);
    const tool = items.find((it) => it.kind === "tool");
    expect(tool && tool.kind === "tool" ? tool.status : "").toBe("error");
  });
});

describe("summarizeToolArgs", () => {
  it("picks command field when present", () => {
    expect(summarizeToolArgs("shell", '{"command":"git status"}')).toBe(
      "git status",
    );
  });

  it("falls back to first key for unknown shapes", () => {
    expect(summarizeToolArgs("custom", '{"foo":42}')).toBe("foo: 42");
  });

  it("returns trimmed raw text for non-JSON args", () => {
    expect(summarizeToolArgs("custom", "  hello  world  ")).toBe("hello world");
  });
});

describe("extractDiffSummary", () => {
  it("counts diff lines", () => {
    const diff = extractDiffSummary(
      "edit",
      ["--- a/x", "+++ b/x", "@@ -1 +1 @@", "-old", "+new"].join("\n"),
    );
    expect(diff).toMatchObject({ added: 1, removed: 1 });
  });
});
