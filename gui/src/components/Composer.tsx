import { useRef } from "react";
import { Button, Kbd } from "../design-system/primitives";

export function Composer({
  input,
  files,
  error,
  canSend,
  streaming,
  mode,
  onInputChange,
  onFilesChange,
  onSend,
}: {
  input: string;
  files: File[];
  error: string | null;
  canSend: boolean;
  streaming: boolean;
  mode: string;
  onInputChange: (value: string) => void;
  onFilesChange: (files: File[]) => void;
  onSend: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const placeholders: Record<string, string> = {
    agent: "make with agent...",
    plan: "plan with agent...",
    ask: "ask an agent...",
  };
  const placeholder = placeholders[mode] ?? "talk to agent...";
  return (
    <footer className="composer-shell">
      {error ? <div className="composer-error">{error}</div> : null}
      {files.length > 0 ? (
        <div className="composer-attachments">
          {files.map((file) => (
            <span key={file.name + file.size}>{file.name}</span>
          ))}
        </div>
      ) : null}
      <div className="composer-box" data-mode={mode}>
        <textarea
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter inserts a newline (also Cmd/Ctrl+Enter
            // kept for muscle memory). Modifiers other than Shift are ignored
            // so IME composition / autocomplete shortcuts pass through.
            if (e.key !== "Enter") {
              return;
            }
            if (e.shiftKey) {
              return;
            }
            if (e.altKey) {
              return;
            }
            e.preventDefault();
            onSend();
          }}
          placeholder={placeholder}
          value={input}
        />
        <div className="composer-tools">
          {/* Hidden, visually-collapsed file input. We trigger it via a
              real <button>, which works reliably in macOS WKWebView once
              the host implements WKUIDelegate.runOpenPanel. ``accept`` is
              limited to images so users only attach pictures. */}
          <input
            accept="image/*"
            multiple
            onChange={(e) => {
              onFilesChange([...(e.target.files ?? [])]);
              if (e.target) {
                e.target.value = "";
              }
            }}
            ref={fileInputRef}
            style={{ display: "none" }}
            type="file"
          />
          <button
            className="attach-button"
            onClick={() => fileInputRef.current?.click()}
            title="Attach image"
            type="button"
          >
            Image
          </button>
          <Button
            disabled={!canSend}
            onClick={onSend}
            type="button"
            variant="primary"
          >
            {streaming ? "Streaming" : "Send"}
          </Button>
        </div>
      </div>
      <div className="composer-hint">
        <span>
          <Kbd>Enter</Kbd> to send · <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> for
          newline · <Kbd>⌘</Kbd>K for commands
        </span>
        <span className="mode-badge" title="Press Tab to switch mode">
          MODE: {mode}
        </span>
      </div>
    </footer>
  );
}
