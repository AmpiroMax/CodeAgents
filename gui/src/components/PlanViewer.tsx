import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { loadPlanMarkdown } from "../lib/api";
import type { Plan } from "../lib/api";

/** Full-screen overlay that renders the plan's Markdown. Esc closes
 *  it and returns to the chat (the chat keeps streaming in the
 *  background). Read-only for now — patches go through the agent. */
export function PlanViewer({
  base,
  plan,
  onClose,
}: {
  base: string;
  plan: Plan | null;
  onClose: () => void;
}) {
  const [markdown, setMarkdown] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!plan) {
      setMarkdown("");
      setError(null);
      return;
    }
    let cancelled = false;
    setMarkdown("");
    setError(null);
    loadPlanMarkdown(base, plan.id)
      .then((md) => {
        if (!cancelled) setMarkdown(md);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [base, plan]);

  useEffect(() => {
    if (!plan) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [plan, onClose]);

  if (!plan) return null;
  return (
    <div className="plan-viewer-backdrop" onClick={onClose}>
      <div
        className="plan-viewer"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`Plan: ${plan.title}`}
      >
        <header className="plan-viewer-header">
          <div>
            <div className="plan-viewer-title">{plan.title}</div>
            <div className="plan-viewer-meta">
              {plan.status} ·{" "}
              {
                plan.steps.filter(
                  (s) => s.status === "done" || s.status === "skipped",
                ).length
              }
              /{plan.steps.length} steps
            </div>
          </div>
          <button
            className="plan-viewer-close"
            onClick={onClose}
            type="button"
            aria-label="Close plan viewer"
          >
            esc
          </button>
        </header>
        <div className="plan-viewer-body md">
          {error ? (
            <pre className="plan-viewer-error">{error}</pre>
          ) : markdown ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
          ) : (
            <div className="plan-viewer-loading">Loading…</div>
          )}
        </div>
      </div>
    </div>
  );
}
