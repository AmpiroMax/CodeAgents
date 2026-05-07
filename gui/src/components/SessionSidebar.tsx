import type { ChatSummary } from "../lib/api";
import { Button, Kbd } from "../design-system/primitives";

export function SessionSidebar({
  chats,
  activeId,
  healthOk,
  onNewChat,
  onOpenChat,
  onDeleteChat,
  onOpenPalette,
}: {
  chats: ChatSummary[];
  activeId?: string;
  healthOk: boolean | null;
  onNewChat: () => void;
  onOpenChat: (id: string) => void;
  onDeleteChat: (id: string) => void;
  onOpenPalette: () => void;
}) {
  return (
    <aside className="session-sidebar">
      <div className="sidebar-brand">
        <div className="brand-mark">CA</div>
        <div>
          <div className="brand-title">CodeAgents</div>
          <div className="brand-subtitle">local agent chat</div>
        </div>
      </div>

      <div className="sidebar-controls">
        <div className="sidebar-actions">
          <Button onClick={onNewChat} type="button" variant="primary">
            + New chat
          </Button>
          <Button onClick={onOpenPalette} type="button" title="Command palette">
            <Kbd>⌘</Kbd>K
          </Button>
        </div>
        <div className={`api-pill ${healthOk === false ? "error" : ""}`}>
          {healthOk === null ? "checking" : healthOk ? "api ready" : "api offline"}
        </div>
      </div>

      <div className="sidebar-section-title">Sessions</div>
      <div className="session-list">
        {chats.map((chat) => (
          <div
            className={`session-row ${chat.id === activeId ? "active" : ""}`}
            key={chat.id}
          >
            <button
              className="session-row-open"
              onClick={() => onOpenChat(chat.id)}
              type="button"
            >
              <span className="session-name">{chat.title || chat.id}</span>
              <span className="session-meta">
                {chat.message_count} msg · {chat.id.slice(0, 6)}
              </span>
            </button>
            <button
              aria-label={`Delete chat ${chat.title || chat.id}`}
              className="session-row-delete"
              onClick={(event) => {
                event.stopPropagation();
                onDeleteChat(chat.id);
              }}
              title="Delete chat"
              type="button"
            >
              ×
            </button>
          </div>
        ))}
        {chats.length === 0 ? (
          <div className="empty-sidebar">No saved sessions yet.</div>
        ) : null}
      </div>
    </aside>
  );
}
