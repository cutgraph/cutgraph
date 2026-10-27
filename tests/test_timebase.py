"""Timebase exactness: half-away-from-zero rounding, per-cut against absolute time, no drift.

Pure/offline — no ffmpeg, no GPU. Runs from the repo root with plain `pytest`.
"""
from __future__ import annotations

from pipeline.timebase import frame_to_sec, sec_to_frame

FPS = 30


def test_half_tie_rounds_away_from_zero_not_bankers():
    # 0.5 frame must round UP to 1; builtin round() is banker's (half-to-even) and gives 0.
    assert sec_to_frame(0.5 / FPS, FPS) == 1
    assert round(0.5) == 0
    # 2.5 frames must round to 3; builtin round(2.5) == 2 (half-to-even) — the trap we avoid.
    assert sec_to_frame(2.5 / FPS, FPS) == 3
    assert round(2.5) == 2
    assert sec_to_frame(1.5 / FPS, FPS) == 2


def test_negative_half_away_from_zero():
    assert sec_to_frame(-0.5 / FPS, FPS) == -1
    assert sec_to_frame(-2.5 / FPS, FPS) == -3


def test_frame_to_sec_inverse_within_half_frame():
    # sec_to_frame rounds to the nearest frame, so the inverse is within half a frame (1/60 s @30).
    tol = 1 / 60 + 1e-9
    for i in range(5000):
        s = i * 0.00731
        f = sec_to_frame(s, FPS)
        assert abs(frame_to_sec(f, FPS) - s) <= tol


def test_no_accumulation_drift_over_1000_cuts():
    # Each cut boundary is converted from ABSOLUTE composition time (never by summing rounded
    # per-cut durations). Two invariants prove there is no drift:
    d = 0.1734  # a deliberately non-frame-aligned cut length in seconds
    boundaries = [sec_to_frame(k * d, FPS) for k in range(1001)]

    # (1) Per-cut frame lengths (each from absolute boundaries) telescope EXACTLY to the whole span
    #     — summing independent per-cut results has zero total error.
    summed = sum(boundaries[k] - boundaries[k - 1] for k in range(1, 1001))
    assert summed == boundaries[1000] - boundaries[0]

    # (2) Every boundary — including the 1000th — stays within half a frame of its true position.
    #     Error is bounded by 0.5 frame forever; it never accumulates with k.
    assert all(abs(boundaries[k] - (k * d) * FPS) <= 0.5 for k in range(1001))
