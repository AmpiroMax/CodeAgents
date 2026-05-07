import type { CSSProperties } from "react";
import type { BudgetPreview, ContextUsage } from "../lib/api";
import { MODE_COLORS } from "../lib/modeColors";

function formatTokens(value: number): string {
  if (value >= 1000) {
    const k = value / 1000;
    return `${k >= 10 ? Math.round(k) : k.toFixed(1)}k`;
  }
  return String(value);
}

export function StatusBar({
  streaming,
  usage,
  model,
  healthOk,
  streamingMode,
  serverVersion,
  modelLoading,
  budget,
}: {
  streaming: boolean;
  usage: ContextUsage | null;
  model?: string;
  healthOk: boolean | null;
  /** Predicted token count for the *next* turn (Phase 2.A.5).
   *  Polled via GET /budget/preview while the user is typing. */
  budget?: BudgetPreview | null;
  /** Mode the *outgoing* user message was sent under. The progress dots
   *  paint themselves in that colour so switching modes mid-response
   *  doesn't recolour the bar that belongs to the previous turn. */
  streamingMode?: string;
  /** Backend build version, surfaced in place of "api ready". */
  serverVersion?: string;
  /** When true, the badge next to the model name shows "loading…" — used
   *  while Ollama is pulling the model into VRAM/unified memory and isn't
   *  yet listed in ``/api/ps``. Driven by the metrics stream. */
  modelLoading?: boolean;
}) {
  const dotsStyle: CSSProperties | undefined =
    streamingMode && MODE_COLORS[streamingMode]
      ? ({ "--mode-accent": MODE_COLORS[streamingMode] } as CSSProperties)
      : undefined;
  const ctxWindow =
    (usage?.context_window && usage.context_window > 0
      ? usage.context_window
      : budget?.context_window || 0) || 0;
  const lastPrompt =
    (usage?.prompt_tokens && usage.prompt_tokens > 0
      ? usage.prompt_tokens
      : budget?.last_prompt_tokens || 0) || 0;
  const nextEst = budget?.estimated_next || 0;
  const showTokens = ctxWindow > 0 && (lastPrompt > 0 || nextEst > 0);
  const warn =
    ctxWindow > 0 &&
    Math.max(lastPrompt, nextEst) > ctxWindow * 0.85;
  return (
    <div className="status-bar" role="status" aria-live="polite">
      <div className="status-bar-left">
        {streaming ? (
          <div
            className="status-progress"
            aria-label="generating"
            style={dotsStyle}
          >
            <span className="dot dot-1" />
            <span className="dot dot-2" />
            <span className="dot dot-3" />
            <span className="dot dot-4" />
            <span className="dot dot-5" />
          </div>
        ) : (
          <span className={`status-dot ${healthOk === false ? "off" : ""}`}>
            {healthOk === null
              ? "checking…"
              : healthOk
                ? `v${serverVersion || "?"}`
                : "api offline"}
          </span>
        )}
      </div>
      <div className="status-bar-right">
        {model ? (
          <span className="status-model">
            model: {model}
            {modelLoading ? " · loading…" : ""}
          </span>
        ) : null}
        {showTokens ? (
          <span className={`status-context ${warn ? "warn" : ""}`}>
            tokens: last {formatTokens(lastPrompt)} · next ~
            {formatTokens(nextEst || lastPrompt)} / {formatTokens(ctxWindow)}
          </span>
        ) : null}
      </div>
    </div>
  );
}
