import { useEffect, useMemo, useRef, useState } from "react";

export type PaletteCommand = {
  id: string;
  label: string;
  hint?: string;
  /** Run when the user presses Enter / clicks the row. */
  action?: () => void;
  /** If set, Enter opens a nested submenu instead of running ``action``. */
  children?: () => PaletteCommand[];
  /** Optional secondary action (Ctrl/Cmd + D). Used for "delete chat". */
  onSecondary?: () => void;
  secondaryHint?: string;
  /** When true, the label is allowed to wrap to multiple lines instead
   * of being ellipsised. Used by info/detail rows (e.g. tool descriptions). */
  wrap?: boolean;
  /** When true, the right-side ``hint`` becomes the long-form value and
   * wraps to multiple lines. Used by tool-detail rows where the LEFT
   * column is the field label (``description``, ``permission``, …) and
   * the RIGHT column is the long value. */
  wrapHint?: boolean;
};

/** Case-insensitive substring filter used by the palette and tests. */
export function filterPaletteCommands(
  commands: PaletteCommand[],
  query: string,
): PaletteCommand[] {
  const q = query.trim().toLowerCase();
  if (!q) {
    return commands;
  }
  return commands.filter((command) => command.label.toLowerCase().includes(q));
}

/** Cycle the highlighted index in response to ArrowUp/ArrowDown. */
export function nextPaletteIndex(
  current: number,
  total: number,
  direction: "up" | "down",
): number {
  if (total <= 0) {
    return 0;
  }
  if (direction === "down") {
    return Math.min(total - 1, current + 1);
  }
  return Math.max(0, current - 1);
}

type Frame = { title?: string; commands: PaletteCommand[] };

export function CommandPalette({
  open,
  commands,
  onClose,
}: {
  open: boolean;
  commands: PaletteCommand[];
  onClose: () => void;
}) {
  const [stack, setStack] = useState<Frame[]>([{ commands }]);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  // When the parent's command list changes (e.g. new chat created), refresh
  // the root frame in-place so the palette never shows stale data.
  useEffect(() => {
    setStack((current) => {
      if (current.length === 0) {
        return [{ commands }];
      }
      const next = current.slice();
      next[0] = { commands };
      return next;
    });
  }, [commands]);

  const currentFrame = stack.at(-1) ?? { commands };

  const filtered = useMemo(
    () => filterPaletteCommands(currentFrame.commands, query),
    [currentFrame, query],
  );

  useEffect(() => {
    if (!open) {
      setStack([{ commands }]);
      setQuery("");
      setActive(0);
      return;
    }
    const id = window.setTimeout(() => inputRef.current?.focus(), 10);
    return () => window.clearTimeout(id);
  }, [commands, open]);

  // Reset highlight + query when navigating between frames.
  useEffect(() => {
    setActive(0);
    setQuery("");
    inputRef.current?.focus();
  }, [stack.length]);

  useEffect(() => {
    setActive((current) => Math.min(current, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  // Keep the highlighted row visible when navigating with the keyboard.
  // We adjust the list's scrollTop directly instead of using
  // ``scrollIntoView`` so only the inner <ul> moves — the surrounding
  // backdrop / page never scroll.
  useEffect(() => {
    const list = listRef.current;
    if (!list) {
      return;
    }
    const item = list.querySelectorAll<HTMLLIElement>(".palette-item")[active];
    if (!item) {
      return;
    }
    const itemTop = item.offsetTop;
    const itemBottom = itemTop + item.offsetHeight;
    const viewTop = list.scrollTop;
    const viewBottom = viewTop + list.clientHeight;
    if (itemTop < viewTop) {
      list.scrollTop = itemTop;
    } else if (itemBottom > viewBottom) {
      list.scrollTop = itemBottom - list.clientHeight;
    }
  }, [active, filtered.length]);

  if (!open) {
    return null;
  }

  const choose = (index: number) => {
    const cmd = filtered[index];
    if (!cmd) {
      return;
    }
    if (cmd.children) {
      const nested = cmd.children();
      setStack((current) => [...current, { title: cmd.label, commands: nested }]);
      return;
    }
    onClose();
    queueMicrotask(() => cmd.action?.());
  };

  const popFrame = () => {
    if (stack.length > 1) {
      setStack((current) => current.slice(0, -1));
      return true;
    }
    return false;
  };

  const triggerSecondary = (index: number) => {
    const cmd = filtered[index];
    if (!cmd?.onSecondary) {
      return;
    }
    cmd.onSecondary();
  };

  const placeholder = currentFrame.title
    ? `${currentFrame.title} — search`
    : "Type a command";

  return (
    <div className="palette-backdrop" onClick={onClose} role="presentation">
      <div
        aria-label="Command palette"
        className="palette"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        {currentFrame.title ? (
          <div className="palette-breadcrumb">
            <button
              className="palette-back"
              onClick={() => popFrame()}
              type="button"
            >
              ← back
            </button>
            <span>{currentFrame.title}</span>
          </div>
        ) : null}
        <input
          aria-label="Search commands"
          className="palette-input"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              // Stop the event from bubbling to the global Esc handler,
              // which would otherwise open the "Exit?" confirmation.
              event.stopPropagation();
              (event.nativeEvent as KeyboardEvent).stopImmediatePropagation?.();
              if (!popFrame()) {
                onClose();
              }
              return;
            }
            if (
              (event.ctrlKey || event.metaKey) &&
              event.key.toLowerCase() === "d"
            ) {
              event.preventDefault();
              triggerSecondary(active);
              return;
            }
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActive((current) =>
                nextPaletteIndex(current, filtered.length, "down"),
              );
              return;
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive((current) =>
                nextPaletteIndex(current, filtered.length, "up"),
              );
              return;
            }
            if (event.key === "Enter") {
              event.preventDefault();
              choose(active);
            }
          }}
          placeholder={placeholder}
          ref={inputRef}
          value={query}
        />
        <ul className="palette-list" ref={listRef} role="listbox">
          {filtered.map((command, index) => (
            <li
              aria-selected={index === active}
              className={`palette-item ${index === active ? "active" : ""}`}
              data-testid="palette-item"
              key={command.id}
              onClick={() => choose(index)}
              onMouseEnter={() => setActive(index)}
              role="option"
            >
              <span
                className={`palette-label${command.wrap ? " wrap" : ""}`}
              >
                {command.label}
                {command.children ? <span className="palette-arrow"> ›</span> : null}
              </span>
              {command.hint ? (
                <span
                  className={`palette-hint${command.wrapHint ? " wrap" : ""}`}
                >
                  {command.hint}
                </span>
              ) : null}
              {command.secondaryHint ? (
                <span className="palette-secondary">
                  {command.secondaryHint}
                </span>
              ) : null}
            </li>
          ))}
          {filtered.length === 0 ? (
            <li className="palette-empty">No commands match.</li>
          ) : null}
        </ul>
      </div>
    </div>
  );
}
