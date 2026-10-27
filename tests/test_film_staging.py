"""Independent staging features validate, round-trip and match the schema and renderer ids."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from pipeline.errors import ValidationError

from pipeline import copilot
from pipeline import project as project_mod
from pipeline import scene_render

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "motion-core" / "src"
WORDS = [{"word": f"w{i}", "start": i * 0.5 + 0.1, "end": i * 0.5 + 0.4} for i in range(12)]
MEMBERS = [{"character": "editor", "pose": "wave"}, {"character": "reviewer", "pose": "point"}]
MEMBER_LABELS = {"mode": "animation", "diagram": "none", "character": "lens", "pose": "rest",
                 "costar": {"character": "lens", "expression": "curious"},
                 "ensemble": [{"character": "lens", "pose": "wave", "expression": "happy"},
                              {"character": "lens", "pose": "point", "expression": "focused"},
                              {"character": "lens", "pose": "think", "expression": "neutral"}],
                 "headline": "Evening walk", "eyebrow": "", "headlineAt": "costar",
                 "badges": [{"text": "HI", "tone": "accent"}, {"text": "7", "tone": "ink"},
                            {"text": "OAK", "tone": "paper"}]}


def _doc(shot: dict | None) -> dict:
    return {
        "schemaVersion": 3, "name": "zzfilm", "fps": 30, "width": 320, "height": 180, "revision": 1, "transcript": None,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": "c0", "media": {"source": "data/source/talk.mp4", "proxy": None}, "srcInF": 0, "srcOutF": 180, "tlStartF": 0, "gainDb": 0.0},
        ]}],
        "scenes": [
            {"id": "s1", "title": "Feature sample", "role": "Beat", "startWord": 0, **({"shot": shot} if shot else {})},
            {"id": "s2", "title": "Middle", "role": "Beat", "startWord": 6},
        ],
    }


def _schema() -> dict:
    return json.loads((ROOT / "schema" / "project.schema.json").read_text(encoding="utf-8"))


def _ts_list(source: str, name: str) -> tuple[str, ...]:
    body = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]", source)
    assert body, name
    return tuple(re.findall(r"'([^']+)'", body.group(1)))


@pytest.mark.parametrize("shot", [
    MEMBER_LABELS,
    {"mode": "animation", "diagram": "tree", "pose": "point", "costar": {"character": "reader", "bubble": "exclaims"}},
    {"mode": "animation", "camera": "push-in", "diagram": "none", "pose": "rest"},
    {"mode": "animation", "diagram": "list", "pose": "wave", "boardLabels": ["INDEX", "", "Pages"]},
    {"mode": "cameo", "bubble": "question"},
    {"mode": "animation", "diagram": "chain", "pose": "point", "boardLabels": ["INDEX", "Files", "Pages", "Notes"]},
    {"mode": "animation", "camera": "pan", "diagram": "none", "costar": {"character": "reader", "move": "enter"}, "interaction": "face"},
    {"mode": "animation", "diagram": "tree", "costar": {"character": "reader", "bubble": "exclaim"}, "ensemble": MEMBERS},
    {"mode": "animation", "diagram": "radial", "pose": "rest", "costar": {"character": "reader", "bubble": "question"}},
    {"mode": "animation", "diagram": "grid", "pose": "wave", "costar": {"character": "reader", "bubble": "say"}},
    {"mode": "animation", "diagram": "none", "bubble": "think"},
])
def test_feature_shots_validate_round_trip_and_match_the_schema(shot: dict) -> None:
    p = project_mod.from_dict(_doc(shot))
    project_mod.validate(p)
    d = project_mod.to_dict(p)
    assert d["scenes"][0]["shot"] == shot
    jsonschema.validate(instance=d, schema=_schema())


@pytest.mark.parametrize("shot, message", [
    ({"headlineAt": "lead"}, "shot.headlineAt must be one of"),
    ({"headlineAt": ""}, "shot.headlineAt must be one of"),
    ({"camera": "dolly"}, "shot.camera must be one of"),
    ({"bubble": "wonder"}, "shot.bubble must be one of"),
    ({"bubble": "!!"}, "shot.bubble must be one of"),
    ({"boardLabels": ["A", "B", "C", "D", "E", "F", "G"]}, "shot.boardLabels must list 1 to 6 labels"),
    ({"boardLabels": ["Thirteen chars"]}, "shot.boardLabels must list 1 to 6 labels"),
    ({"boardLabels": ["", " "]}, "shot.boardLabels must list 1 to 6 labels"),
    ({"boardLabels": []}, "shot.boardLabels must list 1 to 6 labels"),
    ({"boardLabels": ["TOPIC", 3]}, "shot.boardLabels must list 1 to 6 labels"),
    ({"costar": {"character": "reader", "bubble": "?"}}, r"shot.costar.bubble must be one of"),
])
def test_bad_film_fields_are_rejected_by_validate_and_the_schema(shot: dict, message: str) -> None:
    doc = _doc({"mode": "animation", **shot})
    with pytest.raises(ValidationError, match=message):
        project_mod.validate(project_mod.from_dict(doc))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=doc, schema=_schema())


def test_the_film_ids_mirror_motion_core() -> None:
    staging_ts, ideas_ts = (SRC / "staging.ts").read_text(encoding="utf-8"), (SRC / "ideas.ts").read_text(encoding="utf-8")
    shot = _schema()["$defs"]["Shot"]["properties"]
    assert _ts_list(staging_ts, "HEADLINE_SPOTS") == project_mod.HEADLINE_SPOTS == tuple(shot["headlineAt"]["enum"])
    assert "pan" in project_mod.SHOT_CAMERAS and {"question", "exclaims"} <= set(project_mod.BUBBLES)
    assert set(_ts_list(ideas_ts, "BOARD_KINDS")) | {"none"} <= set(copilot.DIAGRAMS)
    assert copilot.DIAGRAMS == project_mod.SHOT_DIAGRAMS == tuple(shot["diagram"]["examples"])
    assert copilot.SHOT_IDS["headlineAt"] == project_mod.HEADLINE_SPOTS
    limits = re.search(r"export const BOARD_LABELS_MAX = (\d+), BOARD_LABEL_MAX = (\d+);", ideas_ts)
    assert limits and (int(limits[1]), int(limits[2])) == (project_mod.SHOT_BOARD_LABELS_MAX, project_mod.SHOT_BOARD_LABEL_MAX)
    labels = shot["boardLabels"]
    assert (labels["maxItems"], labels["items"]["maxLength"]) == (project_mod.SHOT_BOARD_LABELS_MAX, project_mod.SHOT_BOARD_LABEL_MAX)


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize("diagram", ["legacy-layout", "", None, 7, ["grid"], {"layout": "grid"}])
def test_saved_boards_migrate_without_losing_labels(version: int, diagram: object, tmp_path: Path) -> None:
    shot = {"mode": "animation", "diagram": diagram, "boardLabels": ["Map", "Gallery", "Cafe"],
            "camera": "pan", "bubble": "say"}
    raw = _doc(shot)
    raw["schemaVersion"] = version
    original = deepcopy(raw)
    path = tmp_path / "board.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    model = project_mod.from_dict(json.loads(path.read_text(encoding="utf-8")))
    project_mod.validate(model)
    expected = {key: value for key, value in shot.items() if key != "diagram"}
    assert model.scenes[0].shot == expected
    assert raw == original
    upgraded = project_mod.to_dict(model)
    assert upgraded["schemaVersion"] == project_mod.CURRENT_SCHEMA_VERSION
    assert project_mod.to_dict(project_mod.from_dict(upgraded)) == upgraded
    assert project_mod.migrate_v4_to_v5(upgraded) == upgraded
    project_mod.write_project(path, model)
    assert json.loads(path.read_text(encoding="utf-8")) == upgraded
    specs = scene_render.scene_specs(raw, WORDS)
    assert specs[0]["shot"] == expected
    request = scene_render.render_request(specs[0], width=320, height=180, fps=30)
    assert "diagram" not in request["shot"]
    assert request["shot"]["boardLabels"] == shot["boardLabels"]


@pytest.mark.parametrize("diagram", project_mod.SHOT_DIAGRAMS)
def test_saved_supported_diagrams_are_preserved(diagram: str) -> None:
    raw = _doc({"diagram": diagram})
    raw["schemaVersion"] = 4
    assert project_mod.from_dict(raw).scenes[0].shot == {"diagram": diagram}


def _tdoc() -> dict:
    return {"words": WORDS, "segments": [], "fillers": []}


def _apply(doc: dict, ops: list[dict]) -> dict:
    return project_mod.to_dict(project_mod.apply_ops(project_mod.from_dict(doc), ops))


def test_set_shot_proposes_independent_staging_fields() -> None:
    doc = _doc({"mode": "animation"})
    walk = copilot.propose("set_shot", doc, _tdoc(), {"shots": [
        {"scene": "s1", "camera": "pan", "diagram": "none", "costar": {"character": "default-character", "move": "enter"}}]})
    shot = _apply(doc, walk.ops)["scenes"][0]["shot"]
    assert (shot["camera"], shot["diagram"], shot["costar"]["move"]) == ("pan", "none", "enter")
    assert "pan" in walk.marks[0]["label"]
    labelled = copilot.propose("set_shot", doc, _tdoc(), {"shots": [
        {"scene": "s1", "diagram": "none", "headline": "Reading room hours", "headlineAt": "costar",
         "costar": {"character": "default-character", "bubble": "question"}, "ensemble": [{"character": "default-character"}],
         "badges": [{"text": "DESK", "tone": "accent"}]}]})
    shot = _apply(doc, labelled.ops)["scenes"][0]["shot"]
    assert (shot["headlineAt"], shot["costar"]["bubble"], shot["badges"]) == ("costar", "question", [{"text": "DESK", "tone": "accent"}])
    bangs = copilot.propose("set_shot", doc, _tdoc(), {"shots": [{"scene": "s1", "costar": {"character": "default-character", "bubble": "exclaims"}}]})
    assert _apply(doc, bangs.ops)["scenes"][0]["shot"]["costar"]["bubble"] == "exclaims"
    named = copilot.propose("set_shot", doc, _tdoc(), {"shots": [{"scene": "s1", "diagram": "chain", "boardLabels": ["TOPIC", " Map  ", ""]}]})
    assert _apply(doc, named.ops)["scenes"][0]["shot"]["boardLabels"] == ["TOPIC", "Map", ""]
    for bad in (["Much too long a label"], ["", ""], ["a"] * 7, "TOPIC"):
        with pytest.raises(copilot.ToolError, match="boardLabels must list 1 to 6 labels"):
            copilot.propose("set_shot", doc, _tdoc(), {"shots": [{"scene": "s1", "boardLabels": bad}]})
    for bad, message in (({"headlineAt": "lead"}, "headlineAt must be one of costar"), ({"diagram": "erd"}, "diagram must be one of")):
        with pytest.raises(copilot.ToolError, match=message):
            copilot.propose("set_shot", doc, _tdoc(), {"shots": [{"scene": "s1", **bad}]})


def test_named_workspace_signoff_keeps_omitted_mark_unbranded() -> None:
    from pipeline import cast

    workspace_cast = {"brand": {"name": "Field Notes", "accent": "#2bb3a3"}}
    shot = {"mode": "animation", "layout": "signoff"}
    resolved, _ = cast.resolve_shot(shot, workspace_cast)
    assert "mark" not in resolved
    explicit, _ = cast.resolve_shot({**shot, "mark": "END"}, workspace_cast)
    assert explicit["mark"] == "END"
