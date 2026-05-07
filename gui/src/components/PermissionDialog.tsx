import { Button } from "../design-system/primitives";
import type { PendingTool } from "../lib/timeline";

export function PermissionDialog({
  pending,
  onApprove,
  onReject,
}: {
  pending: PendingTool | null;
  onApprove: () => void;
  onReject: () => void;
}) {
  if (!pending) {
    return null;
  }
  return (
    <div className="dialog-backdrop">
      <div className="permission-dialog" role="dialog" aria-modal="true">
        <div className="dialog-eyebrow">permission required</div>
        <h2>{pending.name}</h2>
        <p>{pending.warning || "The agent wants to run this tool."}</p>
        <pre>{pending.arguments || "(no arguments)"}</pre>
        <div className="dialog-actions">
          <Button onClick={onReject} type="button" variant="default">
            Reject
          </Button>
          <Button onClick={onApprove} type="button" variant="primary">
            Approve
          </Button>
        </div>
      </div>
    </div>
  );
}
