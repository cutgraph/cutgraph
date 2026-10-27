import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { resolveLayout } from "../src/layout";
import type { TextItem } from "../src/types/project";

// Read the SAME golden pytest reads. It lives OUTSIDE ui/ at repo tests/; vitest runs node env so fs
// is allowed. Python's gen_layout_golden.py is the sole writer — this side is read-only parity.
interface Case {
  name: string;
  width: number;
  height: number;
  item: TextItem;
  expect: { x_px: number; y_px: number; font_px: number; align: "center" };
}

const golden = JSON.parse(
  readFileSync(resolve(__dirname, "../../tests/layout_golden.json"), "utf-8"),
) as { cases: Case[] };

describe("resolveLayout golden parity", () => {
  it("has cases", () => {
    expect(golden.cases.length).toBeGreaterThan(0);
  });

  for (const c of golden.cases) {
    it(c.name, () => {
      expect(resolveLayout(c.item, c.width, c.height)).toEqual(c.expect);
    });
  }
});

describe("resolveLayout rounding + coercion", () => {
  it("rounds half away from zero (0.5*1281 -> 641, not banker's 640)", () => {
    const item = { preset: "hook", pos: { x: 0.5, y: 0.5 } } as TextItem;
    expect(resolveLayout(item, 1281, 720).x_px).toBe(641);
  });

  it("coerces an unknown preset to caption", () => {
    const unknown = { preset: "banner", pos: { x: 0.5, y: 0.5 } } as TextItem;
    const caption = { preset: "caption", pos: { x: 0.5, y: 0.5 } } as TextItem;
    expect(resolveLayout(unknown, 1920, 1080)).toEqual(resolveLayout(caption, 1920, 1080));
  });

  it("coerces an Object.prototype-member preset name to caption (no NaN font_px)", () => {
    const caption = resolveLayout({ preset: "caption", pos: { x: 0.5, y: 0.5 } } as TextItem, 1920, 1080);
    for (const name of ["constructor", "toString", "valueOf", "hasOwnProperty", "__proto__"]) {
      const box = resolveLayout({ preset: name, pos: { x: 0.5, y: 0.5 } } as TextItem, 1920, 1080);
      expect(box).toEqual(caption);
      expect(Number.isNaN(box.font_px)).toBe(false);
    }
  });
});
