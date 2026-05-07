import type { ResearchReport } from "../lib/api";

const STAGE_LABEL: Record<string, string> = {
  created: "starting",
  awaiting_clarify: "awaiting clarification",
  ready_to_plan: "ready to plan",
  planning: "planning outline",
  researching: "researching",
  drafting: "drafting",
  assembled: "assembling",
  done: "done",
  cancelled: "cancelled",
};

export function ResearchPreviewCard({
  report,
  onOpen,
}: {
  report: ResearchReport;
  onOpen: () => void;
}) {
  const stage = STAGE_LABEL[report.status] || report.status;
  const sectionCount = report.outline?.length || 0;
  const sourceCount = report.sources?.length || 0;
  const drafted = report.outline?.filter((s) => s.status === "drafted" || s.status === "done").length || 0;
  return (
    <button
      type="button"
      className="research-preview-card"
      onClick={onOpen}
      title="Open research report"
    >
      <div className="research-preview-card-title">
        Research report · {report.query.slice(0, 80)}
        {report.query.length > 80 ? "…" : ""}
      </div>
      <div className="research-preview-card-meta">
        <span>{stage}</span>
        <span>·</span>
        <span>
          {drafted}/{sectionCount} sections
        </span>
        <span>·</span>
        <span>{sourceCount} sources</span>
      </div>
    </button>
  );
}
