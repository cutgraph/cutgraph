"""Layout parity — pytest half of the shared golden. Loads tests/layout_golden.json (the
same file ui/test/layout.test.ts reads) and asserts pipeline.layout.resolve_layout reproduces every
`expect`. gen_layout_golden.py wrote the golden FROM resolve_layout, so this also proves the golden is
committed reproducibly; regenerate it and check for drift.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import layout

GOLDEN = Path(__file__).resolve().parent / "layout_golden.json"


def _cases() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_golden_parity() -> None:
    cases = _cases()
    assert cases, "golden has no cases"
    for case in cases:
        got = layout.resolve_layout(case["item"], case["width"], case["height"])
        assert got == case["expect"], case["name"]


def test_half_away_from_zero_boundary() -> None:
    # 0.5 * 1281 = 640.5 → 641 half-away (banker's round would give 640). Locks the rounding parity gotcha.
    got = layout.resolve_layout({"preset": "hook", "pos": {"x": 0.5, "y": 0.5}}, 1281, 720)
    assert got["x_px"] == 641


def test_unknown_preset_coerces_to_caption() -> None:
    unknown = layout.resolve_layout({"preset": "banner", "pos": {"x": 0.5, "y": 0.5}}, 1920, 1080)
    caption = layout.resolve_layout({"preset": "caption", "pos": {"x": 0.5, "y": 0.5}}, 1920, 1080)
    assert unknown == caption


def test_design_scale_matches_motion_core() -> None:
    """The 4K export's scale: motion-core scene.ts `designScale`, the same rule on both sides."""
    scene_ts = (Path(__file__).resolve().parents[1] / "motion-core" / "src" / "scene.ts").read_text(encoding="utf-8")
    assert "export const designScale = (W: number, H: number): number => Math.max(1, Math.min(W, H) / 1080);" in scene_ts
    sizes = [(1920, 1080), (1080, 1920), (1080, 1080), (320, 180), (3840, 2160), (2160, 3840), (2560, 1440)]
    assert [layout.design_scale(w, h) for w, h in sizes] == [1, 1, 1, 1, 2, 2, 1440 / 1080]
