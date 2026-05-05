import type { CSSProperties } from "react";
import type { ContextUsage } from "../lib/api";

const MODE_COLORS: Record<string, string> = {
  agent: "#4ea1ff",
  ask: "#66d685",
  plan: "#ff9b3d",
};

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
}: {
  streaming: boolean;
  usage: ContextUsage | null;
  model?: string;
  healthOk: boolean | null;
  /** Mode the *outgoing* user message was sent under. The progress dots
   *  paint themselves in that colour so switching modes mid-response
   *  doesn't recolour the bar that belongs to the previous turn. */
  streamingMode?: string;
  /** Backend build version, surfaced in place of "api ready". */
  serverVersion?: string;
}) {
  const dotsStyle: CSSProperties | undefined =
    streamingMode && MODE_COLORS[streamingMode]
      ? ({ "--mode-accent": MODE_COLORS[streamingMode] } as CSSProperties)
      : undefined;
  const showUsage =
    usage && usage.context_window > 0 && usage.total_tokens >= 0;
  const ratio = showUsage
    ? Math.min(1, (usage!.total_tokens || 0) / (usage!.context_window || 1))
    : 0;
  const percent = showUsage ? Math.round(ratio * 100) : 0;
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
        {model ? <span className="status-model">model: {model}</span> : null}
        {showUsage ? (
          <span className={`status-context ${ratio > 0.85 ? "warn" : ""}`}>
            context: {percent}% ({formatTokens(usage!.total_tokens)} /{" "}
            {formatTokens(usage!.context_window)} tok)
          </span>
        ) : null}
      </div>
    </div>
  );
}
