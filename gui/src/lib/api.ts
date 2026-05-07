const DEFAULT_API = "http://127.0.0.1:8765";

export function defaultApiBase(): string {
  if (
    typeof window !== "undefined" &&
    window.location.pathname.startsWith("/ui")
  ) {
    return "";
  }
  const v = import.meta.env.VITE_API_BASE;
  return typeof v === "string" && v.length > 0 ? v.replace(/\/$/, "") : DEFAULT_API;
}

export type WireContent = Record<string, unknown>;

export type WireMessage = {
  role: string;
  index: number;
  content: WireContent[];
};

export type WireChat = {
  id?: string;
  messages: WireMessage[];
  meta: Record<string, unknown>;
};

export type ChatSummary = {
  id: string;
  title: string;
  message_count: number;
  workspace?: string;
};

export type HealthInfo = { ok: boolean; version: string };

export async function fetchHealth(base: string): Promise<HealthInfo> {
  try {
    const r = await fetch(`${base}/health`);
    if (!r.ok) return { ok: false, version: "" };
    const j = (await r.json()) as { ok?: boolean; version?: string };
    return { ok: j.ok === true, version: typeof j.version === "string" ? j.version : "" };
  } catch {
    return { ok: false, version: "" };
  }
}

export async function listChats(base: string): Promise<ChatSummary[]> {
  const r = await fetch(`${base}/chats`);
  if (!r.ok) {
    throw new Error(await r.text());
  }
  const j = (await r.json()) as { chats: ChatSummary[] };
  return j.chats ?? [];
}

export async function loadChat(base: string, id: string): Promise<WireChat> {
  const r = await fetch(`${base}/chats/${encodeURIComponent(id)}`);
  if (!r.ok) {
    throw new Error(await r.text());
  }
  const j = (await r.json()) as { chat: WireChat };
  return j.chat;
}

export async function createChat(
  base: string,
  title: string,
  workspace: string,
): Promise<WireChat> {
  const r = await fetch(`${base}/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      meta: { workspace, client: "codeagents-gui" },
    }),
  });
  if (!r.ok) {
    throw new Error(await r.text());
  }
  const j = (await r.json()) as { chat: WireChat };
  return j.chat;
}

export async function uploadBase64(
  base: string,
  filename: string,
  contentBase64: string,
): Promise<string> {
  const r = await fetch(`${base}/chat/upload`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename,
      content_base64: contentBase64,
      subdir: "uploads",
    }),
  });
  if (!r.ok) {
    throw new Error(await r.text());
  }
  const j = (await r.json()) as { saved: string };
  return j.saved;
}

export type InferenceModel = {
  key: string;
  display_name: string;
  backend?: string;
  runtime_model: string;
  profile?: string;
  source?: string;
  notes?: string;
};

export async function listInferenceModels(base: string): Promise<InferenceModel[]> {
  const r = await fetch(`${base}/inference/models`);
  if (!r.ok) {
    throw new Error(await r.text());
  }
  const j = (await r.json()) as { models: InferenceModel[] };
  return j.models ?? [];
}

export type ContextUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  context_window: number;
};

// Phase 2.A.5 — pre-call token estimate served by GET /budget/preview.
export type BudgetPreview = {
  model: string;
  last_prompt_tokens: number;
  estimated_next: number;
  context_window: number;
  warn: boolean;
  calibration: {
    factor?: number;
    samples?: number;
    last_real?: number;
    last_estimate?: number;
  };
};

// Phase 2.B research types ────────────────────────────────────────
export type ResearchSection = {
  title: string;
  questions: string[];
  facts: Array<{ claim: string; span?: string; source_url?: string }>;
  draft: string;
  status: string;
};

export type ResearchReport = {
  version: number;
  id: string;
  chat_id: string;
  query: string;
  status: string;
  outline: ResearchSection[];
  sources: Array<{ url: string; ts?: number }>;
  clarify: {
    questions: string[];
    answers: Array<{ question: string; answer: string }>;
    skipped: boolean;
  };
  created_ts: number;
  updated_ts: number;
};

export async function listResearchReports(
  base: string,
  chatId: string,
): Promise<ResearchReport[]> {
  const r = await fetch(`${base}/research/${encodeURIComponent(chatId)}`);
  if (!r.ok) return [];
  const j = (await r.json()) as { reports?: ResearchReport[] };
  return j.reports ?? [];
}

export async function loadResearchReport(
  base: string,
  chatId: string,
  reportId: string,
): Promise<{ report: ResearchReport; markdown: string } | null> {
  const r = await fetch(
    `${base}/research/${encodeURIComponent(chatId)}/${encodeURIComponent(reportId)}`,
  );
  if (!r.ok) return null;
  return (await r.json()) as { report: ResearchReport; markdown: string };
}

export async function cancelResearchReport(
  base: string,
  chatId: string,
  reportId: string,
): Promise<boolean> {
  const r = await fetch(
    `${base}/research/${encodeURIComponent(chatId)}/${encodeURIComponent(reportId)}/cancel`,
    { method: "POST" },
  );
  return r.ok;
}

export async function fetchBudgetPreview(base: string): Promise<BudgetPreview | null> {
  try {
    const r = await fetch(`${base}/budget/preview`);
    if (!r.ok) return null;
    return (await r.json()) as BudgetPreview;
  } catch {
    return null;
  }
}

export type ToolParam = {
  name: string;
  type: string;
  description: string;
  required: boolean;
  enum: string[] | null;
};

export type ToolInfo = {
  name: string;
  description: string;
  permission: string;
  kind: string;
  parameters: ToolParam[];
};

export type ToolsByMode = { modes: Record<string, ToolInfo[]> };

export async function fetchTools(base: string): Promise<ToolsByMode | null> {
  try {
    const r = await fetch(`${base}/tools`);
    if (!r.ok) return null;
    return (await r.json()) as ToolsByMode;
  } catch {
    return null;
  }
}

export type ModeDescriptor = {
  name: string;
  tool_whitelist: string[];
  allowed_permissions: string[] | null;
  ui_color: string;
};

export type ModesPayload = { modes: ModeDescriptor[] };

export async function fetchModes(base: string): Promise<ModesPayload | null> {
  try {
    const r = await fetch(`${base}/modes`);
    if (!r.ok) return null;
    return (await r.json()) as ModesPayload;
  } catch {
    return null;
  }
}

export type PlanStepStatus = "pending" | "in_progress" | "done" | "skipped";
export type PlanStatus = "draft" | "building" | "completed" | "rejected";

export type PlanStep = {
  n: number;
  title: string;
  detail: string;
  status: PlanStepStatus;
  note: string;
};

export type Plan = {
  id: string;
  title: string;
  summary: string;
  steps: PlanStep[];
  status: PlanStatus;
  workspace: string;
  chat_id: string;
  created_at: string;
  updated_at: string;
};

export async function listPlans(
  base: string,
  opts: { status?: string; chatId?: string } = {},
): Promise<Plan[]> {
  const qs = new URLSearchParams();
  if (opts.status) qs.set("status", opts.status);
  if (opts.chatId) qs.set("chat_id", opts.chatId);
  const url = `${base}/plans${qs.toString() ? `?${qs}` : ""}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  const j = (await r.json()) as { plans: Plan[] };
  return j.plans ?? [];
}

export async function loadPlanMarkdown(base: string, id: string): Promise<string> {
  const r = await fetch(`${base}/plans/${encodeURIComponent(id)}/markdown`);
  if (!r.ok) throw new Error(await r.text());
  const j = (await r.json()) as { markdown: string };
  return j.markdown ?? "";
}

export async function rejectPlan(base: string, id: string): Promise<Plan> {
  const r = await fetch(`${base}/plans/${encodeURIComponent(id)}/reject`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(await r.text());
  const j = (await r.json()) as { plan: Plan };
  return j.plan;
}

export async function deletePlan(base: string, id: string): Promise<void> {
  const r = await fetch(`${base}/plans/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!r.ok && r.status !== 404) throw new Error(await r.text());
}

export async function deleteChat(base: string, id: string): Promise<void> {
  const r = await fetch(`${base}/chats/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!r.ok && r.status !== 404) {
    throw new Error(await r.text());
  }
}

export async function renameChat(
  base: string,
  id: string,
  title: string,
): Promise<WireChat> {
  const r = await fetch(`${base}/chats/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!r.ok) {
    throw new Error(await r.text());
  }
  const j = (await r.json()) as { chat: WireChat };
  return j.chat;
}

export async function requestAutoTitle(
  base: string,
  id: string,
  prompt: string,
  model?: string,
): Promise<string> {
  const body: Record<string, unknown> = { prompt };
  if (model) {
    body.model = model;
  }
  const r = await fetch(`${base}/chats/${encodeURIComponent(id)}/title`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    throw new Error(await r.text());
  }
  const j = (await r.json()) as { title: string };
  return j.title;
}

export async function confirmTool(
  base: string,
  decisionId: string,
  approved: boolean,
  remember: boolean,
): Promise<void> {
  const r = await fetch(`${base}/chat/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      decision_id: decisionId,
      approved,
      remember,
    }),
  });
  if (!r.ok) {
    throw new Error(await r.text());
  }
}

export function nextMessageIndex(messages: WireMessage[]): number {
  if (messages.length === 0) {
    return 0;
  }
  return Math.max(...messages.map((m) => m.index)) + 1;
}

/** Build ``POST /chat/stream`` JSON body (matches Rust ``StructuredChatPayload``). */
export function streamRequestBody(
  chat: WireChat,
  task: string,
  workspace: string,
  mode?: string,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    chat: {
      id: chat.id ?? null,
      messages: chat.messages,
      meta: chat.meta,
    },
    task,
    workspace,
  };
  if (mode) {
    body.mode = mode;
  }
  return body;
}

export async function postChatStream(
  base: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<ReadableStream<Uint8Array> | null> {
  const r = await fetch(`${base}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  return r.body;
}

export type MetricsSnapshot = {
  t: number;
  cpu_percent: number;
  rss_mb: number;
  ram_total_mb: number;
  ram_used_percent: number;
  gpu: {
    ok: boolean;
    gpus: Array<{
      name: string;
      memory_used_mb: number;
      memory_total_mb: number;
      utilization_percent: number;
    }>;
  };
  ollama_ps: {
    ok: boolean;
    models: Array<{ name?: string; model?: string; size?: number }>;
  };
};

export async function fetchMetricsHistory(
  base: string,
): Promise<MetricsSnapshot[]> {
  try {
    const r = await fetch(`${base}/metrics/history`);
    if (!r.ok) return [];
    const j = (await r.json()) as { samples?: MetricsSnapshot[] };
    return j.samples ?? [];
  } catch {
    return [];
  }
}

/** Subscribe to ``GET /metrics/stream`` (NDJSON, one snapshot per line).
 *  Returns an ``unsubscribe`` callback. The connection auto-reconnects
 *  on transient failures with a small backoff. */
export function subscribeMetrics(
  base: string,
  onSnapshot: (snap: MetricsSnapshot) => void,
): () => void {
  const controller = new AbortController();
  let stopped = false;

  async function loop(): Promise<void> {
    let backoff = 500;
    while (!stopped) {
      try {
        const r = await fetch(`${base}/metrics/stream`, {
          signal: controller.signal,
        });
        if (!r.ok || !r.body) {
          throw new Error(`HTTP ${r.status}`);
        }
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        backoff = 500;
        while (!stopped) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let idx = buf.indexOf("\n");
          while (idx >= 0) {
            const line = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 1);
            if (line) {
              try {
                onSnapshot(JSON.parse(line) as MetricsSnapshot);
              } catch {
                // ignore malformed line
              }
            }
            idx = buf.indexOf("\n");
          }
        }
      } catch {
        if (stopped) return;
        await new Promise((r) => setTimeout(r, backoff));
        backoff = Math.min(backoff * 2, 5000);
      }
    }
  }

  void loop();
  return () => {
    stopped = true;
    controller.abort();
  };
}

export async function refreshIndex(base: string): Promise<{ refreshed: boolean }> {
  try {
    const r = await fetch(`${base}/index/refresh`, { method: "POST" });
    if (!r.ok) return { refreshed: false };
    return (await r.json()) as { refreshed: boolean };
  } catch {
    return { refreshed: false };
  }
}
