"""Scenes: the v3 migration, deterministic auto-grouping, scene edits as RFC-6902 patches, the
JSON Schema, and POST /api/project/<p>/scenes/auto (propose vs revision-gated apply).

Migration decision: a pre-v3 document with a speech clip (its only route to a words.json) gets one
"Full video" scene from word 0; one without speech has no words to group and gets no scenes.
"""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Iterator
from pathlib import Path
from typing import TypeVar

import httpx
import jsonpatch
import jsonschema
import pytest

from pipeline.errors import ValidationError

import config
from pipeline import project, scenes
from scripts import studio_app

T = TypeVar("T")

_ROOT = Path(__file__).resolve().parent.parent
_PREFIX = "zzscn"
_SOURCE_STEM = f"{_PREFIX}_src"


def _schema() -> dict:
    return json.loads((_ROOT / "schema" / "project.schema.json").read_text(encoding="utf-8"))


def _transcript(sentences: list[tuple[int, float]], word_s: float = 0.4) -> dict:
    """A words.json with one segment per (word_count, pause_before_s) sentence; each word lasts
    0.3 s with 0.1 s between words, and the last word of every sentence ends with a period."""
    segments, t = [], 0.0
    for n, (count, pause) in enumerate(sentences):
        t += pause
        words = []
        for k in range(count):
            text = f"s{n}w{k}" + ("." if k == count - 1 else "")
            words.append({"word": f" {text}", "start": round(t, 3), "end": round(t + word_s - 0.1, 3), "score": 0.9})
            t += word_s
        segments.append({"start": words[0]["start"], "end": words[-1]["end"],
                         "text": " ".join(w["word"].strip() for w in words), "words": words})
    return {"language": "en", "duration": round(t, 3), "segments": segments}


def _word_count(doc: dict) -> int:
    return len(scenes.flat_words(doc))


def _assert_covers(start_words: list[int], n: int) -> None:
    seen: list[int] = []
    for s, e in scenes.word_ranges(start_words, n):
        assert s < e
        seen.extend(range(s, e))
    assert sorted(seen) == list(range(n)), "every word must belong to exactly one scene"


def _doc(*, speech: bool = True, version: int = 2) -> dict:
    kind, track_id = ("speech", "speech") if speech else ("video", "video")
    return {
        "schemaVersion": version, "name": "scenes", "fps": 30, "width": 1920, "height": 1080,
        "revision": 1, "transcript": None,
        "tracks": [{"id": track_id, "kind": kind, "clips": [
            {"id": "c1", "media": {"source": f"media/{_SOURCE_STEM}.mp4", "proxy": None},
             "srcInF": 0, "srcOutF": 900, "tlStartF": 0, "gainDb": 0.0}]}],
    }


def _scened(starts: list[int]) -> project.Project:
    p = project.from_dict(_doc())
    p.scenes = [project.Scene(id=f"scene-{i + 1}", title=f"Scene {i + 1}", role="Beat", startWord=s)
                for i, s in enumerate(starts)]
    project.validate(p)
    return p


# ── migration ────────────────────────────────────────────────────────────────────

def test_v2_doc_with_speech_migrates_to_one_full_video_scene() -> None:
    p = project.from_dict(_doc())
    project.validate(p)
    assert p.schemaVersion == project.CURRENT_SCHEMA_VERSION == 5
    assert p.scenes == [project.Scene(id="scene-1", title="Full video", role="Beat", startWord=0)]
    jsonschema.validate(instance=project.to_dict(p), schema=_schema())


def test_v2_doc_without_speech_migrates_to_no_scenes_and_emits_none() -> None:
    p = project.from_dict(_doc(speech=False))
    project.validate(p)
    assert p.scenes == []
    assert "scenes" not in project.to_dict(p)


def test_v0_fixture_migrates_through_the_whole_chain() -> None:
    raw = json.loads((_ROOT / "tests" / "fixtures" / "project_v0.json").read_text(encoding="utf-8"))
    p = project.from_dict(raw)
    project.validate(p)
    assert [s.title for s in p.scenes] == ["Full video"]


def test_v3_scenes_roundtrip_with_shot() -> None:
    p = _scened([0, 10, 20])
    p.scenes[1].shot = {"mode": "animation", "character": "notes-guest", "pose": "lean-in", "expression": "curious",
                        "move": "push", "backdrop": "grid", "diagram": "funnel", "cueWord": 12, "headline": "Why it breaks",
                        "eyebrow": "TEAM NOTES", "hideHeadline": True}
    p.scenes[2].shot = {"mode": "animation", "eyebrow": ""}
    project.validate(p)
    d = project.to_dict(p)
    assert project.from_dict(d) == p
    assert "shot" not in d["scenes"][0] and d["scenes"][1]["shot"]["cueWord"] == 12
    assert d["scenes"][1]["shot"]["hideHeadline"] is True
    assert d["scenes"][2]["shot"]["eyebrow"] == "", "an empty eyebrow (hidden) is kept"
    jsonschema.validate(instance=d, schema=_schema())


# ── validation + schema ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("mutate, message", [
    (lambda s: s.__setitem__(0, {**s[0], "startWord": 1}), "word 0"),
    (lambda s: s.__setitem__(1, {**s[1], "startWord": 0}), "already starts"),
    (lambda s: s.__setitem__(1, {**s[1], "id": "scene-1"}), "duplicate scene id"),
    (lambda s: s.__setitem__(1, {**s[1], "id": "../x"}), "scene id"),
    (lambda s: s.__setitem__(1, {**s[1], "role": "Climax"}), "role"),
    (lambda s: s.__setitem__(1, {**s[1], "title": "   "}), "title"),
    (lambda s: s.__setitem__(1, {**s[1], "title": "x" * 121}), "title"),
    (lambda s: s.__setitem__(1, {**s[1], "startWord": True}), "startWord"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"unknownField": "wide"}}), "unknown fields"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"camera": "wide"}}), "shot.camera"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"cueWord": -1}}), "cueWord"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"mode": "hologram"}}), "shot.mode"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"pose": 3}}), "pose"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"character": ["notes-guest"]}}), "character"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"eyebrow": None}}), "shot.eyebrow must be a string"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"eyebrow": "X" * 41}}), "shot.eyebrow is longer than 40"),
    (lambda s: s.__setitem__(1, {**s[1], "shot": {"hideHeadline": "yes"}}), "shot.hideHeadline must be a boolean"),
])
def test_validate_rejects_bad_scenes(mutate, message) -> None:
    d = project.to_dict(_scened([0, 10, 20]))
    mutate(d["scenes"])
    with pytest.raises(ValidationError, match=message):
        project.validate(project.from_dict(d))


def test_unknown_scene_field_is_rejected_at_load() -> None:
    d = project.to_dict(_scened([0, 10]))
    d["scenes"][0]["color"] = "red"
    with pytest.raises(ValidationError, match="unknown fields"):
        project.from_dict(d)


def test_schema_mirrors_scene_single_field_rules() -> None:
    schema = _schema()
    scene = schema["$defs"]["Scene"]["properties"]
    assert scene["role"]["enum"] == list(project.SCENE_ROLES)
    assert scene["title"]["maxLength"] == project.SCENE_TITLE_MAX
    assert scene["startWord"]["minimum"] == 0
    assert set(schema["$defs"]["Shot"]["properties"]) == {
        "mode", "character", "pose", "expression", "move", "backdrop", "diagram", "cueWord", "headline", "eyebrow",
        "hideHeadline", "costar", "interaction", "bubble", "ensemble", "camera", "layout", "screen", "badges", "mark", "headlineAt",
        "boardLabels"}
    assert schema["$defs"]["Shot"]["properties"]["eyebrow"]["maxLength"] == project.SHOT_EYEBROW_MAX
    assert schema["$defs"]["Shot"]["properties"]["mode"]["enum"] == list(project.SHOT_MODES)
    assert schema["$defs"]["Shot"]["properties"]["interaction"]["enum"] == list(project.INTERACTIONS)
    bad = project.to_dict(_scened([0, 10]))
    bad["scenes"][1]["role"] = "Climax"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


# ── auto-grouping ────────────────────────────────────────────────────────────────

def test_group_is_deterministic() -> None:
    doc = _transcript([(8, 0.1 + (n % 5) * 0.3) for n in range(120)])
    first = scenes.group(doc)
    assert scenes.group(copy.deepcopy(doc)) == first
    assert scenes.group(json.loads(json.dumps(doc))) == first


@pytest.mark.parametrize("sentences, expected", [
    (8, 3),      # ~26 s: never fewer than 3
    (190, 7),    # ~10 min: one scene per ~90 s
    (560, 8),    # ~30 min: capped at 8
])
def test_group_yields_three_to_eight_scenes_covering_every_word(sentences, expected) -> None:
    doc = _transcript([(8, 0.1)] * sentences)
    proposal = scenes.group(doc)
    assert len(proposal) == expected
    _assert_covers([s["startWord"] for s in proposal], _word_count(doc))
    assert [s["startWord"] for s in proposal] == sorted(s["startWord"] for s in proposal)
    p = project.from_dict(_doc())
    project.validate(project.apply_ops(p, scenes.set_scenes_ops(proposal)))


def test_group_splits_at_long_pauses() -> None:
    sentences = [(8, 0.1)] * 12
    sentences[4] = (8, 3.0)
    sentences[8] = (8, 3.0)
    proposal = scenes.group(_transcript(sentences))
    assert [s["startWord"] for s in proposal] == [0, 32, 64]


def test_group_titles_and_default_roles() -> None:
    doc = {"language": "en", "duration": 12.0, "segments": [
        {"start": 0.0, "end": 2.0, "text": "", "words": [
            {"word": f" {w}", "start": 0.2 * i, "end": 0.2 * i + 0.1, "score": 1.0}
            for i, w in enumerate(["so,", "today", "we", "are", "reading", "the", "museum", "map."])]},
        {"start": 4.0, "end": 6.0, "text": "", "words": [
            {"word": f" {w}", "start": 4.0 + 0.2 * i, "end": 4.1 + 0.2 * i, "score": 1.0}
            for i, w in enumerate(["First.", "Read", "slowly"])]},
        {"start": 9.0, "end": 10.0, "text": "", "words": [
            {"word": " \"thanks", "start": 9.0, "end": 9.2, "score": 1.0},
            {"word": " for", "start": 9.3, "end": 9.5, "score": 1.0}]},
    ]}
    proposal = scenes.group(doc)
    assert [(s["title"], s["role"], s["startWord"]) for s in proposal] == [
        ("So, today we are reading", "Hook", 0),
        ("First", "Beat", 8),
        ("Thanks", "Wrap", 11),
    ]
    assert [s["id"] for s in proposal] == ["scene-1", "scene-2", "scene-3"]


def test_group_edge_cases() -> None:
    assert scenes.group({"language": "en", "duration": 1.0, "segments": []}) == []
    one = _transcript([(1, 0.0)])
    assert [(s["role"], s["startWord"]) for s in scenes.group(one)] == [("Beat", 0)]
    two = _transcript([(1, 0.0), (1, 0.5)])
    assert [(s["role"], s["startWord"]) for s in scenes.group(two)] == [("Hook", 0), ("Wrap", 1)]
    # Sentence-free speech still splits (every word boundary becomes a candidate).
    flat = {"language": "en", "duration": 5.0, "segments": [{"start": 0.0, "end": 5.0, "text": "", "words": [
        {"word": f" w{i}", "start": 0.5 * i, "end": 0.5 * i + 0.4, "score": 1.0} for i in range(10)]}]}
    assert len(scenes.group(flat)) == 3


# ── scene edits as RFC-6902 patches through apply_ops ─────────────────────────────

def test_split_at_word_inserts_the_new_scene_after_it() -> None:
    p = _scened([0, 10, 20])
    out = project.apply_ops(p, scenes.split_ops(p, "scene-2", 15, title="Second half"))
    assert [(s.id, s.startWord, s.title) for s in out.scenes] == [
        ("scene-1", 0, "Scene 1"), ("scene-2", 10, "Scene 2"), ("scene-4", 15, "Second half"),
        ("scene-3", 20, "Scene 3")]
    _assert_covers([s.startWord for s in out.scenes], 30)
    for bad in (10, 20, 5):
        with pytest.raises(ValueError):
            scenes.split_ops(p, "scene-2", bad)
    last = project.apply_ops(p, scenes.split_ops(p, "scene-3", 99))
    assert last.scenes[-1].startWord == 99


def test_merge_folds_the_word_order_follower_into_the_scene() -> None:
    p = _scened([0, 10, 20])
    p.scenes[1].shot = {"pose": "point"}
    out = project.apply_ops(p, scenes.merge_ops(p, "scene-2"))
    assert [(s.id, s.startWord) for s in out.scenes] == [("scene-1", 0), ("scene-2", 10)]
    assert out.scenes[1].shot == {"pose": "point"}
    assert scenes.word_ranges([s.startWord for s in out.scenes], 30) == [(0, 10), (10, 30)]
    with pytest.raises(ValueError):
        scenes.merge_ops(p, "scene-3")


def test_reorder_changes_story_order_but_not_word_coverage() -> None:
    p = _scened([0, 10, 20])
    out = project.apply_ops(p, scenes.move_ops(p, "scene-3", 0))
    assert [s.id for s in out.scenes] == ["scene-3", "scene-1", "scene-2"]
    assert scenes.word_ranges([s.startWord for s in out.scenes], 30) == [(20, 30), (0, 10), (10, 20)]
    back = project.apply_ops(out, scenes.move_ops(out, "scene-3", 2))
    assert [s.id for s in back.scenes] == ["scene-1", "scene-2", "scene-3"]
    assert scenes.move_ops(p, "scene-1", 0) == []
    with pytest.raises(ValueError):
        scenes.move_ops(p, "scene-1", 3)
    # Merge follows word order, not list order, after a reorder.
    merged = project.apply_ops(out, scenes.merge_ops(out, "scene-1"))
    assert [(s.id, s.startWord) for s in merged.scenes] == [("scene-3", 20), ("scene-1", 0)]


def test_rename_set_role_and_shot() -> None:
    p = _scened([0, 10])
    p = project.apply_ops(p, scenes.rename_ops(p, "scene-2", "The fix"))
    p = project.apply_ops(p, scenes.set_role_ops(p, "scene-2", "Payoff"))
    p = project.apply_ops(p, scenes.set_shot_ops(p, "scene-2", {"headline": "Read", "cueWord": 11}))
    assert p.scenes[1] == project.Scene(id="scene-2", title="The fix", role="Payoff", startWord=10,
                                        shot={"headline": "Read", "cueWord": 11})
    p = project.apply_ops(p, scenes.set_shot_ops(p, "scene-2", None))
    assert p.scenes[1].shot is None
    assert scenes.set_shot_ops(p, "scene-2", None) == []
    with pytest.raises(ValidationError):
        project.apply_ops(p, scenes.set_role_ops(p, "scene-2", "Climax"))
    with pytest.raises(ValidationError):
        project.apply_ops(p, scenes.rename_ops(p, "scene-2", ""))


def test_patches_are_id_guarded_against_a_changed_document() -> None:
    p = _scened([0, 10, 20])
    stale = scenes.rename_ops(p, "scene-3", "Late")
    moved = project.apply_ops(p, scenes.move_ops(p, "scene-3", 0))
    with pytest.raises(jsonpatch.JsonPatchTestFailed):
        project.apply_ops(moved, stale)
    with pytest.raises(ValueError):
        scenes.rename_ops(p, "nope", "x")


def test_removing_the_first_scene_by_raw_patch_is_rejected() -> None:
    p = _scened([0, 10])
    with pytest.raises(ValidationError, match="word 0"):
        project.apply_ops(p, [{"op": "remove", "path": "/scenes/0"}])


# ── API: PUT/PATCH round-trip + POST /scenes/auto ─────────────────────────────────

def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _call(method: str, path: str, **kwargs) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=studio_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
            return await client.request(method, path, **kwargs)
    return _run(go())


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def wipe() -> None:
        for d in (config.PROJECTS_DIR, config.TRANSCRIPTS_DIR):
            if d.exists():
                for f in d.glob(f"{_PREFIX}*"):
                    f.unlink(missing_ok=True)
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    config.TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    wipe()
    yield
    wipe()


def _seed(name: str, *, words: dict | None = None) -> None:
    (config.PROJECTS_DIR / f"{name}.json").write_text(json.dumps(_doc()), encoding="utf-8")
    if words is not None:
        (config.TRANSCRIPTS_DIR / f"{_SOURCE_STEM}.words.json").write_text(json.dumps(words), encoding="utf-8")


def test_scenes_survive_put_and_patch_through_the_api() -> None:
    name = f"{_PREFIX}_rt"
    doc = project.to_dict(_scened([0, 10, 20]))
    doc["revision"] = 0
    doc["scenes"][2]["shot"] = {"headline": "Wrap it", "cueWord": 21}
    r = _call("PUT", f"/api/project/{name}", json={"project": doc, "baseRevision": 0})
    assert r.status_code == 200, r.text
    got = _call("GET", f"/api/project/{name}").json()
    assert got["project"]["scenes"] == doc["scenes"]

    model = project.from_dict(got["project"])
    ops = scenes.split_ops(model, "scene-1", 4, title="Setup") + scenes.set_role_ops(model, "scene-1", "Hook")
    r = _call("PATCH", f"/api/project/{name}", json={"ops": ops, "baseRevision": got["revision"]})
    assert r.status_code == 200, r.text
    got = _call("GET", f"/api/project/{name}").json()
    assert [(s["id"], s["role"], s["startWord"]) for s in got["project"]["scenes"]] == [
        ("scene-1", "Hook", 0), ("scene-4", "Beat", 4), ("scene-2", "Beat", 10), ("scene-3", "Beat", 20)]
    assert got["project"]["scenes"][3]["shot"] == {"headline": "Wrap it", "cueWord": 21}


def test_auto_proposes_without_writing() -> None:
    name = f"{_PREFIX}_prop"
    words = _transcript([(8, 0.1)] * 30)
    _seed(name, words=words)
    before = (config.PROJECTS_DIR / f"{name}.json").read_text(encoding="utf-8")
    r = _call("POST", f"/api/project/{name}/scenes/auto")
    assert r.status_code == 200, r.text
    assert r.json() == {"scenes": scenes.group(words)}
    assert (config.PROJECTS_DIR / f"{name}.json").read_text(encoding="utf-8") == before


def test_auto_apply_writes_through_the_revision_gate() -> None:
    name = f"{_PREFIX}_apply"
    words = _transcript([(8, 0.1)] * 30)
    _seed(name, words=words)
    url = f"/api/project/{name}/scenes/auto?apply=1"
    assert _call("POST", url, json={}).status_code == 400
    stale = _call("POST", url, json={"baseRevision": 0})
    assert stale.status_code == 409 and stale.json() == {"currentRevision": 1, "error": "The request conflicts with the current state.", "code": "conflict"}
    r = _call("POST", url, json={"baseRevision": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 2
    assert body["project"]["scenes"] == scenes.group(words)
    on_disk = json.loads((config.PROJECTS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert on_disk["revision"] == 2 and on_disk["scenes"] == scenes.group(words)
    _assert_covers([s["startWord"] for s in on_disk["scenes"]], _word_count(words))


def test_auto_404s_without_project_or_transcript() -> None:
    assert _call("POST", f"/api/project/{_PREFIX}_none/scenes/auto").status_code == 404
    _seed(f"{_PREFIX}_bare")
    r = _call("POST", f"/api/project/{_PREFIX}_bare/scenes/auto")
    assert r.status_code == 404 and r.json() == {"error": "no transcript", "code": "not_found"}
