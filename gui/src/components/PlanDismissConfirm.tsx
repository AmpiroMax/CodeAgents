import { useEffect, useState } from "react";
import type { Plan } from "../lib/api";

/** "Are you sure?" dialog for the small × on a plan card. Mirrors
 *  ExitConfirm visually so the user reads it as the same modal surface
 *  but dedicated to a single yes/no choice. Default highlight is "No"
 *  to make accidental dismissal harder. */
export function PlanDismissConfirm({
  plan,
  onCancel,
  onConfirm,
}: {
  plan: Plan | null;
  onCancel: () => void;
  onConfirm: (plan: Plan) => void;
}) {
  const options: { id: "no" | "yes"; label: string; hint?: string }[] = [
    { id: "no", label: "No", hint: "Enter" },
    { id: "yes", label: "Yes — mark plan as rejected", hint: "↓ then Enter" },
  ];
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (plan) setActive(0);
  }, [plan]);

  useEffect(() => {
    if (!plan) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((i) => (i + 1) % options.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((i) => (i - 1 + options.length) % options.length);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        if (options[active]?.id === "yes") onConfirm(plan);
        else onCancel();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [active, plan, onCancel, onConfirm, options]);

  if (!plan) return null;
  return (
    <div className="palette-backdrop" onClick={onCancel}>
      <div
        aria-modal="true"
        className="palette"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="palette-breadcrumb">
          <span>Dismiss plan: {plan.title}?</span>
        </div>
        <ul className="palette-list">
          {options.map((option, idx) => (
            <li
              className={`palette-item${idx === active ? " active" : ""}`}
              key={option.id}
              onClick={() => {
                if (option.id === "yes") onConfirm(plan);
                else onCancel();
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
