import { useEffect, useState } from "react";

/** Esc-triggered "Exit?" confirmation. Visually mirrors CommandPalette so
 *  the user reads it as the same surface but reduced to a yes/no choice.
 *
 *  Behaviour:
 *  - Default selection is "No" (safer) — pressing Enter immediately closes.
 *  - Arrow keys move between options; Enter activates; Esc cancels.
 *  - Selecting "Yes" calls onExit(); the parent should close the window.
 */
export function ExitConfirm({
  open,
  onCancel,
  onExit,
}: {
  open: boolean;
  onCancel: () => void;
  onExit: () => void;
}) {
  // Order matters: Enter on the default highlight (index 0 = "No") cancels.
  // Arrow Down moves the highlight to "Yes" so the user has to deliberately
  // pick destructive option.
  const options: { id: "no" | "yes"; label: string; hint?: string }[] = [
    { id: "no", label: "No", hint: "Enter" },
    { id: "yes", label: "Yes — quit CodeAgents", hint: "↓ then Enter" },
  ];
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (open) {
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((idx) => (idx + 1) % options.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((idx) => (idx - 1 + options.length) % options.length);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        if (options[active]?.id === "yes") {
          onExit();
        } else {
          onCancel();
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [active, onCancel, onExit, open, options]);

  if (!open) {
    return null;
  }

  return (
    <div className="palette-backdrop" onClick={onCancel}>
      <div
        aria-modal="true"
        className="palette"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="palette-breadcrumb">
          <span>Exit?</span>
        </div>
        <ul className="palette-list">
          {options.map((option, idx) => (
            <li
              className={`palette-item${idx === active ? " active" : ""}`}
              key={option.id}
              onClick={() => {
                if (option.id === "yes") {
                  onExit();
                } else {
                  onCancel();
                }
              }}
              onMouseEnter={() => setActive(idx)}
            >
              <span className="palette-label">{option.label}</span>
              {option.hint ? <span className="palette-hint">{option.hint}</span> : null}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
