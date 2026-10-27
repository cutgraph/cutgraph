"""Preview/export acting parity: bubbles, ensembles and entrances. Python scene specs and render requests match the shared fixture independently checked by TypeScript. Regenerate with tests/fixtures/generate.py."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import scene_render

FIXTURE = Path(__file__).parent / "fixtures" / "scene_specs_parity_acting.json"


def _fx() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_export_scene_specs_match_the_acting_parity_fixture() -> None:
    fx = _fx()
    assert scene_render.scene_specs(fx["project"], fx["words"]) == fx["expected"]


def test_render_requests_resolve_every_performer_like_the_fixture() -> None:
    fx = _fx()
    for spec, want in zip(fx["expected"], fx["requests"], strict=True):
        request = scene_render.render_request(spec, width=1920, height=1080, fps=fx["project"]["fps"], cast=fx["cast"])
        assert (spec["sceneId"], request["shot"], request.get("custom")) == (want["sceneId"], want["shot"], want["custom"])


def test_the_acting_fixture_covers_the_edge_cases() -> None:
    fx = _fx()
    shots = {r["sceneId"]: r["shot"] for r in fx["requests"]}
    duo, cameo, line = shots["scene-1"], shots["scene-2"], shots["scene-3"]
    assert duo["bubble"] and duo["costar"]["bubble"] and duo["ensemble"][0]["bubble"], "a bubble on every kind of performer"
    assert duo["costar"]["custom"], "a co-star's custom face rides inside it"
    assert [m["palette"] for m in duo["ensemble"]] == ["periwinkle", "periwinkle"], "members cast as their own profiles"
    assert any(m.get("move") == "enter" for m in duo["ensemble"]), "a member walks in"
    assert duo["interaction"] == "fistbump", "kept in the document, not played with an ensemble"
    assert "costar" not in line and len(line["ensemble"]) == 2, "an ensemble without a co-star"
    assert line["ensemble"][1] == {"character": "lens", "palette": "periwinkle", "pose": "flex"}, "an unseen member keeps a supported pose on the default rig"
    assert "expression" not in duo["ensemble"][1], "another character's custom expression is dropped"
    assert cameo["mode"] == "cameo" and cameo["bubble"] == "exclaim", "a cameo's bubble"
    assert {s["mode"] for s in fx["expected"]} == {"animation", "cameo"}
    missing = [shots["missing-lead"], shots["missing-costar"]["costar"],
               shots["missing-ensemble"]["ensemble"][0], shots["missing-cameo"]]
    assert all(look["character"] == "retired-rig" and "custom" not in look for look in missing)
    assert shots["missing-costar"]["interaction"] == "face"
    assert shots["missing-ensemble"]["ensemble"][0]["move"] == "enter"
