import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppProviders } from "./components/AppProviders";
import { ChatTimeline } from "./components/ChatTimeline";
import { CommandPalette, type PaletteCommand } from "./components/CommandPalette";
import { Composer } from "./components/Composer";
import { ExitConfirm } from "./components/ExitConfirm";
import { PermissionDialog } from "./components/PermissionDialog";
import { PlanBanner } from "./components/PlanBanner";
import { PlanDismissConfirm } from "./components/PlanDismissConfirm";
import { PlanViewer } from "./components/PlanViewer";
import { SessionHeader } from "./components/SessionHeader";
import { StatusBar } from "./components/StatusBar";
import { useTheme, type ThemeSetting } from "./design-system/ThemeProvider";
import { usePersistentState } from "./hooks/usePersistentState";
import {
  type ChatSummary,
  type ContextUsage,
  type InferenceModel,
  type WireChat,
  type WireContent,
  type WireMessage,
  confirmTool,
  createChat,
  defaultApiBase,
  deleteChat,
  fetchHealth,
  listChats,
  listInferenceModels,
  listPlans,
  loadChat,
  nextMessageIndex,
  postChatStream,
  rejectPlan,
  renameChat,
  requestAutoTitle,
  streamRequestBody,
  uploadBase64,
  type Plan,
} from "./lib/api";
import { readNdjsonStream, type StreamRow } from "./lib/ndjson";
import {
  appendStreamRow,
  contextUsageFromRow,
  pendingToolFromItem,
  wireMessagesToTimeline,
  type PendingTool,
  type TimelineItem,
} from "./lib/timeline";

const MODES = ["agent", "plan", "ask"] as const;
type Mode = (typeof MODES)[number];

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function AppInner() {
  const apiBase = useMemo(() => defaultApiBase(), []);
  // Mode intentionally NOT persisted: every fresh launch starts in ``agent``
  // mode (the safe-default for "do something for me"). Users press Tab to
  // switch within a session.
  const [mode, setMode] = useState<Mode>("agent");
  const [selectedModel, setSelectedModel] = usePersistentState<string>(
    "codeagents.model",
    "",
  );
  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  // Backend build version (e.g. "3.0.0") — surfaced in StatusBar in place
  // of the old "api ready" string. Empty until the first /health succeeds.
  const [serverVersion, setServerVersion] = useState<string>("");
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [models, setModels] = useState<InferenceModel[]>([]);
  const [active, setActive] = useState<WireChat | null>(null);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [streaming, setStreaming] = useState(false);
  // Mode the currently-streaming response was triggered with. Captured at
  // send-time so toggling Tab mid-response doesn't recolour this turn's
  // progress bar.
  const [streamingMode, setStreamingMode] = useState<Mode>("agent");
  const [streamItems, setStreamItems] = useState<TimelineItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [exitOpen, setExitOpen] = useState(false);
  const [shuttingDown, setShuttingDown] = useState(false);
  // Plans subsystem state.
  const [plans, setPlans] = useState<Plan[]>([]);
  const [planViewer, setPlanViewer] = useState<Plan | null>(null);
  const [planDismiss, setPlanDismiss] = useState<Plan | null>(null);
  const [buildingPlanId, setBuildingPlanId] = useState<string | null>(null);
  // Full archive — completed + rejected too. Loaded lazily for the
  // "Plans" submenu of the command palette; the banner only ever needs
  // the active subset.
  const [allPlans, setAllPlans] = useState<Plan[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const theme = useTheme();

  const base = apiBase.trim().replace(/\/$/, "");

  const timelineItems = useMemo(
    () => [
      ...wireMessagesToTimeline(active?.messages ?? [], active?.meta),
      ...streamItems,
    ],
    [active?.messages, active?.meta, streamItems],
  );

  const pendingTool = useMemo<PendingTool | null>(() => {
    for (let i = timelineItems.length - 1; i >= 0; i -= 1) {
      const pending = pendingToolFromItem(timelineItems[i]!);
      if (pending) {
        return pending;
      }
    }
    return null;
  }, [timelineItems]);

  const canSend = useMemo(
    () => (input.trim().length > 0 || files.length > 0) && !streaming,
    [input, files.length, streaming],
  );

  const ping = useCallback(async () => {
    try {
      const info = await fetchHealth(base);
      setHealthOk(info.ok);
      setServerVersion(info.version);
    } catch {
      setHealthOk(false);
      setServerVersion("");
    }
  }, [base]);

  const refreshChats = useCallback(async () => {
    const list = await listChats(base);
    setChats(list);
  }, [base]);

  const refreshPlans = useCallback(async () => {
    try {
      const full = await listPlans(base, { status: "all" });
      setAllPlans(full);
      const ACTIVE = new Set(["draft", "building"]);
      const activeId = active?.id ?? "";
      // The right-side banner is per-chat: only show plans owned by the chat
      // currently in focus. Without an active chat (the "Start a session"
      // placeholder) the banner stays empty so plans from other chats don't
      // bleed in. The command palette's "Plans" submenu still uses
      // ``allPlans`` for cross-chat browsing.
      setPlans(
        full.filter(
          (p) => ACTIVE.has(p.status) && (p.chat_id || "") === activeId,
        ),
      );
    } catch {
      setPlans([]);
      setAllPlans([]);
    }
  }, [active?.id, base]);

  const refreshModels = useCallback(async () => {
    try {
      setModels(await listInferenceModels(base));
    } catch {
      setModels([]);
    }
  }, [base]);

  useEffect(() => {
    void ping();
  }, [ping]);

  useEffect(() => {
    void refreshChats().catch(() => setChats([]));
  }, [refreshChats]);

  useEffect(() => {
    void refreshModels();
  }, [refreshModels]);

  useEffect(() => {
    void refreshPlans();
  }, [refreshPlans]);

  const openChat = useCallback(
    async (id: string) => {
      setError(null);
      setStreamItems([]);
      setContextUsage(null);
      try {
        setActive(await loadChat(base, id));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [base],
  );

  const createNewChat = useCallback(async (): Promise<WireChat | null> => {
    setError(null);
    try {
      const placeholder = `Chat ${new Date().toISOString().slice(0, 19)}`;
      const chat = await createChat(base, placeholder, "");
      setActive(chat);
      setStreamItems([]);
      setContextUsage(null);
      await refreshChats();
      return chat;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, [base, refreshChats]);

  const ensureChat = useCallback(async (): Promise<WireChat | null> => {
    return active ?? (await createNewChat());
  }, [active, createNewChat]);

  const stopStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  const applyStreamRow = useCallback(
    (row: StreamRow) => {
      const usage = contextUsageFromRow(row);
      if (usage) {
        setContextUsage(usage);
        return;
      }
      setStreamItems((items) => appendStreamRow(items, row));
      if (row.type === "error") {
        setError(String(row.message ?? "stream error"));
      }
      // Plan tools mutate plan_store on the backend → re-pull the banner so
      // the live "n/m" count + per-step status reflect agent progress.
      const name = String(row.name ?? "");
      if (
        (row.type === "tool_result" || row.type === "tool_call") &&
        (name === "create_plan" ||
          name === "patch_plan" ||
          name === "mark_step")
      ) {
        void refreshPlans();
      }
    },
    [refreshPlans],
  );

  const handleAutoTitle = useCallback(
    async (chatId: string, prompt: string) => {
      try {
        await requestAutoTitle(base, chatId, prompt, selectedModel || undefined);
        const updated = await loadChat(base, chatId);
        setActive((current) =>
          current && current.id === chatId ? updated : current,
        );
        await refreshChats();
      } catch {
        // Naming is best-effort; never surface to the user.
      }
    },
    [base, refreshChats, selectedModel],
  );

  const runStream = useCallback(
    async (
      chat: WireChat,
      isFirstUserMessage: boolean,
      firstPrompt: string,
      modeOverride?: Mode,
    ) => {
      // Build/Continue actions need to run the turn in agent mode regardless
      // of the picker state. Don't read ``mode`` from the closure here — the
      // caller knows which mode this turn really belongs to.
      const effectiveMode: Mode = modeOverride ?? mode;
      setStreaming(true);
      setStreamingMode(effectiveMode);
      setStreamItems([]);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        // Workspace stays empty; backend falls back to $HOME.
        // Pass selected model name as ``task`` so ModelRouter routes through
        // it via its "direct Ollama model" branch; falls back to "code".
        const taskOrModel = selectedModel.trim() || "code";
        const body = streamRequestBody(chat, taskOrModel, "", effectiveMode);
        const stream = await postChatStream(base, body, controller.signal);
        for await (const row of readNdjsonStream(stream)) {
          applyStreamRow(row);
        }
        if (chat.id) {
          setActive(await loadChat(base, chat.id));
          setStreamItems([]);
        }
        await refreshChats();
        if (isFirstUserMessage && chat.id) {
          void handleAutoTitle(chat.id, firstPrompt);
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        abortRef.current = null;
        setStreaming(false);
      }
    },
    [applyStreamRow, base, handleAutoTitle, mode, refreshChats, selectedModel],
  );

  const sendMessage = useCallback(async () => {
    setError(null);
    const chat = await ensureChat();
    if (!chat) {
      return;
    }

    const content: WireContent[] = [];
    const trimmed = input.trim();
    if (trimmed) {
      content.push({ type: "text", text: trimmed });
    }
    for (const file of files) {
      try {
        const saved = await uploadBase64(base, file.name, await fileToBase64(file));
        content.push({
          type: "file",
          file: { path: saved, media_type: file.type || "application/octet-stream" },
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    if (content.length === 0) {
      return;
    }

    const isFirstUserMessage = !chat.messages.some(
      (message) => message.role === "user",
    );
    const userIndex = nextMessageIndex(chat.messages);
    const userMsg: WireMessage = {
      role: "user",
      index: userIndex,
      content,
    };
    // Persist the active mode for this specific user message so the timeline
    // can colour the left border accordingly. Stored on chat meta because
    // ``WireMessage`` itself doesn't carry per-message metadata.
    const prevModes =
      (chat.meta?.message_modes as Record<string, string> | undefined) ?? {};
    const nextChat: WireChat = {
      id: chat.id,
      messages: [...chat.messages, userMsg],
      meta: {
        ...chat.meta,
        message_modes: { ...prevModes, [String(userIndex)]: mode },
      },
    };
    setActive(nextChat);
    setInput("");
    setFiles([]);
    await runStream(nextChat, isFirstUserMessage, trimmed || files[0]?.name || "");
  }, [base, ensureChat, files, input, runStream]);

  const confirmPendingTool = useCallback(
    async (approved: boolean) => {
      if (!pendingTool) {
        return;
      }
      try {
        await confirmTool(base, pendingTool.decision_id, approved, false);
        setStreamItems((items) =>
          items.map((item) =>
            item.kind === "tool" && item.decisionId === pendingTool.decision_id
              ? { ...item, status: approved ? "running" : "error" }
              : item,
          ),
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [base, pendingTool],
  );

  const handleBuildPlan = useCallback(
    async (plan: Plan) => {
      // We do NOT add a new HTTP endpoint for "build" — instead we drop a
      // user message into the active chat that tells the agent to execute
      // the plan. The agent's system prompt already gets the
      // "Plan execution" addendum when an active plan exists, so this just
      // gives the model an explicit trigger.
      let chat = active;
      if (!chat) {
        chat = await createNewChat();
        if (!chat) return;
      }
      const done = plan.steps.filter(
        (s) => s.status === "done" || s.status === "skipped",
      ).length;
      const text =
        done === 0
          ? `Build the plan "${plan.title}" (plan_id=${plan.id}). Execute every step in order. Before each step call mark_step(plan_id, step_n, status="in_progress"); when done call status="done". Do not stop until all ${plan.steps.length} steps are done.`
          : `Continue the plan "${plan.title}" (plan_id=${plan.id}). ${done} of ${plan.steps.length} steps already completed. Resume from the first step whose status is not "done" or "skipped" and finish all remaining steps. Use mark_step on every transition.`;
      const userIndex = nextMessageIndex(chat.messages);
      const prevModes =
        (chat.meta?.message_modes as Record<string, string> | undefined) ?? {};
      const userMsg: WireMessage = {
        role: "user",
        index: userIndex,
        content: [{ type: "text", text }],
      };
      const nextChat: WireChat = {
        id: chat.id,
        messages: [...chat.messages, userMsg],
        meta: {
          ...chat.meta,
          message_modes: { ...prevModes, [String(userIndex)]: "agent" },
        },
      };
      setActive(nextChat);
      setMode("agent");
      setBuildingPlanId(plan.id);
      try {
        // Force agent mode even if the picker is still on plan/ask — the
        // setMode call above only takes effect on the next render, while
        // runStream's closure already has the previous ``mode``.
        await runStream(nextChat, false, text, "agent");
      } finally {
        setBuildingPlanId(null);
        void refreshPlans();
      }
    },
    [active, createNewChat, runStream, refreshPlans],
  );

  const handleDismissPlan = useCallback(
    async (plan: Plan) => {
      try {
        await rejectPlan(base, plan.id);
        await refreshPlans();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [base, refreshPlans],
  );

  const handleDeleteChat = useCallback(
    async (id: string) => {
      try {
        await deleteChat(base, id);
        if (active?.id === id) {
          setActive(null);
          setStreamItems([]);
          setContextUsage(null);
        }
        await refreshChats();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [active?.id, base, refreshChats],
  );

  const handleRenameChat = useCallback(async () => {
    if (!active?.id) {
      return;
    }
    const current =
      (typeof active.meta?.title === "string" && active.meta.title) || "";
    const next = window.prompt("Rename chat", current);
    if (!next || next.trim() === current) {
      return;
    }
    try {
      const updated = await renameChat(base, active.id, next.trim());
      setActive(updated);
      await refreshChats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [active, base, refreshChats]);

  // Tab cycles the mode globally — including while typing in the composer
  // textarea. We always preventDefault so focus stays put (otherwise Tab
  // would jump to the next focusable button and the user would lose the
  // text cursor mid-sentence). Skip only when the palette is open.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      if (paletteOpen) {
        return;
      }
      // Allow Shift+Tab to behave normally (focus traversal backwards) so
      // power users still have a way out of the textarea if they want.
      if (event.shiftKey) {
        return;
      }
      event.preventDefault();
      setMode((current) => {
        const idx = MODES.indexOf(current as Mode);
        const nextIndex = (idx + 1 + MODES.length) % MODES.length;
        return MODES[nextIndex]!;
      });
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [paletteOpen, setMode]);

  // Cmd/Ctrl + K (and Ctrl+P) open the palette anywhere.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const isPalette =
        ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") ||
        (event.ctrlKey && event.key.toLowerCase() === "p");
      if (!isPalette) {
        return;
      }
      event.preventDefault();
      setPaletteOpen((current) => !current);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Esc anywhere outside an open modal opens an Exit? confirmation.
  // The dialog itself owns Esc/Enter/Arrow handling once visible (see
  // ExitConfirm), so we only react to the *first* Esc here.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }
      if (paletteOpen || exitOpen || planViewer || planDismiss) {
        // Sub-modals own their own Esc handling.
        return;
      }
      // If the user is in the middle of a streaming reply, treat the first
      // Esc as a stream stop (familiar terminal behaviour) and only the
      // second Esc opens the exit dialog.
      if (streaming) {
        event.preventDefault();
        stopStream();
        return;
      }
      event.preventDefault();
      setExitOpen(true);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [exitOpen, paletteOpen, planViewer, planDismiss, stopStream, streaming]);

  const handleExit = useCallback(() => {
    setExitOpen(false);
    // Show a "shutting down" overlay so the user knows the click was
    // accepted while the Swift launcher tears down ``ca-services``.
    setShuttingDown(true);
    // Cut any in-flight stream so we don't fight the launcher's stop hook.
    abortRef.current?.abort();

    // Preferred path: Swift launcher's JS bridge → NSApp.terminate(nil).
    // Fallback to window.close() for plain browser use.
    if (typeof window !== "undefined") {
      const bridge = (
        window as unknown as {
          webkit?: {
            messageHandlers?: {
              codeagents?: { postMessage: (data: unknown) => void };
            };
          };
        }
      ).webkit?.messageHandlers?.codeagents;
      if (bridge && typeof bridge.postMessage === "function") {
        bridge.postMessage({ action: "quit" });
      } else {
        window.close();
      }
    }
    // Hard-stop fallback in case the host doesn't tear the page down. After
    // 6s we just hide the overlay so the user can manually quit if needed.
    window.setTimeout(() => {
      setShuttingDown(false);
    }, 6000);
  }, []);

  const activeTitle =
    (typeof active?.meta?.title === "string" && active.meta.title) ||
    active?.id ||
    "New session";

  const chatCommands = useCallback((): PaletteCommand[] => {
    if (chats.length === 0) {
      return [{ id: "no-chats", label: "(no chats yet)", action: () => {} }];
    }
    return chats.map((chat) => ({
      id: `open-${chat.id}`,
      label: chat.title || chat.id,
      hint: `${chat.message_count} msg`,
      secondaryHint: "⌘D delete",
      action: () => void openChat(chat.id),
      onSecondary: () => {
        if (window.confirm(`Delete chat "${chat.title || chat.id}"?`)) {
          void handleDeleteChat(chat.id);
        }
      },
    }));
  }, [chats, handleDeleteChat, openChat]);

  const modeCommands = useCallback((): PaletteCommand[] =>
    MODES.map((candidate) => ({
      id: `mode-${candidate}`,
      label: candidate,
      hint: candidate === mode ? "current" : undefined,
      action: () => setMode(candidate),
    })),
  [mode, setMode]);

  const themeCommands = useCallback((): PaletteCommand[] =>
    (["dark", "light", "auto"] as const).map((candidate) => ({
      id: `theme-${candidate}`,
      label: candidate,
      hint: candidate === theme.themeSetting ? "current" : undefined,
      action: () => theme.setThemeSetting(candidate as ThemeSetting),
    })),
  [theme]);

  const modelCommands = useCallback((): PaletteCommand[] => {
    const items: PaletteCommand[] = [
      {
        id: "model-default",
        label: "default (router decides)",
        hint: selectedModel === "" ? "current" : undefined,
        action: () => setSelectedModel(""),
      },
    ];
    for (const model of models) {
      const id = model.runtime_model || model.key;
      items.push({
        id: `model-${id}`,
        label: model.display_name || id,
        hint:
          selectedModel === id
            ? "current"
            : model.backend
              ? model.backend
              : undefined,
        action: () => setSelectedModel(id),
      });
    }
    return items;
  }, [models, selectedModel, setSelectedModel]);

  const planCommands = useMemo<PaletteCommand[]>(() => {
    const groups: { label: string; statuses: string[] }[] = [
      { label: "Active", statuses: ["draft", "building"] },
      { label: "Completed", statuses: ["completed"] },
      { label: "Rejected", statuses: ["rejected"] },
    ];
    const cmds: PaletteCommand[] = [];
    // Refresh-on-open by also surfacing a re-fetch action — useful while
    // the agent edits plans in the background.
    cmds.push({
      id: "plans-refresh",
      label: "Refresh plan list",
      action: () => void refreshPlans(),
    });
    for (const group of groups) {
      const inGroup = allPlans.filter((p) => group.statuses.includes(p.status));
      if (inGroup.length === 0) continue;
      const groupItems = inGroup.map((plan) => {
        const done = plan.steps.filter(
          (s) => s.status === "done" || s.status === "skipped",
        ).length;
        return {
          id: `plan-${plan.id}`,
          label: plan.title || "(untitled)",
          hint: `${done}/${plan.steps.length} · ${plan.status}`,
          action: () => setPlanViewer(plan),
        } as PaletteCommand;
      });
      cmds.push({
        id: `plans-group-${group.label.toLowerCase()}`,
        label: group.label,
        hint: `${inGroup.length}`,
        children: () => groupItems,
      });
    }
    return cmds;
  }, [allPlans, refreshPlans]);

  const paletteCommands = useMemo<PaletteCommand[]>(() => {
    const cmds: PaletteCommand[] = [
      {
        id: "new-chat",
        label: "New chat",
        hint: "create empty session",
        action: () => void createNewChat(),
      },
      {
        id: "open-chat",
        label: "Open chat",
        hint: `${chats.length} saved`,
        children: chatCommands,
      },
      {
        id: "switch-mode",
        label: "Switch mode",
        hint: `current: ${mode}`,
        children: modeCommands,
      },
      {
        id: "switch-model",
        label: "Switch model",
        hint: selectedModel || "default",
        children: modelCommands,
      },
      {
        id: "switch-theme",
        label: "Theme",
        hint: theme.themeSetting,
        children: themeCommands,
      },
      {
        id: "plans",
        label: "Plans",
        hint: `${allPlans.length} stored`,
        children: () => planCommands,
      },
    ];
    if (active?.id) {
      cmds.push({
        id: "rename-chat",
        label: "Rename current chat",
        action: () => void handleRenameChat(),
      });
      cmds.push({
        id: "delete-chat",
        label: "Delete current chat",
        hint: "removes from disk",
        action: () => {
          if (window.confirm("Delete this chat?")) {
            void handleDeleteChat(active.id!);
          }
        },
      });
    }
    return cmds;
  }, [
    active?.id,
    allPlans.length,
    chatCommands,
    chats.length,
    createNewChat,
    handleDeleteChat,
    handleRenameChat,
    mode,
    modeCommands,
    modelCommands,
    planCommands,
    selectedModel,
    theme.themeSetting,
    themeCommands,
  ]);

  return (
    <div className="desktop-shell" data-mode={mode}>
      <main className="session-shell">
        {/* Drag strip is rendered by the native Swift launcher (a real
            NSView with mouseDownCanMoveWindow=true). We only render chat
            content inside the WebView. */}
        <SessionHeader
          activeTitle={activeTitle}
          onOpenPalette={() => setPaletteOpen(true)}
          onStop={stopStream}
          streaming={streaming}
        />
        <div className="session-workspace">
          <PlanBanner
            building={buildingPlanId}
            onBuild={(plan) => void handleBuildPlan(plan)}
            onDismiss={(plan) => setPlanDismiss(plan)}
            onView={(plan) => setPlanViewer(plan)}
            plans={plans}
          />
          <ChatTimeline
            items={timelineItems}
            onApproveTool={(decisionId) => {
              if (pendingTool?.decision_id === decisionId) {
                void confirmPendingTool(true);
              }
            }}
            onRejectTool={(decisionId) => {
              if (pendingTool?.decision_id === decisionId) {
                void confirmPendingTool(false);
              }
            }}
          />
        </div>
        <Composer
          canSend={canSend}
          error={error}
          files={files}
          input={input}
          mode={mode}
          onFilesChange={setFiles}
          onInputChange={setInput}
          onSend={() => void sendMessage()}
          streaming={streaming}
        />
        <StatusBar
          healthOk={healthOk}
          model={selectedModel || undefined}
          serverVersion={serverVersion}
          streaming={streaming}
          streamingMode={streamingMode}
          usage={contextUsage}
        />
      </main>
      <PermissionDialog
        onApprove={() => void confirmPendingTool(true)}
        onReject={() => void confirmPendingTool(false)}
        pending={pendingTool}
      />
      <CommandPalette
        commands={paletteCommands}
        onClose={() => setPaletteOpen(false)}
        open={paletteOpen}
      />
      <ExitConfirm
        onCancel={() => setExitOpen(false)}
        onExit={handleExit}
        open={exitOpen}
      />
      <PlanViewer
        base={base}
        onClose={() => setPlanViewer(null)}
        plan={planViewer}
      />
      <PlanDismissConfirm
        onCancel={() => setPlanDismiss(null)}
        onConfirm={(plan) => {
          setPlanDismiss(null);
          void handleDismissPlan(plan);
        }}
        plan={planDismiss}
      />
      {shuttingDown ? (
        <div className="shutdown-overlay" role="alert" aria-live="assertive">
          <div className="shutdown-card">
            <div className="status-progress" aria-hidden>
              <span className="dot dot-1" />
              <span className="dot dot-2" />
              <span className="dot dot-3" />
              <span className="dot dot-4" />
              <span className="dot dot-5" />
            </div>
            <div className="shutdown-label">Завершаем работу…</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function App() {
  return (
    <AppProviders>
      <AppInner />
    </AppProviders>
  );
}
