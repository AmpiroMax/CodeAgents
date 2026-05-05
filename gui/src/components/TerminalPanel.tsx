import { useMemo } from "react";
import { Button } from "../design-system/primitives";
import type { TimelineItem } from "../lib/timeline";

export function TerminalPanel({
  items,
  height,
  collapsed,
  onHeightChange,
  onCollapsedChange,
}: {
  items: TimelineItem[];
  height: number;
  collapsed: boolean;
  onHeightChange: (height: number) => void;
  onCollapsedChange: (collapsed: boolean) => void;
}) {
  const lines = useMemo(() => {
    return items
      .flatMap((item) => {
        if (item.kind === "terminal") {
          return [`[terminal ${item.sessionId || "default"}] ${item.chunk}`];
        }
        if (item.kind === "tool") {
          const state = `${item.name} ${item.status}`;
          const output = item.output ? `\n${item.output}` : "";
          return [`[tool] ${state}${output}`];
        }
        if (item.kind === "notice") {
          return [`[${item.level}] ${item.message}`];
        }
        return [];
      })
      .slice(-120);
  }, [items]);

  return (
    <section
      className={`terminal-panel ${collapsed ? "collapsed" : ""}`}
      style={{ height: collapsed ? 36 : height }}
    >
      <div
        className="terminal-resize"
        onPointerDown={(event) => {
          const startY = event.clientY;
          const startHeight = height;
          const onMove = (move: PointerEvent) => {
            onHeightChange(Math.min(520, Math.max(160, startHeight + startY - move.clientY)));
          };
          const onUp = () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
          };
          window.addEventListener("pointermove", onMove);
          window.addEventListener("pointerup", onUp);
        }}
      />
      <div className="terminal-header">
        <span>terminal / tool output</span>
        <Button onClick={() => onCollapsedChange(!collapsed)} type="button" variant="ghost">
          {collapsed ? "Expand" : "Collapse"}
        </Button>
      </div>
      {!collapsed ? (
        <pre className="terminal-body">
          {lines.length > 0 ? lines.join("\n\n") : "No tool or terminal output yet."}
        </pre>
      ) : null}
    </section>
  );
}
