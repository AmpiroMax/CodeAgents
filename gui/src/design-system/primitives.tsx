import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

export function Button({
  className = "",
  variant = "default",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "danger" | "ghost";
}) {
  return (
    <button
      {...props}
      className={`ca-button ca-button-${variant} ${className}`.trim()}
    />
  );
}

export function Pane({
  className = "",
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={`ca-pane ${className}`.trim()} />;
}

export function StatusIcon({
  status,
  label,
}: {
  status: "running" | "done" | "error" | "pending" | "muted";
  label?: string;
}) {
  return (
    <span className={`status-icon status-icon-${status}`} aria-label={label}>
      {status === "running"
        ? "⠋"
        : status === "done"
          ? "✓"
          : status === "error"
            ? "!"
            : status === "pending"
              ? "?"
              : "·"}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="ca-kbd">{children}</kbd>;
}

export function Tabs({
  active,
  tabs,
  onChange,
}: {
  active: string;
  tabs: Array<{ id: string; label: string }>;
  onChange: (id: string) => void;
}) {
  return (
    <div className="ca-tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          className={tab.id === active ? "active" : ""}
          key={tab.id}
          onClick={() => onChange(tab.id)}
          role="tab"
          type="button"
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
