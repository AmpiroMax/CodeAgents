import type { Plan } from "../lib/api";

/** Active plan column pinned to the right edge of the chat.
 *
 * Each card shows the plan title, the running ``done/total`` step count and a
 * ``Build`` / ``Continue`` button directly below the title (the user wants the
 * step count to read as "{n} Build", so the count sits inside the button label).
 *
 * The whole strip is positioned absolutely (see App.css) and rendered with a
 * translucent background so it floats above the timeline — including any tool
 * cards — without obscuring the conversation underneath. */
export function PlanBanner({
  plans,
  building,
  onBuild,
  onView,
  onDismiss,
}: {
  plans: Plan[];
  building: string | null;
  onBuild: (plan: Plan) => void;
  onView: (plan: Plan) => void;
  onDismiss: (plan: Plan) => void;
}) {
  if (plans.length === 0) return null;
  return (
    <aside className="plan-banner" role="region" aria-label="Active plans">
      {plans.map((plan) => {
        const total = plan.steps.length;
        const done = plan.steps.filter(
          (s) => s.status === "done" || s.status === "skipped",
        ).length;
        const inProgress = building === plan.id;
        const started = done > 0 || plan.status === "building";
        const action = inProgress
          ? "Running…"
          : started
            ? "Continue"
            : "Build";
        return (
          <article className="plan-card" key={plan.id} data-status={plan.status}>
            <button
              className="plan-card-body"
              onClick={() => onView(plan)}
              type="button"
              title={plan.title}
            >
              <div className="plan-card-title">{plan.title}</div>
              <div className="plan-card-meta">{plan.status}</div>
            </button>
            <div className="plan-card-actions">
              <button
                className="plan-build-btn"
                disabled={inProgress}
                onClick={() => onBuild(plan)}
                type="button"
              >
                <span className="plan-build-count">
                  {done}/{total}
                </span>
                <span className="plan-build-label">{action}</span>
              </button>
              <button
                aria-label="Dismiss plan"
                className="plan-dismiss-btn"
                onClick={() => onDismiss(plan)}
                title="Dismiss (mark rejected)"
                type="button"
              >
                ×
              </button>
            </div>
          </article>
        );
      })}
    </aside>
  );
}
