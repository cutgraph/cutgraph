"""Preview/export short-film parity: a museum visit, a camera pan, a route board, marks and ensemble placement. Python scene specs and render requests match the shared fixture independently checked by TypeScript. Regenerate with tests/fixtures/generate.py."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import scene_render

FIXTURE = Path(__file__).parent / "fixtures" / "scene_specs_parity_film.json"


def _fx() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_export_scene_specs_match_the_film_parity_fixture() -> None:
    fx = _fx()
    assert scene_render.scene_specs(fx["project"], fx["words"]) == fx["expected"]


def test_render_requests_carry_the_staging_like_the_fixture() -> None:
    fx = _fx()
    for spec, want in zip(fx["expected"], fx["requests"], strict=True):
        request = scene_render.render_request(spec, width=1920, height=1080, fps=fx["project"]["fps"], cast=fx["cast"])
        assert (spec["sceneId"], request["shot"], request.get("custom")) == (want["sceneId"], want["shot"], want["custom"])


def test_the_film_fixture_covers_the_travel_features() -> None:
    fx = _fx()
    shots = [r["shot"] for r in fx["requests"]]
    assert len(shots) == 6
    assert [s["title"] for s in fx["expected"]] == ["title", "meet", "route", "tickets", "gallery", "ask"]
    assert shots[0]["badges"] == [{"text": "INDOORS", "tone": "accent"}]
    assert len(shots[1]["ensemble"]) == 1 and shots[1]["ensemble"][0]["pose"] == "point"
    assert shots[1]["costar"]["bubble"] == "say" and shots[1]["headlineAt"] == "costar"
    assert shots[2]["diagram"] == "grid" and shots[2]["boardLabels"] == ["Map", "Gallery", "Cafe"]
    phones = [s for s in shots if s.get("layout") == "screen"]
    assert len(phones) == 1 and not phones[0]["screen"].get("more")
    assert shots[4]["camera"] == "pan" and shots[4]["costar"]["move"] == "idle"
    assert shots[5]["bubble"] == "question"
    assert not any(s.get("layout") == "signoff" for s in shots), "the staging fixture covers the sign-off"
    assert not {s.get("camera") for s in shots} & {"push-in", "pull-out"}
    footage = next(s for s in fx["project"]["scenes"] if s["id"] == "footage")
    assert "mode" not in footage["shot"] and footage["id"] not in {e["sceneId"] for e in fx["expected"]}
    assert any(e["cueWordF"] is None and "cueWord" in e["shot"] for e in fx["expected"]), "a designed cut removes a cue word"
