"""Acting: a bubble on any performer (the lead, the co-star, each ensemble member) and an
ensemble of up to three more performers. The document validates both, the schema and motion-core agree
on their ids, the render request resolves each member against the video's cast like the co-star, the
scene cache follows them, deleting a character counts its ensemble scenes, the Node renderer draws a
line-up, and the co-pilot's set_shot proposes them, validated server side."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest

from pipeline.errors import ValidationError

import config
from pipeline import cast as cast_mod
from pipeline import copilot, library, scene_render
from pipeline import project as project_mod

ROOT = Path(__file__).resolve().parent.parent
MEDIA = "data/source/talk.mp4"
WORDS = [{"word": f"w{i}", "start": i * 0.5 + 0.1, "end": i * 0.5 + 0.4} for i in range(12)]
LINE_UP = {"mode": "animation", "bubble": "think", "cueWord": 3,
           "costar": {"character": "notes-guest", "expression": "grin", "bubble": "exclaim"},
           "ensemble": [{"character": "notes-guest", "move": "enter", "bubble": "say"}, {"character": "default-character", "pose": "cheer"}]}


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    return tmp_path


def _doc(shot: dict | None, workspace: str | None = None) -> dict:
    return {
        "schemaVersion": 3, "name": "zzacting", "fps": 30, "width": 320, "height": 180, "revision": 1, "transcript": None,
        **({"workspaceId": workspace} if workspace else {}),
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": "c0", "media": {"source": MEDIA, "proxy": None}, "srcInF": 0, "srcOutF": 180, "tlStartF": 0, "gainDb": 0.0},
        ]}],
        "scenes": [
            {"id": "s1", "title": "Launch", "role": "Payoff", "startWord": 0, **({"shot": shot} if shot else {})},
            {"id": "s2", "title": "Middle", "role": "Beat", "startWord": 6},
        ],
    }


def _cast() -> dict:
    return json.loads((ROOT / "tests" / "fixtures" / "cast_shot_parity.json").read_text(encoding="utf-8"))["cast"]


def _schema() -> dict:
    return json.loads((ROOT / "schema" / "project.schema.json").read_text(encoding="utf-8"))


def _request(shot: dict, cast: dict | None = None) -> dict:
    spec = scene_render.scene_specs(_doc(shot), WORDS)[0]
    return scene_render.render_request(spec, width=320, height=180, fps=30, cast=_cast() if cast is None else cast)


# ── the document ────────────────────────────────────────────────────────────────

def test_an_acting_shot_validates_round_trips_and_matches_the_schema() -> None:
    p = project_mod.from_dict(_doc(LINE_UP))
    project_mod.validate(p)
    d = project_mod.to_dict(p)
    assert d["scenes"][0]["shot"] == LINE_UP
    jsonschema.validate(instance=d, schema=_schema())
    defs = _schema()["$defs"]
    assert defs["Bubble"]["enum"] == list(project_mod.BUBBLES)
    assert defs["Shot"]["properties"]["ensemble"]["maxItems"] == project_mod.ENSEMBLE_MAX
    assert defs["Shot"]["properties"]["ensemble"]["items"] == {"$ref": "#/$defs/Costar"}
    assert defs["Costar"]["properties"]["bubble"] == {"$ref": "#/$defs/Bubble"}


@pytest.mark.parametrize("shot, message", [
    ({"bubble": "shout"}, "shot.bubble must be one of"),
    ({"costar": {"character": "notes-guest", "bubble": "sing"}}, "shot.costar.bubble must be one of"),
    ({"ensemble": {"character": "notes-guest"}}, "shot.ensemble must be a list of at most 3"),
    ({"ensemble": [{"character": "a"}] * 4}, "shot.ensemble must be a list of at most 3"),
    ({"ensemble": ["notes-guest"]}, r"shot.ensemble\[0\] must be an object"),
    ({"ensemble": [{"pose": "flex"}]}, r"shot.ensemble\[0\].character must name a character"),
    ({"ensemble": [{"character": "a"}, {"character": "b", "hat": "top"}]}, r"shot.ensemble\[1\] contains unknown fields"),
    ({"ensemble": [{"character": "a", "move": 3}]}, r"shot.ensemble\[0\].move must be a string"),
    ({"ensemble": [{"character": "a", "bubble": "hum"}]}, r"shot.ensemble\[0\].bubble must be one of"),
])
def test_bad_acting_shots_are_rejected(shot: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        project_mod.validate(project_mod.from_dict(_doc(shot)))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_doc(shot), schema=_schema())


def test_bubbles_and_the_ensemble_limit_match_motion_core() -> None:
    src = ROOT / "motion-core" / "src"
    acting = (src / "acting.ts").read_text(encoding="utf-8")
    ensemble = (src / "ensemble.ts").read_text(encoding="utf-8")
    bubbles = re.search(r"export const BUBBLES\b[^=]*=\s*\[([^\]]*)\]", acting)
    assert bubbles and tuple(re.findall(r"'([^']+)'", bubbles.group(1))) == project_mod.BUBBLES
    assert re.search(rf"export const ENSEMBLE_MAX = {project_mod.ENSEMBLE_MAX};", ensemble)
    bubble_type = re.search(r"export type BubbleId = ([^;]+);", (src / "types.ts").read_text(encoding="utf-8"))
    assert bubble_type and tuple(re.findall(r"'([^']+)'", bubble_type.group(1))) == project_mod.BUBBLES


# ── the export ──────────────────────────────────────────────────────────────────

def test_the_render_request_resolves_each_member_like_the_co_star(client_rig: None) -> None:
    request = _request(LINE_UP)
    shot = request["shot"]
    assert shot["bubble"] == "think"
    assert shot["costar"] == {"character": "lens", "palette": "periwinkle", "expression": "grin", "bubble": "exclaim",
                              "custom": {"eyes": "arc", "mouth": "smile"}}
    assert shot["ensemble"] == [
        {"character": "lens", "palette": "periwinkle", "move": "enter", "bubble": "say"},
        {"character": "lens", "palette": "periwinkle", "pose": "cheer"},
    ]
    red = _request({"mode": "animation", "ensemble": [{"character": "visitor-guide", "expression": "focus", "pose": "wave"},
                                                      {"character": "gone-client-character", "expression": "focus"}]})["shot"]["ensemble"]
    assert red == [{"character": "sample-rig", "palette": "fern", "pose": "wave", "expression": "focus",
                    "custom": {"brows": "raised", "eyes": "open", "mouth": "closed"}},
                   {"character": "lens", "palette": "periwinkle"}], "each member's own rig, palette and face; an unseen one is the default"
    assert "ensemble" not in _request({"mode": "animation"})["shot"]


def test_the_cache_key_covers_every_bubble_and_member() -> None:
    key = scene_render.cache_key(_request(LINE_UP), "v")
    assert scene_render.cache_key(_request(json.loads(json.dumps(LINE_UP))), "v") == key
    member = LINE_UP["ensemble"][0]
    for shot in [
        {**LINE_UP, "bubble": "say"},
        {**LINE_UP, "costar": {**LINE_UP["costar"], "bubble": "think"}},
        {**LINE_UP, "ensemble": [{**member, "bubble": "exclaim"}, LINE_UP["ensemble"][1]]},
        {**LINE_UP, "ensemble": [{**member, "move": "idle"}, LINE_UP["ensemble"][1]]},
        {**LINE_UP, "ensemble": LINE_UP["ensemble"][:1]},
        {k: v for k, v in LINE_UP.items() if k != "ensemble"},
    ]:
        assert scene_render.cache_key(_request(shot), "v") != key, shot


def test_deleting_a_character_counts_the_scenes_it_plays_in_an_ensemble(client_rig: None) -> None:
    garden = library.create_workspace("Garden")["id"]
    cast_mod.create_character(garden, "Editor", "sample-rig", palette="fog")
    scene = lambda i, shot: {"id": f"s{i}", "title": "t", "role": "Hook", "startWord": i, "shot": shot}  # noqa: E731
    member = {"ensemble": [{"character": "default-character"}, {"character": "editor"}]}
    docs = [{"workspaceId": garden, "scenes": [scene(0, {"mode": "animation", **member}), scene(1, {"mode": "cameo", **member})]}]
    assert cast_mod.character_usage("editor", docs) == {"scenes": 1, "videos": 1}, "a cameo draws no ensemble"


@pytest.mark.skipif(not (shutil.which("node") and shutil.which("ffmpeg")), reason="needs Node 24+ and ffmpeg")
def test_the_node_renderer_draws_the_line_up_and_its_bubbles(tmp_path: Path) -> None:
    frames = {}
    for name, shot in (("line-up", LINE_UP), ("duo", {k: v for k, v in LINE_UP.items() if k != "ensemble"})):
        dest = tmp_path / f"{name}.mp4"
        scene_render.render_scene({**_request(shot), "durF": 2, "startF": 0, "cueWordF": 0}, dest)
        frames[name] = subprocess.run(["ffmpeg", "-v", "error", "-i", str(dest), "-vf", "select=eq(n\\,1)", "-frames:v", "1",
                                       "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(frames["line-up"]) == 320 * 180 * 3 and frames["line-up"] != frames["duo"]


# ── the co-pilot ────────────────────────────────────────────────────────────────

def _tdoc() -> dict:
    return {"words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in WORDS], "segments": [], "fillers": []}


def _apply(doc: dict, ops: list[dict]) -> dict:
    return project_mod.to_dict(project_mod.apply_ops(project_mod.from_dict(doc), ops))


def _set(doc: dict, **fields: object) -> tuple[dict, copilot.Proposal]:
    p = copilot.propose("set_shot", doc, _tdoc(), {"shots": [{"scene": "s1", **fields}]})
    return _apply(doc, p.ops), p


def test_set_shot_proposes_bubbles_and_an_ensemble(client_rig: None) -> None:
    garden = library.create_workspace("Garden")["id"]
    cast_mod.create_character(garden, "Editor", "sample-rig", palette="fog")
    doc = _doc({"mode": "animation", "costar": {"character": "default-character"}}, garden)
    doc, p = _set(doc, bubble="think", costar={"bubble": "say"},
                  ensemble=[{"character": "editor", "pose": "wave", "move": "nod", "bubble": "exclaim"}, {"character": "default-character"}])
    assert doc["scenes"][0]["shot"] == {
        "mode": "animation", "bubble": "think", "costar": {"character": "default-character", "bubble": "say"},
        "ensemble": [{"character": "editor", "pose": "wave", "move": "nod", "bubble": "exclaim"}, {"character": "default-character"}]}
    assert p.marks[0]["label"] == "Animate “Launch” · think · with default-character · +2 more"
    project_mod.validate(project_mod.from_dict(doc))
    doc, _ = _set(doc, ensemble=[{"character": "default-character", "bubble": "say"}])
    assert doc["scenes"][0]["shot"]["ensemble"] == [{"character": "default-character", "bubble": "say"}], "an ensemble replaces the line-up"
    doc, _ = _set(doc, bubble=None, costar={"bubble": None}, ensemble=None)
    assert doc["scenes"][0]["shot"] == {"mode": "animation", "costar": {"character": "default-character"}}, "null clears each"


@pytest.mark.parametrize("fields, message", [
    ({"bubble": "shout"}, "shots\\[0\\].bubble must be one of think, say, exclaim"),
    ({"costar": {"character": "default-character", "bubble": "sing"}}, "costar.bubble must be one of think, say, exclaim"),
    ({"ensemble": [{"character": "default-character"}] * 4}, "shots\\[0\\].ensemble takes at most 3 entries"),
    ({"ensemble": []}, "shots\\[0\\].ensemble must be a non-empty list"),
    ({"ensemble": [{"pose": "rest"}]}, "ensemble\\[0\\].character is required: one of default-character"),
    ({"ensemble": [{"character": "someone-elses"}]}, "ensemble\\[0\\].character must be one of default-character"),
    ({"ensemble": [{"character": "default-character", "pose": "not-a-pose"}]}, "ensemble\\[0\\].pose must be one of rest"),
    ({"ensemble": [{"character": "default-character", "bubble": "hum"}]}, "ensemble\\[0\\].bubble must be one of"),
    ({"ensemble": [{"character": "default-character", "hat": "top"}]}, "ensemble\\[0\\] has unknown fields: hat"),
])
def test_set_shot_validates_bubbles_and_members(fields: dict, message: str) -> None:
    with pytest.raises(copilot.ToolError, match=message):
        copilot.propose("set_shot", _doc({"mode": "animation"}), _tdoc(), {"shots": [{"scene": "s1", **fields}]})
