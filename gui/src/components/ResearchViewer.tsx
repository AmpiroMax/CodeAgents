import { useEffect, useMemo, useRef, useState } from "react";
import {
  cancelResearchReport,
  loadResearchReport,
  type ResearchReport,
} from "../lib/api";

/** Full-screen viewer for a single deep-research report.
 *
 * The clarify form is rendered when ``status === 'awaiting_clarify'``;
 * a final-markdown panel is rendered when status is ``done`` or ``cancelled``;
 * everything else shows a per-section live progress view.
 */
export function ResearchViewer({
  base,
  chatId,
  reportId,
  onClose,
  onSubmitClarify,
}: {
  base: string;
  chatId: string;
  reportId: string;
  onClose: () => void;
  /** Called when the user types answers and clicks Submit (or Skip).
   *  The parent then sends a synthetic user turn so the agent picks up
   *  the answers via ``submit_clarify_answers``. */
  onSubmitClarify: (
    payload:
      | { reportId: string; answers: Array<{ question: string; answer: string }> }
      | { reportId: string; skipped: true },
  ) => void;
}) {
  const [report, setReport] = useState<ResearchReport | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [answers, setAnswers] = useState<string[]>([]);
  // Seed the answers array exactly once per (reportId, question count).
  // Using a ref prevents the polling tick from re-seeding (and thus
  // wiping) what the user is typing.
  const seededRef = useRef<string>("");
  // Pause polling while the user is editing answers — otherwise React
  // re-renders the inputs on every refetch and (combined with stale
  // controlled values) eats keystrokes.
  const editingRef = useRef<boolean>(false);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (editingRef.current) return;
      const data = await loadResearchReport(base, chatId, reportId);
      if (cancelled || !data) return;
      setReport(data.report);
      setMarkdown(data.markdown);
      const qs = data.report.clarify?.questions ?? [];
      const seedKey = `${reportId}:${qs.length}`;
      if (qs.length > 0 && seededRef.current !== seedKey) {
        seededRef.current = seedKey;
        setAnswers(qs.map(() => ""));
      }
    };
    void tick();
    const handle = window.setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [base, chatId, reportId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const sections = useMemo(() => report?.outline ?? [], [report]);

  if (!report) {
    return (
      <div className="research-viewer-overlay" onClick={onClose}>
        <div className="research-viewer" onClick={(e) => e.stopPropagation()}>
          <div className="research-viewer-loading">loading report…</div>
        </div>
      </div>
    );
  }

  return (
    <div className="research-viewer-overlay" onClick={onClose}>
      <div className="research-viewer" onClick={(e) => e.stopPropagation()}>
        <header className="research-viewer-header">
          <div>
            <div className="research-viewer-title">{report.query}</div>
            <div className="research-viewer-status">
              status: {report.status} · {sections.length} sections ·{" "}
              {report.sources.length} sources
            </div>
          </div>
          <div className="research-viewer-actions">
            {report.status !== "done" && report.status !== "cancelled" ? (
              <button
                type="button"
                onClick={() => void cancelResearchReport(base, chatId, reportId)}
              >
                Cancel
              </button>
            ) : null}
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </header>

        {report.status === "awaiting_clarify" ? (
          <section className="research-viewer-clarify">
            <h3>Clarifying questions</h3>
            <p className="research-viewer-hint">
              Answer 3–4 short questions so the agent can plan a better
              outline. You can also skip.
            </p>
            <div className="research-viewer-clarify-list">
              {report.clarify.questions.map((q, i) => (
                <label key={i} className="research-viewer-clarify-row">
                  <span>{q}</span>
                  <input
                    type="text"
                    value={answers[i] ?? ""}
                    onFocus={() => {
                      editingRef.current = true;
                    }}
                    onBlur={() => {
                      editingRef.current = false;
                    }}
                    onChange={(e) => {
                      const next = [...answers];
                      next[i] = e.target.value;
                      setAnswers(next);
                    }}
                  />
                </label>
              ))}
            </div>
            <div className="research-viewer-clarify-actions">
              <button
                type="button"
                onClick={() =>
                  onSubmitClarify({
                    reportId,
                    answers: report.clarify.questions
                      .map((q, i) => ({ question: q, answer: answers[i] || "" }))
                      .filter((p) => p.answer.trim()),
                  })
                }
                disabled={!answers.some((a) => a.trim())}
              >
                Submit
              </button>
              <button
                type="button"
                onClick={() => onSubmitClarify({ reportId, skipped: true })}
              >
                Skip
              </button>
            </div>
          </section>
        ) : null}

        <section className="research-viewer-outline">
          <h3>Outline</h3>
          {sections.length === 0 ? (
            <div className="research-viewer-empty">no outline yet</div>
          ) : (
            <ol>
              {sections.map((s, i) => (
                <li key={i}>
                  <strong>{s.title}</strong>
                  <span className="research-viewer-section-status">
                    {" · "}
                    {s.status}
                    {s.facts?.length ? ` · ${s.facts.length} facts` : ""}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>

        {markdown ? (
          <section className="research-viewer-markdown">
            <h3>Final report</h3>
            <pre className="research-viewer-markdown-body">{markdown}</pre>
          </section>
        ) : null}

        {report.sources.length > 0 ? (
          <section className="research-viewer-sources">
            <h3>Sources</h3>
            <ol>
              {report.sources.map((s, i) => (
                <li key={i}>
                  <a href={s.url} target="_blank" rel="noreferrer">
                    {s.url}
                  </a>
                </li>
              ))}
            </ol>
          </section>
        ) : null}
      </div>
    </div>
  );
}
