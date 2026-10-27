r"""Regenerate tests/layout_golden.json from pipeline.layout — the shared parity golden that BOTH
tests/test_layout.py (pytest) and ui/test/layout.test.ts (vitest) read. Python is the sole generator;
the TS side never writes it. Any change to layout.py's constants or rounding requires re-running this
in the same change: any layout change updates the golden.

    .venv\Scripts\python scripts\gen_layout_golden.py --regen

Without --regen it prints what WOULD change (dry run) and exits non-zero if the on-disk golden is stale.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import layout  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "tests" / "layout_golden.json"


def _item(id_: str, preset: str, x: float, y: float) -> dict:
    # Exact TextItem JSON shape so both langs deserialize with their existing loaders.
    return {"id": id_, "text": "Hi", "preset": preset, "pos": {"x": x, "y": y}, "tlStartF": 0, "durF": 30}


# (name, width, height, item) — expect is computed from layout.resolve_layout, never hand-written.
# Matrix: each preset × {1920x1080, 1080x1920 vertical, 1280x720} × off-center/edge pos × a .5 boundary
# × an unknown preset (coerces to caption).
_SPECS: list[tuple[str, int, int, dict]] = [
    ("hook_1080p", 1920, 1080, _item("t1", "hook", 0.5, 0.18)),
    ("hook_vertical", 1080, 1920, _item("t2", "hook", 0.5, 0.18)),
    ("hook_720p", 1280, 720, _item("t3", "hook", 0.5, 0.18)),
    ("lower_third_1080p", 1920, 1080, _item("t4", "lower_third", 0.5, 0.78)),
    ("lower_third_vertical", 1080, 1920, _item("t5", "lower_third", 0.5, 0.78)),
    ("lower_third_720p", 1280, 720, _item("t6", "lower_third", 0.5, 0.78)),
    ("caption_1080p", 1920, 1080, _item("t7", "caption", 0.5, 0.85)),
    ("caption_vertical", 1080, 1920, _item("t8", "caption", 0.5, 0.85)),
    ("caption_720p", 1280, 720, _item("t9", "caption", 0.5, 0.85)),
    ("caption_offcenter_left", 1920, 1080, _item("t10", "caption", 0.25, 0.75)),
    ("hook_offcenter_right", 1920, 1080, _item("t11", "hook", 0.75, 0.25)),
    ("caption_origin_corner", 1920, 1080, _item("t12", "caption", 0.0, 0.0)),
    ("caption_max_corner", 1920, 1080, _item("t13", "caption", 1.0, 1.0)),
    ("hook_half_boundary", 1281, 720, _item("t14", "hook", 0.5, 0.5)),
    ("unknown_preset_coerces_caption", 1920, 1080, _item("t15", "banner", 0.5, 0.5)),
    ("lower_third_offcenter_720p", 1280, 720, _item("t16", "lower_third", 0.25, 0.9)),
    # A preset that names an Object.prototype member must coerce to caption in BOTH langs (layout.ts
    # uses a Map, not a plain-object index) — a plain `LAYOUT_PRESETS[preset]` would resolve it to a
    # prototype function and yield NaN font_px in the preview while pytest burns a real caption.
    ("prototype_name_preset_coerces_caption", 1920, 1080, _item("t17", "constructor", 0.5, 0.5)),
]


def _build() -> dict:
    cases = []
    for name, width, height, item in _SPECS:
        cases.append(
            {
                "name": name,
                "width": width,
                "height": height,
                "item": item,
                "expect": layout.resolve_layout(item, width, height),
            }
        )
    return {"cases": cases}


def _serialize(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regen", action="store_true", help="write the golden to disk")
    args = parser.parse_args()

    text = _serialize(_build())
    if args.regen:
        GOLDEN_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {GOLDEN_PATH} ({len(_SPECS)} cases)")
        return 0

    current = GOLDEN_PATH.read_text(encoding="utf-8") if GOLDEN_PATH.exists() else ""
    if current == text:
        print("golden up to date")
        return 0
    print("golden is STALE — run with --regen", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
