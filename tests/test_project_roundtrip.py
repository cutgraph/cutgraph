"""project.json model <-> dict identity, schema validation of the golden, v0 migration, validate().

Pure/offline. jsonschema validates the golden file against the checked-in schema; the model
round-trip is asserted at the dataclass level (from_dict(to_dict(p)) == p).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from pipeline.errors import ValidationError

from pipeline import project

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "project.schema.json"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _golden_dict() -> dict:
    return _load("project_v1_golden.json")


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# ── round-trip identity ──────────────────────────────────────────────────────────

def test_from_dict_to_dict_identity():
    golden = _golden_dict()
    model = project.from_dict(golden)
    assert project.from_dict(project.to_dict(model)) == model


def test_to_dict_text_track_emits_items_others_clips():
    model = project.from_dict(_golden_dict())
    d = project.to_dict(model)
    by_id = {t["id"]: t for t in d["tracks"]}
    assert "items" in by_id["text"] and "clips" not in by_id["text"]
    assert "clips" in by_id["speech"] and "items" not in by_id["speech"]


# ── golden validates against the JSON Schema ─────────────────────────────────────

def test_golden_validates_against_schema():
    jsonschema.validate(instance=_golden_dict(), schema=_schema())


def test_model_validate_accepts_golden():
    project.validate(project.from_dict(_golden_dict()))


def test_roundtrip_accepts_overlapping_titles_on_distinct_text_tracks():
    """Text tiers may overlap each other, but each individual tier remains overlap-free."""
    model = project.from_dict(_golden_dict())
    first_item = next(track for track in model.tracks if track.kind == "text").items[0]
    assert isinstance(first_item, project.TextItem)
    first = first_item
    model.tracks.append(project.Track(
        id="text_lower",
        kind="text",
        items=[project.TextItem(
            id="t2", text="LOWER TITLE", preset=first.preset, pos=dict(first.pos),
            tlStartF=first.tlStartF, durF=first.durF,
        )],
    ))
    project.validate(model)
    assert project.from_dict(project.to_dict(model)) == model

    # The same overlap in one tier remains invalid.
    model.tracks[-1].items.append(project.TextItem(
        id="t3", text="CONFLICT", preset=first.preset, pos=dict(first.pos),
        tlStartF=first.tlStartF, durF=first.durF,
    ))
    with pytest.raises(ValidationError, match="overlap"):
        project.validate(model)


# ── v0 -> v1 migration ───────────────────────────────────────────────────────────

def test_v0_migrates_to_expected_v1_model():
    model = project.from_dict(_load("project_v0.json"))
    expected = project.Project(
        name="legacy-clip-01",
        fps=30,
        width=1920,
        height=1080,
        revision=1,
        transcript="transcript.json",
        scenes=[project.Scene(id="scene-1", title="Full video", role="Beat", startWord=0)],
        schemaVersion=project.CURRENT_SCHEMA_VERSION,
        tracks=[
            project.Track(
                id="speech",
                kind="speech",
                clips=[
                    project.Clip(
                        id="c1",
                        media=project.Media("media/A001.mp4", None),
                        srcInF=0,
                        srcOutF=900,
                        tlStartF=0,
                        gainDb=0,
                    )
                ],
            ),
            project.Track(
                id="broll",
                kind="video",
                clips=[
                    project.Clip(
                        id="b1",
                        media=project.Media("media/garlic-chopping.mp4", None),
                        srcInF=0,
                        srcOutF=120,
                        tlStartF=900,
                    )
                ],
            ),
            project.Track(
                id="text",
                kind="text",
                items=[
                    project.TextItem(
                        id="t1",
                        text="OLD TITLE",
                        preset="hook",
                        pos={"x": 0.5, "y": 0.1},
                        tlStartF=0,
                        durF=60,
                        anchor=project.Anchor(mode="time"),
                    )
                ],
            ),
        ],
    )
    assert model == expected
    project.validate(model)


def test_v0_bare_string_media_becomes_object():
    model = project.from_dict(_load("project_v0.json"))
    clip = model.tracks[0].clips[0]
    assert isinstance(clip.media, project.Media)
    assert clip.media.source == "media/A001.mp4"
    assert clip.media.proxy is None


def test_v1_migrates_without_materializing_visual_transform_defaults():
    model = project.from_dict(_golden_dict())
    assert model.schemaVersion == project.CURRENT_SCHEMA_VERSION
    assert model.tracks[0].clips[0].transform is None
    # Reset/default is deliberately represented by omission, not a frozen v1 rendering snapshot.
    assert "transform" not in project.to_dict(model)["tracks"][0]["clips"][0]


def test_visual_transform_roundtrips_and_validates():
    model = _base_model()
    clip = model.tracks[1].clips[0]
    clip.transform = {"x": 0.35, "y": 0.6, "scale": 1.25, "fit": "cover"}
    project.validate(model)
    assert project.from_dict(project.to_dict(model)) == model


@pytest.mark.parametrize("transform", [
    {"x": -0.01, "y": 0.5, "scale": 1, "fit": "cover"},
    {"x": 0.5, "y": 0.5, "scale": 4.01, "fit": "cover"},
    {"x": 0.5, "y": 0.5, "scale": 1, "fit": "stretch"},
    {"x": 0.5, "y": 0.5, "scale": True, "fit": "contain"},
])
def test_validate_rejects_invalid_visual_transform(transform):
    model = _base_model()
    model.tracks[1].clips[0].transform = transform
    with pytest.raises(ValidationError):
        project.validate(model)


def test_validate_rejects_audio_transform():
    model = _base_model()
    model.tracks.append(project.Track(
        id="music", kind="audio", clips=[project.Clip(
            id="music1", media=project.Media("media/music.mp3"), srcInF=0, srcOutF=30, tlStartF=0,
            transform={"x": 0.5, "y": 0.5, "scale": 1, "fit": "contain"},
        )],
    ))
    with pytest.raises(ValidationError):
        project.validate(model)


# ── title ───────────────────────────────────────────────────────────────

def test_title_roundtrips_and_validates():
    p = _base_model()
    p.title = "My Great Video"
    project.validate(p)
    assert project.to_dict(p)["title"] == "My Great Video"
    assert project.from_dict(project.to_dict(p)) == p


def test_title_omitted_from_dict_when_unset():
    p = _base_model()
    assert p.title is None
    assert "title" not in project.to_dict(p)


@pytest.mark.parametrize("bad_title", ["", "   ", "  leading space", "trailing space  ", "x" * 121, 123, True])
def test_validate_rejects_invalid_title(bad_title):
    p = _base_model()
    p.title = bad_title
    with pytest.raises(ValidationError):
        project.validate(p)


def test_schema_rejects_empty_title():
    bad = _golden_dict()
    bad["title"] = ""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_schema())


def test_schema_accepts_null_title():
    good = _golden_dict()
    good["title"] = None
    jsonschema.validate(instance=good, schema=_schema())


# ── validate() rejections ────────────────────────────────────────────────────────

def _base_model() -> project.Project:
    return project.from_dict(_golden_dict())


def test_validate_rejects_within_track_overlap():
    p = _base_model()
    # add a second speech clip that starts before the first (0..1152) ends
    p.tracks[0].clips.append(
        project.Clip(id="c2", media=project.Media("media/A001.mp4"), srcInF=0, srcOutF=300, tlStartF=100)
    )
    with pytest.raises(ValidationError):
        project.validate(p)


def test_validate_rejects_speech_clip_with_anchor():
    p = _base_model()
    p.tracks[0].clips[0].anchor = project.Anchor(mode="time")
    with pytest.raises(ValidationError):
        project.validate(p)


def test_validate_rejects_bare_string_media_at_load():
    # A schemaVersion:1 doc with bare-string media never migrates, so loading it fails outright
    # (v1 media is always the {source, proxy?} object). The schema rejects it too.
    bad = _golden_dict()
    bad["tracks"][0]["clips"][0]["media"] = "media/A001.mp4"
    with pytest.raises((TypeError, KeyError)):
        project.from_dict(bad)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_schema())


def test_validate_rejects_absolute_media_path():
    p = _base_model()
    p.tracks[0].clips[0].media = project.Media("/etc/passwd")
    with pytest.raises(ValidationError):
        project.validate(p)


def test_validate_rejects_dotdot_media_path():
    p = _base_model()
    p.tracks[0].clips[0].media = project.Media("../secret/A001.mp4")
    with pytest.raises(ValidationError):
        project.validate(p)


def test_validate_rejects_backslash_media_path():
    p = _base_model()
    p.tracks[0].clips[0].media = project.Media("media\\A001.mp4")
    with pytest.raises(ValidationError):
        project.validate(p)


@pytest.mark.parametrize("bad_ref", ["C:/evil.mp4", "C:x", "//server/share/evil.mp4", "http://x/e.mp4"])
def test_validate_rejects_drive_and_scheme_media_paths(bad_ref):
    # A Windows drive-absolute ('C:/x'), drive-relative ('C:x'), UNC ('//server/..') or URL ref lets
    # Path(project_dir)/ref discard project_dir and escape the sandbox. The colon / leading-slash
    # checks in _check_rel_path must reject all of them.
    p = _base_model()
    p.tracks[0].clips[0].media = project.Media(bad_ref)
    with pytest.raises(ValidationError):
        project.validate(p)


@pytest.mark.parametrize("bad_ref", ["C:/evil.mp4", "C:x", "//server/share/evil.mp4"])
def test_resolve_media_refuses_to_escape_project_dir(bad_ref):
    # Belt-and-braces: even a ref that somehow bypassed validate() must not resolve outside
    # project_dir. On Windows, Path('D:/proj') / 'C:/evil.mp4' == WindowsPath('C:/evil.mp4').
    with pytest.raises(ValidationError):
        project.resolve_media(Path("D:/proj"), project.Media(bad_ref), prefer_proxy=False)


def test_resolve_media_joins_relative_ref_under_project_dir():
    resolved = project.resolve_media(Path("D:/proj"), project.Media("media/A001.mp4"), prefer_proxy=False)
    assert resolved == Path("D:/proj") / "media" / "A001.mp4"


def test_validate_rejects_fps_not_30():
    p = _base_model()
    p.fps = 25
    with pytest.raises(ValidationError):
        project.validate(p)


def test_validate_rejects_duplicate_ids():
    p = _base_model()
    # give the broll clip the same id as the speech clip -> global id collision
    p.tracks[1].clips[0].id = "c1"
    with pytest.raises(ValidationError):
        project.validate(p)


def test_validate_rejects_word_anchor_without_ref():
    p = _base_model()
    p.tracks[1].clips[0].anchor = project.Anchor(mode="word", ref=None)
    with pytest.raises(ValidationError):
        project.validate(p)


def test_validate_rejects_text_pos_out_of_range():
    p = _base_model()
    p.tracks[2].items[0].pos = {"x": 1.5, "y": 0.1}
    with pytest.raises(ValidationError):
        project.validate(p)


# ── the JSON Schema now mirrors validate()'s single-field bounds ─────────────
def test_schema_rejects_fps_not_30():
    bad = _golden_dict()
    bad["fps"] = 25
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_schema())


def test_schema_rejects_negative_frame():
    bad = _golden_dict()
    bad["tracks"][0]["clips"][0]["srcInF"] = -1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_schema())


def test_schema_rejects_zero_srcoutf():
    bad = _golden_dict()
    bad["tracks"][0]["clips"][0]["srcOutF"] = 0
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_schema())


def test_schema_rejects_zero_durf():
    bad = _golden_dict()
    bad["tracks"][2]["items"][0]["durF"] = 0
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_schema())


def test_schema_rejects_pos_out_of_range():
    bad = _golden_dict()
    bad["tracks"][2]["items"][0]["pos"] = {"x": 1.5, "y": 0.1}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_schema())


# ── apply_ops (RFC-6902) ─────────────────────────────────────────────────────────

def test_apply_ops_applies_patch_and_revalidates():
    p = _base_model()
    ops = [{"op": "replace", "path": "/tracks/0/clips/0/srcOutF", "value": 600}]
    out = project.apply_ops(p, ops)
    assert out.tracks[0].clips[0].srcOutF == 600
    # original model is untouched (apply_ops works on a serialized copy)
    assert p.tracks[0].clips[0].srcOutF == 1152


def test_apply_ops_invalid_result_raises():
    p = _base_model()
    ops = [{"op": "replace", "path": "/fps", "value": 25}]
    with pytest.raises(ValidationError):
        project.apply_ops(p, ops)


# ── write_project atomicity ──────────────────────────────────────────────────────

def test_write_project_is_atomic_and_roundtrips(tmp_path):
    p = _base_model()
    dest = tmp_path / "proj.json"
    project.write_project(dest, p)
    assert dest.exists()
    assert not (tmp_path / "proj.json.tmp").exists()
    reloaded = project.from_dict(json.loads(dest.read_text(encoding="utf-8")))
    assert reloaded == p


def test_to_dict_does_not_mutate_input_ops():
    # guard: apply_ops must not mutate the caller's ops list or model
    p = _base_model()
    ops = [{"op": "replace", "path": "/revision", "value": 7}]
    snapshot = copy.deepcopy(ops)
    project.apply_ops(p, ops)
    assert ops == snapshot
