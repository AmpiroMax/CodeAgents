import type { WireContent, WireMessage } from "./api";
import type { StreamRow } from "./ndjson";

export type PendingTool = {
  decision_id: string;
  name: string;
  arguments: string;
  remember_supported: boolean;
  warning: string;
};

export type TimelineItem =
  | {
      id: string;
      kind: "message";
      role: string;
      text: string;
      attachments: string[];
      mode?: string;
    }
  | { id: string; kind: "assistant-live"; content: string }
  | { id: string; kind: "thinking"; content: string }
  | {
      id: string;
      kind: "tool";
      name: string;
      status: "running" | "done" | "pending" | "error";
      args: string;
      output: string;
      deltas: string;
      startedAt: number;
      completedAt?: number;
      diff?: DiffSummary;
      decisionId?: string;
      warning?: string;
    }
  | { id: string; kind: "notice"; level: string; message: string }
  | { id: string; kind: "terminal"; sessionId: string; chunk: string }
  | { id: string; kind: "model"; model: string }
  | { id: string; kind: "done"; model: string; stopReason: string }
  | { id: string; kind: "error"; message: string };

export type DiffSummary = {
  title: string;
  added: number;
  removed: number;
  preview: string[];
};

export function blockPreview(block: WireContent): { text: string; attachment?: string } {
  const ty = typeof block.type === "string" ? block.type : "";
  if (ty === "text" && typeof block.text === "string") {
    return { text: block.text };
  }
  if (
    ty === "thinking" &&
    typeof (block as { thinking?: string }).thinking === "string"
  ) {
    return { text: (block as { thinking: string }).thinking };
  }
  if (ty === "image" && typeof (block as { image?: string }).image === "string") {
    const src = (block as { image: string }).image;
    return { text: "", attachment: `image: ${src}` };
  }
  if (ty === "file") {
    const file = block.file as { path?: string; media_type?: string } | undefined;
    const suffix = file?.media_type ? ` (${file.media_type})` : "";
    return {
      text: "",
      attachment: file?.path ? `${file.path}${suffix}` : "file",
    };
  }
  if (ty === "function_call") {
    return {
      text: JSON.stringify((block as { function_call?: unknown }).function_call ?? {}),
    };
  }
  return { text: `[${ty || "content"}]` };
}

type FunctionCallBlock = {
  id?: string;
  name?: string;
  arguments?: unknown;
};

function readFunctionCall(block: WireContent): FunctionCallBlock | null {
  const fc = (block as { function_call?: unknown }).function_call;
  if (!fc || typeof fc !== "object") {
    return null;
  }
  const obj = fc as Record<string, unknown>;
  const args = obj.arguments;
  return {
    id: typeof obj.id === "string" ? obj.id : undefined,
    name: typeof obj.name === "string" ? obj.name : undefined,
    arguments:
      typeof args === "string"
        ? args
        : args !== undefined
          ? JSON.stringify(args)
          : undefined,
  };
}

/** Convert persisted wire messages into timeline items.
 *
 * Pairs assistant ``function_call`` blocks with the matching ``function``-role
 * result message into a single ``kind: "tool"`` card, so the saved view looks
 * the same as the live streaming view. Per-user-message mode tags are read
 * from ``chatMeta.message_modes[<index>]`` (a `{ [index]: "agent"|"plan"|"ask" }`
 * map maintained by the GUI when the user sends a message).
 */
export function wireMessagesToTimeline(
  messages: WireMessage[],
  chatMeta: Record<string, unknown> | undefined = undefined,
): TimelineItem[] {
  const messageModes =
    chatMeta && typeof chatMeta === "object"
      ? ((chatMeta as Record<string, unknown>).message_modes as
          | Record<string, unknown>
          | undefined)
      : undefined;
  const modeFor = (index: number): string | undefined => {
    if (!messageModes) {
      return undefined;
    }
    const raw = messageModes[String(index)] ?? messageModes[index as unknown as string];
    return typeof raw === "string" ? raw : undefined;
  };

  const items: TimelineItem[] = [];
  // Map from function_call id (or synthetic ordinal) → tool item index inside
  // ``items``. Used to attach the function-role result later.
  const pendingToolIndex = new Map<string, number>();
  let syntheticToolOrdinal = 0;

  messages.forEach((message, i) => {
    const baseId = `message-${message.role}-${message.index}-${i}`;

    // function-role messages carry the raw tool result.
    if (message.role === "function") {
      const fnName = (message as unknown as { name?: string }).name;
      const callId = (message as unknown as { function_call_id?: string })
        .function_call_id;
      const text = message.content
        .map((block) => {
          const ty = typeof block.type === "string" ? block.type : "";
          if (ty === "function") {
            return String((block as { function?: string }).function ?? "");
          }
          return blockPreview(block).text;
        })
        .filter(Boolean)
        .join("\n");
      // Try every key we might have keyed the call under, in priority order.
      // Real-world chats: providers often disagree on call-id schemes
      // (assistant uses one, function-role result uses another like
      // ``call_0_0``), so we always fall back to a name match and finally to
      // the most recent un-paired tool of any name.
      const candidates: string[] = [];
      if (callId) candidates.push(callId);
      if (fnName) candidates.push(`name:${fnName}`);
      candidates.push(`ord:${syntheticToolOrdinal - 1}`);
      let slot: number | undefined;
      let matchedKey: string | undefined;
      for (const k of candidates) {
        const v = pendingToolIndex.get(k);
        if (v !== undefined) {
          slot = v;
          matchedKey = k;
          break;
        }
      }
      if (slot !== undefined) {
        const tool = items[slot];
        if (tool && tool.kind === "tool") {
          items[slot] = {
            ...tool,
            output: text,
            status: looksLikeToolError(text) ? "error" : "done",
          };
        }
        if (matchedKey) pendingToolIndex.delete(matchedKey);
        // Also clear sibling keys pointing at the same slot so a later
        // function-role doesn't accidentally re-bind to the same call.
        for (const [k, v] of Array.from(pendingToolIndex.entries())) {
          if (v === slot) pendingToolIndex.delete(k);
        }
      } else {
        items.push({
          id: `${baseId}-orphan`,
          kind: "tool",
          name: fnName ?? "tool",
          status: looksLikeToolError(text) ? "error" : "done",
          args: "",
          output: text,
          deltas: "",
          startedAt: 0,
        });
      }
      return;
    }

    const textParts: string[] = [];
    const attachments: string[] = [];
    let thinkingBuffer = "";

    const flushThinking = (suffix: string) => {
      if (thinkingBuffer.trim()) {
        items.push({
          id: `${baseId}-thinking-${suffix}`,
          kind: "thinking",
          content: thinkingBuffer,
        });
      }
      thinkingBuffer = "";
    };

    message.content.forEach((block, blockIdx) => {
      const ty = typeof block.type === "string" ? block.type : "";
      if (ty === "thinking") {
        const value = (block as { thinking?: string }).thinking;
        if (typeof value === "string") {
          thinkingBuffer += (thinkingBuffer ? "\n" : "") + value;
        }
        return;
      }
      if (ty === "function_call" || ty === "thinking_function_call") {
        flushThinking(String(blockIdx));
        const call = readFunctionCall(block);
        const name = call?.name ?? "tool";
        const args =
          typeof call?.arguments === "string" ? call.arguments : "";
        const key = call?.id ?? `ord:${syntheticToolOrdinal}`;
        const id = `${baseId}-tool-${blockIdx}`;
        // Persisted view starts from "done" — if a result actually exists in
        // the chat we'll attach it below; if it doesn't (orphaned call from a
        // previous interrupted run) we still show a settled card instead of a
        // never-ending "running" indicator.
        items.push({
          id,
          kind: "tool",
          name,
          status: "done",
          args,
          output: "",
          deltas: "",
          startedAt: 0,
        });
        pendingToolIndex.set(key, items.length - 1);
        if (call?.name) {
          // Also key by name to handle providers that drop the call id.
          const nameKey = `name:${call.name}`;
          if (!pendingToolIndex.has(nameKey)) {
            pendingToolIndex.set(nameKey, items.length - 1);
          }
        }
        syntheticToolOrdinal += 1;
        return;
      }
      flushThinking(String(blockIdx));
      const preview = blockPreview(block);
      if (preview.text) {
        textParts.push(preview.text);
      }
      if (preview.attachment) {
        attachments.push(preview.attachment);
      }
    });
    flushThinking("end");

    const text = textParts.join("\n\n");
    if (text || attachments.length > 0) {
      const item: Extract<TimelineItem, { kind: "message" }> = {
        id: baseId,
        kind: "message",
        role: message.role,
        text,
        attachments,
      };
      if (message.role === "user") {
        const mode = modeFor(message.index);
        if (mode) {
          item.mode = mode;
        }
      }
      items.push(item);
    }
  });
  return items;
}

export function appendStreamRow(
  items: TimelineItem[],
  row: StreamRow,
  now = Date.now(),
): TimelineItem[] {
  switch (row.type) {
    case "delta":
      return appendText(items, "assistant-live", String(row.content ?? ""));
    case "thinking":
      return appendText(items, "thinking", String(row.content ?? ""));
    case "model_info":
      return [
        ...items,
        { id: `model-${items.length}`, kind: "model", model: String(row.model ?? "") },
      ];
    case "tool_call_start": {
      // The runtime guarantees one ``tool_call_start`` per call (gated on
      // ``is_new`` per (turn, index) tuple). We must NOT dedup by row.index
      // here: across turns / across user messages the index resets to 0 and
      // a stale match would silently swallow the new card. Use the current
      // items.length as a strictly-monotonic id so every start spawns a card.
      const id = `tool-${items.length}-${String(row.tool_call_id ?? row.index ?? 0)}`;
      return [
        ...items,
        {
          id,
          kind: "tool",
          name: String(row.name ?? "tool"),
          status: "running",
          args: "",
          output: "",
          deltas: "",
          startedAt: now,
        },
      ];
    }
    case "tool_call_delta":
      return updateLastTool(items, row, (tool) => ({
        ...tool,
        name: String(row.name ?? tool.name),
        deltas: tool.deltas + String(row.delta ?? ""),
      }));
    case "tool_call":
      return updateLastTool(items, row, (tool) => ({
        ...tool,
        name: String(row.name ?? tool.name),
        args: String(row.arguments ?? tool.args),
        status: "running",
      }));
    case "tool_result":
      return updateLastTool(items, row, (tool) => {
        const output = String(row.result ?? "");
        return {
          ...tool,
          name: String(row.name ?? tool.name),
          output,
          diff: extractDiffSummary(tool.name, output),
          status: looksLikeToolError(output) ? "error" : "done",
          completedAt: now,
        };
      });
    case "tool_pending":
      return [
        ...items,
        {
          id: `pending-${String(row.decision_id ?? items.length)}`,
          kind: "tool",
          name: String(row.name ?? "tool"),
          status: "pending",
          args: String(row.arguments ?? ""),
          output: "",
          deltas: "",
          startedAt: now,
          decisionId: String(row.decision_id ?? ""),
          warning: String(row.warning ?? ""),
        },
      ];
    case "notice":
      return [
        ...items,
        {
          id: `notice-${items.length}`,
          kind: "notice",
          level: String(row.level ?? "info"),
          message: String(row.message ?? ""),
        },
      ];
    case "terminal_output":
      return [
        ...items,
        {
          id: `terminal-${items.length}`,
          kind: "terminal",
          sessionId: String(row.session_id ?? ""),
          chunk: String(row.chunk ?? ""),
        },
      ];
    case "done":
      return [
        ...items,
        {
          id: `done-${items.length}`,
          kind: "done",
          model: String(row.model ?? ""),
          stopReason: String(row.stop_reason ?? "completed"),
        },
      ];
    case "error":
      return [
        ...items,
        { id: `error-${items.length}`, kind: "error", message: String(row.message ?? "") },
      ];
    default:
      return items;
  }
}

export type ContextUsageRow = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  context_window: number;
};

/** Extract a ``context_usage`` row into a typed object, or ``null`` if it isn't one. */
export function contextUsageFromRow(row: StreamRow): ContextUsageRow | null {
  if (row.type !== "context_usage") {
    return null;
  }
  return {
    prompt_tokens: Number(row.prompt_tokens ?? 0) || 0,
    completion_tokens: Number(row.completion_tokens ?? 0) || 0,
    total_tokens: Number(row.total_tokens ?? 0) || 0,
    context_window: Number(row.context_window ?? 0) || 0,
  };
}

export function pendingToolFromItem(item: TimelineItem): PendingTool | null {
  if (item.kind !== "tool" || item.status !== "pending" || !item.decisionId) {
    return null;
  }
  return {
    decision_id: item.decisionId,
    name: item.name,
    arguments: item.args,
    remember_supported: false,
    warning: item.warning ?? "",
  };
}

function appendText(
  items: TimelineItem[],
  kind: "assistant-live" | "thinking",
  content: string,
): TimelineItem[] {
  if (!content) {
    return items;
  }
  const last = items.at(-1);
  if (last?.kind === kind) {
    return [
      ...items.slice(0, -1),
      { ...last, content: last.content + content },
    ] as TimelineItem[];
  }
  return [...items, { id: `${kind}-${items.length}`, kind, content }];
}

function updateLastTool(
  items: TimelineItem[],
  row: StreamRow,
  update: (tool: Extract<TimelineItem, { kind: "tool" }>) => TimelineItem,
): TimelineItem[] {
  const index = findToolIndex(items, String(row.name ?? ""));
  if (index < 0) {
    return items;
  }
  const tool = items[index];
  if (!tool || tool.kind !== "tool") {
    return items;
  }
  return [...items.slice(0, index), update(tool), ...items.slice(index + 1)];
}

function findToolIndex(items: TimelineItem[], name: string): number {
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (item?.kind === "tool" && item.status === "running") {
      if (!name || item.name === name || item.name === "tool") {
        return i;
      }
    }
  }
  return -1;
}

/** Best-effort detection of failed tool results.
 *
 * Backends (and the agent's own argument validator) return errors as a JSON
 * blob with ``error`` / ``status: "rejected_*"`` / ``status: "error"`` keys.
 * Some shell tools instead surface a plain "exit_code: 7" line. We use a
 * permissive parse so the timeline can paint a red ✕ icon instead of a
 * misleading green ✓ when the call clearly didn't succeed. */
export function looksLikeToolError(output: string): boolean {
  const text = (output || "").trim();
  if (!text) return false;
  try {
    const parsed = JSON.parse(text) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const obj = parsed as Record<string, unknown>;
      if (typeof obj.error === "string" && obj.error.length > 0) return true;
      const status = typeof obj.status === "string" ? obj.status : "";
      if (
        status === "error" ||
        status === "failed" ||
        status.startsWith("rejected") ||
        status.startsWith("error")
      ) {
        return true;
      }
    }
  } catch {
    // Not JSON — fall through to heuristic checks on the raw text.
  }
  return false;
}

/** Compact one-line argument summary for tool cards (OpenCode-style).
 *
 * Tries to JSON-parse ``args``; falls back to the first 80 chars of the raw
 * string. Picks a sensible field for common tool names (path/command/query).
 */
export function summarizeToolArgs(name: string, args: string): string {
  const raw = (args || "").trim();
  if (!raw) {
    return "";
  }
  let parsed: Record<string, unknown> | null = null;
  try {
    const value = JSON.parse(raw) as unknown;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      parsed = value as Record<string, unknown>;
    }
  } catch {
    parsed = null;
  }
  if (!parsed) {
    return raw.replace(/\s+/g, " ").slice(0, 120);
  }
  const preferred = [
    "command",
    "cmd",
    "path",
    "file",
    "filename",
    "query",
    "pattern",
    "url",
  ];
  for (const key of preferred) {
    const value = parsed[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim().slice(0, 160);
    }
  }
  const first = Object.entries(parsed)[0];
  if (first) {
    const [key, value] = first;
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return `${key}: ${text}`.slice(0, 160);
  }
  return name;
}

export function extractDiffSummary(name: string, output: string): DiffSummary | undefined {
  const lower = `${name} ${output.slice(0, 200)}`.toLowerCase();
  if (
    !lower.includes("diff") &&
    !lower.includes("patch") &&
    !lower.includes("edit") &&
    !/^\s*(---|\+\+\+|@@)/m.test(output)
  ) {
    return undefined;
  }
  const lines = output.split(/\r?\n/);
  const diffLines = lines.filter((line) => /^(---|\+\+\+|@@|[-+][^-+])/.test(line));
  if (diffLines.length === 0) {
    return undefined;
  }
  return {
    title: name,
    added: diffLines.filter((line) => line.startsWith("+") && !line.startsWith("+++")).length,
    removed: diffLines.filter((line) => line.startsWith("-") && !line.startsWith("---")).length,
    preview: diffLines.slice(0, 80),
  };
}
