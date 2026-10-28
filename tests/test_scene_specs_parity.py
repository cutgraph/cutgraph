"""Preview/export scene parity on synthetic prose covered by the project licence, with word timestamps generated from its timing recipe. Covers an unaligned word, a cut cue, reordered scenes, footage exclusion, a duo and a delayed cameo. Regenerate with tests/fixtures/generate.py; the TypeScript consumer is an independent check."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import scene_render

FIXTURE = Path(__file__).parent / "fixtures" / "scene_specs_parity.json"


def test_export_scene_specs_match_the_shared_parity_fixture() -> None:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert scene_render.scene_specs(fx["project"], fx["words"]) == fx["expected"]


def test_parity_fixture_covers_the_edge_cases() -> None:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    modes = {s["mode"] for s in fx["expected"]}
    assert modes == {"animation", "cameo"}
    assert any(s["cueWordF"] is None and "cueWord" in s["shot"] for s in fx["expected"]), "a cut cue word"
    assert any(w["start"] is None for w in fx["words"]), "an unaligned word"
    assert [s["id"] for s in fx["project"]["scenes"]][0] != "scene-1", "a reordered story"
    assert len(fx["project"]["scenes"]) > len(fx["expected"]), "a footage scene left out"
    assert any("costar" in s["shot"] and s["shot"].get("interaction") == "fistbump" and s["cueWordF"] is not None
               for s in fx["expected"]), "a duo whose fist bump lands on a kept cue word"
    assert any(s["shot"].get("eyebrow") for s in fx["expected"]), "a shot with its own eyebrow"
    assert any(s["shot"].get("hideHeadline") for s in fx["expected"]), "a shot that hides its headline"
    assert any(s["mode"] == "cameo" and s["cueWordF"] is not None and s["cueWordF"] > s["firstWordF"]
               for s in fx["expected"]), "a cameo springing in on a kept cue word after its first word"
