// Shared text-layout math — the TS parity follower for pipeline/layout.py.
// Constants and rounding MUST stay byte-identical with layout.py; tests/layout_golden.json (generated
// by Python) is the shared contract, and ui/test/layout.test.ts asserts this file reproduces it.
//
// `resolveLayout` maps a TextItem's normalized center (pos.x/y in 0..1) + preset to integer pixels
// (x_px, y_px, font_px) plus a box `align`. The player preview positions each text <div> at
// (x_px, y_px) with transform translate(-50%,-50%) and fontSize font_px; compile.py's drawtext
// offsets from the same center. Font scales by frame HEIGHT (matches caption_burn.py).
//
// `preset` is a free string; an unknown preset coerces to "caption", exactly as layout.py does.

import type { TextItem } from "./types/project";

export interface LayoutBox {
  x_px: number;
  y_px: number;
  font_px: number;
  align: "center";
}

interface Preset {
  fontScale: number;
  align: "center";
}

// fontScale = fraction of FRAME HEIGHT (matches caption_burn.py). Mirror LAYOUT_PRESETS in layout.py.
// A Map (not a plain object) so lookup is prototype-safe: layout.py uses `preset in LAYOUT_PRESETS`, so
// a preset that happens to name an Object.prototype member ("constructor", "toString", …) must still
// coerce to caption — a plain-object index would resolve it to a prototype function and yield NaN font_px.
const LAYOUT_PRESETS = new Map<string, Preset>([
  ["hook", { fontScale: 0.075, align: "center" }],
  ["lower_third", { fontScale: 0.045, align: "center" }],
  ["caption", { fontScale: 72 / 1080, align: "center" }], // preserves CAPTION_SIZE=72 @1080p
]);
const DEFAULT_PRESET = "caption"; // unknown preset coerces here (mirror layout.py)

// Round half away from zero (NOT banker's) — mirror layout._round_half_away / cutmap.sec_to_frame.
function roundHalfAway(x: number): number {
  return x >= 0 ? Math.floor(x + 0.5) : Math.ceil(x - 0.5);
}

export function resolveLayout(item: TextItem, width: number, height: number): LayoutBox {
  const p = LAYOUT_PRESETS.get(item.preset) ?? LAYOUT_PRESETS.get(DEFAULT_PRESET)!;
  const pos = item.pos;
  const scale = typeof item.scale === "number" && Number.isFinite(item.scale) ? item.scale : 1;
  return {
    x_px: roundHalfAway(pos.x * width),
    y_px: roundHalfAway(pos.y * height),
    font_px: roundHalfAway(p.fontScale * height * scale),
    align: p.align, // field is `align`, NOT `anchor` — avoid collision with project Anchor (word/time pin)
  };
}
