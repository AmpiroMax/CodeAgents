import { useEffect, useLayoutEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { StatusIcon } from "../design-system/primitives";
import { summarizeToolArgs, type TimelineItem } from "../lib/timeline";

const STICK_THRESHOLD_PX = 80;
const PIN_TOP_OFFSET_PX = 12;

function Markdown({ source }: { source: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
    </div>
  );
}

function lastUserMessageId(items: TimelineItem[]): string | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === "message" && item.role === "user") {
      return item.id;
    }
  }
  return null;
}

export function ChatTimeline({
  items,
  onApproveTool,
  onRejectTool,
}: {
  items: TimelineItem[];
  onApproveTool: (decisionId: string) => void;
  onRejectTool: (decisionId: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Spacer at the very bottom of the timeline. We size it imperatively so
  // that, after a new user message arrives, there's always enough room
  // *below* that message for it to scroll up to the viewport's top edge —
  // even when the chat is short. Without this spacer, ``scrollHeight ==
  // clientHeight`` and our pin computation clamps to 0 (no movement).
  const tailRef = useRef<HTMLDivElement | null>(null);
  // When the user opens a chat from history we want the bottom in view; when
  // they send a new message we instead pin that message to the TOP of the
  // viewport (Cursor-style), so the assistant's response grows downward into
  // an empty area and the user can naturally scroll up to re-read context.
  const stickBottomRef = useRef(true);
  const pinnedUserIdRef = useRef<string | null>(null);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY < 0) {
        stickBottomRef.current = false;
      } else {
        const distanceFromBottom =
          node.scrollHeight - node.scrollTop - node.clientHeight;
        stickBottomRef.current = distanceFromBottom <= STICK_THRESHOLD_PX;
      }
    };
    const onScroll = () => {
      const distanceFromBottom =
        node.scrollHeight - node.scrollTop - node.clientHeight;
      stickBottomRef.current = distanceFromBottom <= STICK_THRESHOLD_PX;
    };
    node.addEventListener("wheel", onWheel, { passive: true });
    node.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      node.removeEventListener("wheel", onWheel);
      node.removeEventListener("scroll", onScroll);
    };
  }, []);

  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    const lastUserId = lastUserMessageId(items);
    const isNewPin = !!lastUserId && lastUserId !== pinnedUserIdRef.current;

    // Recompute the spacer on every items change while a user message is
    // pinned. The spacer height is "viewport − everything-below-the-pin"
    // (clamped to ≥0) so the pin can always be at the top.
    const sizeTail = () => {
      const tail = tailRef.current;
      if (!tail) return;
      const target = lastUserId
        ? node.querySelector<HTMLElement>(
            `[data-message-id="${CSS.escape(lastUserId)}"]`,
          )
        : null;
      if (!target) {
        tail.style.height = "0px";
        return;
      }
      const tRect = target.getBoundingClientRect();
      const cRect = node.getBoundingClientRect();
      const distFromTopOfPin = tRect.top - cRect.top + node.scrollTop;
      const totalContent = node.scrollHeight - tail.offsetHeight;
      const belowPin = totalContent - distFromTopOfPin - target.offsetHeight;
      const want = node.clientHeight - target.offsetHeight - belowPin - PIN_TOP_OFFSET_PX;
      tail.style.height = `${Math.max(0, want)}px`;
    };

    if (isNewPin) {
      pinnedUserIdRef.current = lastUserId;
      stickBottomRef.current = false;
    }

    sizeTail();

    if (isNewPin) {
      const target = node.querySelector<HTMLElement>(
        `[data-message-id="${CSS.escape(lastUserId!)}"]`,
      );
      if (target) {
        const apply = () => {
          sizeTail();
          const tRect = target.getBoundingClientRect();
          const cRect = node.getBoundingClientRect();
          const targetTop = node.scrollTop + (tRect.top - cRect.top);
          const max = node.scrollHeight - node.clientHeight;
          node.scrollTo({
            top: Math.min(max, Math.max(0, targetTop - PIN_TOP_OFFSET_PX)),
            behavior: "auto",
          });
        };
        apply();
        requestAnimationFrame(apply);
        return;
      }
    }
    if (stickBottomRef.current) {
      node.scrollTop = node.scrollHeight;
    }
  }, [items]);

  return (
    <div className="timeline" ref={scrollRef}>
      {items.length === 0 ? (
        <div className="empty-timeline">
          <div className="empty-title">Start a session</div>
          <p>Ask CodeAgents to inspect, edit, test, or explain this workspace.</p>
        </div>
      ) : null}
      {items.map((item) => (
        <TimelineEntry
          item={item}
          key={item.id}
          onApproveTool={onApproveTool}
          onRejectTool={onRejectTool}
        />
      ))}
      {/* Empty growth area beneath the chat: lets the pinned user message
          scroll to the top edge of the viewport on short conversations. */}
      <div className="timeline-tail" ref={tailRef} aria-hidden />
    </div>
  );
}

function TimelineEntry({
  item,
  onApproveTool,
  onRejectTool,
}: {
  item: TimelineItem;
  onApproveTool: (decisionId: string) => void;
  onRejectTool: (decisionId: string) => void;
}) {
  switch (item.kind) {
    case "message": {
      const isAssistant = item.role === "assistant";
      return (
        <article
          className={`timeline-message timeline-message-${item.role}`}
          data-message-id={item.id}
          data-mode={item.mode}
        >
          {item.text ? (
            isAssistant ? (
              <Markdown source={item.text} />
            ) : (
              <div className="message-body">{item.text}</div>
            )
          ) : (
            <div className="message-body">(attachment only)</div>
          )}
          {item.attachments.length > 0 ? (
            <div className="message-attachments">
              {item.attachments.map((attachment) => (
                <span key={attachment}>{attachment}</span>
              ))}
            </div>
          ) : null}
        </article>
      );
    }
    case "assistant-live":
      return (
        <article className="timeline-message timeline-message-assistant live">
          <Markdown source={item.content} />
        </article>
      );
    case "thinking":
      return (
        <article className="thinking-block">
          <Markdown source={item.content} />
        </article>
      );
    case "tool":
      return (
        <ToolCard
          item={item}
          onApproveTool={onApproveTool}
          onRejectTool={onRejectTool}
        />
      );
    case "terminal":
      return (
        <article className="terminal-inline">
          <span>terminal {item.sessionId}</span>
          <pre>{item.chunk}</pre>
        </article>
      );
    case "model":
      return (
        <article className="notice-row">
          <StatusIcon status="muted" /> model: {item.model || "loading"}
        </article>
      );
    case "notice":
      return (
        <article className={`notice-row notice-${item.level}`}>
          <StatusIcon status={item.level === "error" ? "error" : "muted"} />{" "}
          {item.message}
        </article>
      );
    case "done":
      return (
        <article className="notice-row">
          <StatusIcon status="done" /> done {item.model ? `· ${item.model}` : ""}{" "}
          {item.stopReason ? `· ${item.stopReason}` : ""}
        </article>
      );
    case "error":
      return (
        <article className="notice-row notice-error">
          <StatusIcon status="error" /> {item.message}
        </article>
      );
  }
}

function ToolCard({
  item,
  onApproveTool,
  onRejectTool,
}: {
  item: Extract<TimelineItem, { kind: "tool" }>;
  onApproveTool: (decisionId: string) => void;
  onRejectTool: (decisionId: string) => void;
}) {
  const isRunning = item.status === "running";
  // Persisted tools come from saved chats with ``startedAt: 0`` (we don't
  // know the original wall-clock). Showing ``Date.now() / 1000 sec`` would
  // print a 10-digit unix timestamp, so suppress elapsed in that case.
  const elapsedKnown = item.startedAt > 0;
  const elapsed = elapsedKnown
    ? Math.max(
        0,
        Math.round(((item.completedAt ?? Date.now()) - item.startedAt) / 1000),
      )
    : null;
  const summary = summarizeToolArgs(item.name, item.args || item.deltas);
  const fullArgs = item.deltas || item.args || "";
  return (
    <article className={`tool-card tool-card-${item.status}`}>
      <div className="tool-tree">
        <span className="tree-glyph">└─</span>
        <StatusIcon
          status={
            item.status === "done"
              ? "done"
              : item.status === "pending"
                ? "pending"
                : item.status === "error"
                  ? "error"
                  : "running"
          }
        />
        <strong className="tool-name">{item.name}</strong>
        {summary ? <span className="tool-summary">{summary}</span> : null}
        <span className="tool-meta">
          {isRunning ? "running" : item.status}
          {elapsed !== null ? ` · ${elapsed}s` : null}
        </span>
      </div>
      {fullArgs && fullArgs !== summary ? (
        <details className="tool-details">
          <summary>arguments</summary>
          <pre className="tool-input">{fullArgs}</pre>
        </details>
      ) : null}
      {item.warning ? <div className="tool-warning">{item.warning}</div> : null}
      {item.output ? <pre className="tool-output">{item.output}</pre> : null}
      {item.diff ? <DiffCard diff={item.diff} /> : null}
      {item.status === "pending" && item.decisionId ? (
        <div className="tool-actions">
          <button onClick={() => onRejectTool(item.decisionId!)} type="button">
            Reject
          </button>
          <button onClick={() => onApproveTool(item.decisionId!)} type="button">
            Approve
          </button>
        </div>
      ) : null}
    </article>
  );
}

function DiffCard({ diff }: { diff: NonNullable<Extract<TimelineItem, { kind: "tool" }>["diff"]> }) {
  return (
    <div className="diff-card">
      <div className="diff-header">
        <span>diff</span>
        <span>
          +{diff.added} -{diff.removed}
        </span>
      </div>
      <pre>
        {diff.preview.map((line) => (
          <div
            className={
              line.startsWith("+")
                ? "diff-add"
                : line.startsWith("-")
                  ? "diff-del"
                  : "diff-meta"
            }
            key={line}
          >
            {line}
          </div>
        ))}
      </pre>
    </div>
  );
}
