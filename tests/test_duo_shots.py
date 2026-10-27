"""Duo shots: a scene's shot can carry a co-star (a second cast profile with its own look) and an
interaction. The document validates both, the export's spec builder keeps them, the render request
resolves the co-star against the video's cast like the lead, and the scene cache key follows every
part of the pair. The co-pilot's set_shot proposes them, validated server side."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest

from pipeline.errors import ValidationError

from pipeline import copilot, scene_render
from pipeline import project as project_mod

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
MEDIA = "data/source/talk.mp4"
WORDS = [{"word": f"w{i}", "start": i * 0.5 + 0.1, "end": i * 0.5 + 0.4} for i in range(12)]
GRIN = {"eyes": "arc", "mouth": "smile"}
DUO = {"mode": "animation", "costar": {"character": "notes-guest", "expression": "grin", "pose": "point"},
       "interaction": "fistbump", "cueWord": 3}


def _doc(shot: dict | None) -> dict:
    return {
        "schemaVersion": 3, "name": "zzduo", "fps": 30, "width": 320, "height": 180, "revision": 1, "transcript": None,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": "c0", "media": {"source": MEDIA, "proxy": None}, "srcInF": 0, "srcOutF": 180, "tlStartF": 0, "gainDb": 0.0},
        ]}],
        "scenes": [
            {"id": "s1", "title": "Launch", "role": "Payoff", "startWord": 0, **({"shot": shot} if shot else {})},
            {"id": "s2", "title": "Middle", "role": "Beat", "startWord": 6},
        ],
    }


def _cast() -> dict:
    return json.loads((FIXTURES / "cast_shot_parity.json").read_text(encoding="utf-8"))["cast"]


def _schema() -> dict:
    return json.loads((ROOT / "schema" / "project.schema.json").read_text(encoding="utf-8"))


def _request(shot: dict, cast: dict | None = None) -> dict:
    spec = scene_render.scene_specs(_doc(shot), WORDS)[0]
    return scene_render.render_request(spec, width=320, height=180, fps=30, cast=_cast() if cast is None else cast)


# ── the document ────────────────────────────────────────────────────────────────

def test_a_duo_shot_validates_round_trips_and_matches_the_schema() -> None:
    p = project_mod.from_dict(_doc(DUO))
    project_mod.validate(p)
    d = project_mod.to_dict(p)
    assert d["scenes"][0]["shot"] == DUO
    jsonschema.validate(instance=d, schema=_schema())
    shot = _schema()["$defs"]["Shot"]["properties"]
    assert shot["interaction"]["enum"] == list(project_mod.INTERACTIONS)
    costar = _schema()["$defs"]["Costar"]
    assert costar["required"] == ["character"] and set(costar["properties"]) == project_mod._COSTAR_FIELDS


@pytest.mark.parametrize("shot, message", [
    ({"costar": "notes-guest"}, "shot.costar must be an object"),
    ({"costar": {"pose": "point"}}, "costar.character must name a character"),
    ({"costar": {"character": ""}}, "costar.character must name a character"),
    ({"costar": {"character": "notes-guest", "hat": "top"}}, "costar contains unknown fields"),
    ({"costar": {"character": "notes-guest", "pose": 3}}, "costar.pose must be a string"),
    ({"costar": {"character": "notes-guest"}, "interaction": "hug"}, "shot.interaction must be one of"),
])
def test_bad_duo_shots_are_rejected(shot: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        project_mod.validate(project_mod.from_dict(_doc(shot)))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_doc(shot), schema=_schema())


# ── the export ──────────────────────────────────────────────────────────────────

def test_the_spec_builder_keeps_the_duo_and_its_beat_is_the_cue_word() -> None:
    [spec] = scene_render.scene_specs(_doc(DUO), WORDS)
    assert spec["shot"] == DUO and spec["mode"] == "animation"
    assert spec["cueWordF"] == 3 * 15 + 3, "the fist bump lands on word 3 (1.6 s) at frame 48"
    fx = json.loads((FIXTURES / "scene_specs_parity.json").read_text(encoding="utf-8"))
    duo = next(s for s in fx["expected"] if "costar" in s["shot"])
    assert duo["shot"]["interaction"] == "fistbump" and duo["cueWordF"] is not None, "the shared fixture has a duo on its beat"


def test_the_render_request_resolves_the_co_star_like_the_lead() -> None:
    request = _request(DUO)
    assert request["shot"]["costar"] == {"character": "lens", "palette": "periwinkle", "expression": "grin", "pose": "point", "custom": GRIN}
    assert request["shot"]["interaction"] == "fistbump" and "custom" not in request
    gone = _request({**DUO, "costar": {"character": "gone-client-character", "expression": "grin"}})
    assert gone["shot"]["costar"] == {"character": "lens", "palette": "periwinkle"}, "an unseen profile is the default, without its face"
    single = _request({"mode": "animation", "cueWord": 3})
    assert "costar" not in single["shot"] and "interaction" not in single["shot"]


def test_the_cache_key_covers_both_characters_and_the_interaction() -> None:
    key = scene_render.cache_key(_request(DUO), "v")
    assert scene_render.cache_key(_request(dict(DUO)), "v") == key
    variants = [
        {**DUO, "costar": {**DUO["costar"], "expression": "happy"}},
        {**DUO, "costar": {**DUO["costar"], "pose": "wave"}},
        {**DUO, "costar": {**DUO["costar"], "move": "hop"}},
        {**DUO, "interaction": "highfive"},
        {k: v for k, v in DUO.items() if k != "costar"},
    ]
    for shot in variants:
        assert scene_render.cache_key(_request(shot), "v") != key, shot
    grin = {**_cast()["characters"][1]["expressions"][0], "spec": {**GRIN, "mouth": "grin"}}
    edited = {**_cast(), "characters": [_cast()["characters"][0], {**_cast()["characters"][1], "expressions": [grin]}]}
    assert scene_render.cache_key(_request(DUO, edited), "v") != key, "editing the co-star's custom face re-renders"


@pytest.mark.skipif(not (shutil.which("node") and shutil.which("ffmpeg")), reason="needs Node 24+ and ffmpeg")
def test_the_node_renderer_draws_the_duo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import config
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    frames = {}
    for name, shot in (("duo", DUO), ("single", {"mode": "animation", "cueWord": 3})):
        dest = tmp_path / f"{name}.mp4"
        scene_render.render_scene({**_request(shot), "durF": 2}, dest)
        frames[name] = subprocess.run(["ffmpeg", "-v", "error", "-i", str(dest), "-frames:v", "1", "-f", "rawvideo",
                                       "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(frames["duo"]) == 320 * 180 * 3 and frames["duo"] != frames["single"]


# ── the co-pilot ────────────────────────────────────────────────────────────────

def _tdoc() -> dict:
    return {"words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in WORDS], "segments": [], "fillers": []}


def _apply(doc: dict, ops: list[dict]) -> dict:
    return project_mod.to_dict(project_mod.apply_ops(project_mod.from_dict(doc), ops))


def test_set_shot_proposes_a_co_star_and_an_interaction() -> None:
    doc = _doc({"mode": "animation"})
    p = copilot.propose("set_shot", doc, _tdoc(), {"shots": [
        {"scene": "s1", "costar": {"character": "default-character", "expression": "happy"}, "interaction": "highfive"}]})
    shot = _apply(doc, p.ops)["scenes"][0]["shot"]
    assert shot == {"mode": "animation", "costar": {"character": "default-character", "expression": "happy"}, "interaction": "highfive"}
    assert p.marks[0]["label"] == "Animate “Launch” · with default-character · highfive"
    merged = copilot.propose("set_shot", _apply(doc, p.ops), _tdoc(), {"shots": [{"scene": "s1", "costar": {"pose": "wave", "expression": None}}]})
    assert _apply(_apply(doc, p.ops), merged.ops)["scenes"][0]["shot"]["costar"] == {"character": "default-character", "pose": "wave"}
    removed = copilot.propose("set_shot", _apply(doc, p.ops), _tdoc(), {"shots": [{"scene": "s1", "costar": None}]})
    assert _apply(_apply(doc, p.ops), removed.ops)["scenes"][0]["shot"] == {"mode": "animation"}, "its interaction goes with it"


@pytest.mark.parametrize("costar, message", [
    ({"expression": "happy"}, "costar.character is required: one of default-character"),
    ({"character": "someone-elses"}, "costar.character must be one of default-character"),
    ({"character": "default-character", "pose": "dab"}, "costar.pose must be one of rest, point"),
    ({"character": "default-character", "hat": "top"}, "costar has unknown fields: hat"),
    ("default-character", "costar must be an object"),
])
def test_set_shot_validates_the_co_star(costar: object, message: str) -> None:
    with pytest.raises(copilot.ToolError, match=message):
        copilot.propose("set_shot", _doc({"mode": "animation"}), _tdoc(), {"shots": [{"scene": "s1", "costar": costar}]})
    with pytest.raises(copilot.ToolError, match="interaction must be one of none, face, fistbump, highfive, point"):
        copilot.propose("set_shot", _doc({"mode": "animation"}), _tdoc(), {"shots": [{"scene": "s1", "interaction": "hug"}]})
