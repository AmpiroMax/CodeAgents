import { describe, expect, it, vi } from "vitest";
import {
  filterPaletteCommands,
  nextPaletteIndex,
  type PaletteCommand,
} from "./CommandPalette";

const sampleCommands: PaletteCommand[] = [
  { id: "new", label: "New chat", action: vi.fn() },
  { id: "rename", label: "Rename current chat", action: vi.fn() },
  { id: "delete", label: "Delete current chat", action: vi.fn() },
  { id: "mode-agent", label: "Switch mode → agent", action: vi.fn() },
];

describe("filterPaletteCommands", () => {
  it("returns everything for empty query", () => {
    expect(filterPaletteCommands(sampleCommands, "")).toHaveLength(
      sampleCommands.length,
    );
  });

  it("filters case-insensitively by label substring", () => {
    const result = filterPaletteCommands(sampleCommands, "Chat");
    expect(result.map((c) => c.id)).toEqual(["new", "rename", "delete"]);
  });

  it("supports unicode arrows in labels", () => {
    const result = filterPaletteCommands(sampleCommands, "agent");
    expect(result.map((c) => c.id)).toEqual(["mode-agent"]);
  });
});

describe("nextPaletteIndex", () => {
  it("clamps to upper bound when moving down", () => {
    expect(nextPaletteIndex(2, 3, "down")).toBe(2);
    expect(nextPaletteIndex(0, 3, "down")).toBe(1);
  });

  it("clamps to zero when moving up", () => {
    expect(nextPaletteIndex(0, 3, "up")).toBe(0);
    expect(nextPaletteIndex(2, 3, "up")).toBe(1);
  });

  it("returns 0 for empty list", () => {
    expect(nextPaletteIndex(5, 0, "down")).toBe(0);
  });
});
