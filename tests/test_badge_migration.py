"""Saved badge tones upgrade without changing the rest of the video document."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pipeline import compile as compile_mod
from pipeline import copilot, edits, project, scene_render, thumbnail
from pipeline.errors import ValidationError


def _saved_document(tone: str = "neon") -> dict:
    return project.to_dict(project.Project(
        name="badge-video", title="Morning walk", revision=7,
        tracks=[project.Track(id="speech", kind="speech", clips=[project.Clip(
            id="c1", media=project.Media("media/sample.mp4"), srcInF=0, srcOutF=180, tlStartF=0,
        )])],
        scenes=[project.Scene(id="s1", title="Footpath", role="Beat", startWord=0, shot={
            "mode": "animation", "layout": "title", "headline": "Morning walk",
            "badges": [{"text": "Outside", "tone": tone}],
        }), project.Scene(id="s2", title="Next", role="Beat", startWord=4)],
        thumbnail={"headline": "Footpath", "badges": [{"text": "Map", "tone": tone}], "ensemble": [
            {"character": "default-character", "badge": {"text": "Trail", "tone": tone}},
            {"character": "default-character"},
        ]},
        schemaVersion=3,
    ))


def _upgraded_document(raw: dict) -> dict:
    expected = copy.deepcopy(raw)
    expected["schemaVersion"] = project.CURRENT_SCHEMA_VERSION
    for badges in (expected["scenes"][0]["shot"]["badges"], expected["thumbnail"]["badges"]):
        badges[0]["tone"] = "ink"
    expected["thumbnail"]["ensemble"][0]["badge"]["tone"] = "ink"
    return expected


@pytest.mark.parametrize("version", [None, 0, 1, 2, 3])
@pytest.mark.parametrize("tone", ["neon", "ultraviolet"])
def test_saved_badges_load_validate_edit_and_build_an_export_plan(
    version: int | None, tone: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _saved_document(tone)
    if version is None:
        del raw["schemaVersion"]
    else:
        raw["schemaVersion"] = version
    original = copy.deepcopy(raw)
    path = tmp_path / "project.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    model = project.from_dict(json.loads(path.read_text(encoding="utf-8")))
    project.validate(model)
    expected = _upgraded_document(raw)
    assert project.to_dict(model) == expected
    assert raw == original
    project.write_project(path, model)
    assert json.loads(path.read_text(encoding="utf-8")) == expected

    changed, warnings = edits.ripple_cut(model, 120, 150)
    project.validate(changed)
    assert warnings == []
    assert changed.tracks[0].clips[0].srcOutF == 120
    assert project.to_dict(changed)["scenes"] == expected["scenes"]
    assert changed.thumbnail == expected["thumbnail"]
    words = [{"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(8)]
    proposal = copilot.propose("set_shot", raw, {"words": words, "segments": [], "fillers": []},
                               {"shots": [{"scene": "s1", "camera": "pan"}]})
    proposed = project.apply_ops(model, proposal.ops)
    project.validate(proposed)
    assert proposed.scenes[0].shot == {**expected["scenes"][0]["shot"], "camera": "pan"}

    media = tmp_path / "media" / "sample.mp4"
    media.parent.mkdir()
    media.write_bytes(b"fixture")
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda _path: "")
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda _path: True)
    for doc in (raw, project.to_dict(changed)):
        plan = compile_mod.build_plan(doc, project_dir=tmp_path)
        assert plan.maps == ["-map", "[vout]", "-map", "[aout]"]
        assert "trim=start=0.000000:end=" in plan.filter_complex
    [spec] = scene_render.scene_specs(project.to_dict(changed), words)
    assert spec["shot"]["badges"] == expected["scenes"][0]["shot"]["badges"]
    poster = thumbnail.render_request(project.to_dict(changed), None, "16:9")["poster"]
    assert poster["badges"] == expected["thumbnail"]["badges"]
    assert [m.get("badge") for m in poster["ensemble"]] == [
        m.get("badge") for m in expected["thumbnail"]["ensemble"]
    ]


@pytest.mark.parametrize("tone", project.BADGE_TONES)
def test_migration_preserves_current_tones_and_unrelated_fields(tone: str) -> None:
    raw = _saved_document(tone)
    original = copy.deepcopy(raw)
    expected = copy.deepcopy(raw)
    expected["schemaVersion"] = project.CURRENT_SCHEMA_VERSION
    model = project.from_dict(raw)
    assert project.to_dict(model) == expected
    assert raw == original
    project.validate(model)


def test_migration_is_idempotent_and_copies_nested_badges() -> None:
    raw = _saved_document()
    original = copy.deepcopy(raw)
    migrated = project.migrate_v3_to_v4(raw)
    assert migrated == {**_upgraded_document(raw), "schemaVersion": 4}
    assert project.migrate_v3_to_v4(migrated) == migrated
    assert project.to_dict(project.from_dict(migrated)) == _upgraded_document(raw)
    migrated["scenes"][0]["shot"]["badges"][0]["text"] = "Evening"
    migrated["thumbnail"]["badges"][0]["text"] = "Path"
    migrated["thumbnail"]["ensemble"][0]["badge"]["text"] = "Creek"
    assert raw == original


@pytest.mark.parametrize("location", ["shot", "thumbnail", "ensemble"])
def test_current_documents_still_reject_unknown_tones(location: str) -> None:
    raw = _upgraded_document(_saved_document())
    badge = {
        "shot": raw["scenes"][0]["shot"]["badges"][0],
        "thumbnail": raw["thumbnail"]["badges"][0],
        "ensemble": raw["thumbnail"]["ensemble"][0]["badge"],
    }[location]
    badge["tone"] = "neon"
    with pytest.raises(ValidationError, match="tone must be one of"):
        project.validate(project.from_dict(raw))
