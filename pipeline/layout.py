"""Shared text-layout math — the ONE source of truth for text pixel
placement, mirrored byte-identically in ui/src/layout.ts.

`resolve_layout` maps a TextItem's normalized center (`pos.x/y` in 0..1) + `preset` to integer
pixels (`x_px`, `y_px`, `font_px`) plus a box `align`. Both preview consumers — the player
text-<div>s and compile.py's drawtext — call this so a caption sits at the same place in
the browser preview and the final ffmpeg render.

Font is scaled by frame HEIGHT (matches caption_burn.py).
Rounding is half-away-from-zero (NOT Python's banker's `round`) to stay bit-for-bit identical with
the TS `Math.round`-style half-up follower — the golden test locks a `.5` boundary case.

Dependency-free: stdlib only. `preset` is a free string; an unknown preset coerces to
`caption`, exactly as ui/src/layout.ts does.
"""
from __future__ import annotations

import math

LAYOUT_PRESETS = {                       # fontScale = fraction of FRAME HEIGHT (matches caption_burn.py)
    "hook":        {"fontScale": 0.075,   "align": "center"},
    "lower_third": {"fontScale": 0.045,   "align": "center"},
    "caption":     {"fontScale": 72 / 1080, "align": "center"},   # preserves CAPTION_SIZE=72 @1080p
}
_DEFAULT_PRESET = "caption"              # unknown preset coerces here (same fallback as resolve_layout)


def _round_half_away(x: float) -> int:  # NOT banker's; mirror cutmap.sec_to_frame / timebase.sec_to_frame
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


def design_scale(width: int, height: int) -> float:
    """Device pixels per design pixel of a frame, as motion-core's `designScale`: 1 at every 1080-class
    size, 2 at 4K and 2/3 at 720p. Fixed pixel sizes (a graphic's border and padding, a text outline)
    are 1080-class design pixels, so a 4K export draws them twice as large and its layout never changes."""
    return min(width, height) / 1080


def resolve_layout(item: dict, width: int, height: int) -> dict:
    """Pure. item = TextItem JSON ({preset, pos:{x,y}, ...}). Returns integer pixels + box alignment.
    pos is the normalized CENTER; each renderer offsets from center per `align`."""
    preset = item.get("preset")
    key = preset if isinstance(preset, str) and preset in LAYOUT_PRESETS else _DEFAULT_PRESET
    p = LAYOUT_PRESETS[key]
    pos = item["pos"]
    scale = item.get("scale", 1.0)
    if not isinstance(scale, (int, float)) or isinstance(scale, bool):
        scale = 1.0
    return {
        "x_px": _round_half_away(pos["x"] * width),
        "y_px": _round_half_away(pos["y"] * height),
        "font_px": _round_half_away(p["fontScale"] * height * scale),
        "align": p["align"],                    # field is `align`, NOT `anchor` — avoid collision with
    }                                           # project.Anchor (word/time pin). Distinct concept, distinct name.
