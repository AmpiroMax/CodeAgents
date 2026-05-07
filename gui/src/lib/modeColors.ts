// Single source of truth for mode-related colours used from JavaScript.
//
// CSS uses the same palette via the ``[data-mode="..."]`` block in
// ``App.css`` (search for ``--mode-accent``); when adding or tweaking a
// mode keep both files in sync.
//
// At runtime the GUI fetches ``GET /modes`` and calls ``hydrateModes`` so
// the colours match exactly what the backend reports (single source of
// truth: ``codeagents.core.modes._MODE_COLORS``). The constants below are
// only the offline fallback used before the first fetch lands.

import type { ModeDescriptor } from "./api";

const FALLBACK: Record<string, string> = {
  agent: "#4ea1ff",
  ask: "#66d685",
  plan: "#ff9b3d",
  research: "#ff5fb0",
};

export const MODE_COLORS: Record<string, string> = { ...FALLBACK };

/** Replace ``MODE_COLORS`` entries with values from ``GET /modes``. The
 * object identity is preserved so existing imports stay live. */
export function hydrateModes(modes: ModeDescriptor[] | null | undefined): void {
  if (!modes) return;
  for (const m of modes) {
    if (m && typeof m.name === "string" && typeof m.ui_color === "string") {
      MODE_COLORS[m.name] = m.ui_color;
    }
  }
}

export type ModeName = keyof typeof FALLBACK;
