"""Canonical frame<->second timebase for the project.json contract.

Cut points and durations live in INTEGER FRAMES at project.fps (30, non-drop). Seconds only
appear at the render/OTIO boundary. sec_to_frame rounds half-away-from-zero and is always applied
per-cut against ABSOLUTE composition time — never accumulated — so a thousand sequential boundaries
carry no rounding drift. Builtin round() is banker's (half-to-even) and would mis-handle .5 ties,
so it is deliberately not used here.
"""
from __future__ import annotations

import math


def sec_to_frame(sec: float, fps: int) -> int:
    """Round-half-away-from-zero, per-cut against ABSOLUTE composition time (never accumulated)."""
    x = sec * fps
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


def frame_to_sec(frame: int, fps: int) -> float:
    return frame / fps
