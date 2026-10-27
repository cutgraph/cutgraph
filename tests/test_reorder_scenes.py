"""Story-board scene reorder: the pure `edits.reorder_scenes` edit and its stateless verb
POST /api/verbs/reorder_scenes/<p> (scripts/studio_app.py), in-process via httpx.ASGITransport.

Fixture timing: six words, one per second, word i starting at (i + 0.1)s → source frames 3, 33, 63,
93, 123, 153 at 30 fps. Scenes s1@word0, s2@word2, s3@word4 own source [0,63), [63,123), [123,∞).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import httpx
import pytest

import config
from pipeline import edits
from pipeline import project as P
from pipeline.project import Anchor, Clip, Media, Project, Scene, TextItem, Track
from scripts import studio_app

_PREFIX = "zzro"
_SRC = f"data/source/{_PREFIX}src.mp4"
_WORD_STARTS = [0.1, 1.1, 2.1, 3.1, 4.1, 5.1]
_FRAMES = {_SRC: [3, 33, 63, 93, 123, 153]}


def _scenes() -> list[Scene]:
    return [Scene(id="s1", title="Open", role="Hook", startWord=0),
            Scene(id="s2", title="Middle", role="Beat", startWord=2),
            Scene(id="s3", title="Close", role="Wrap", startWord=4)]


def _clip(cid: str, src_in: int, src_out: int, tl: int, *, media: str = _SRC,
          anchor: Anchor | None = None, seams: list[dict] | None = None) -> Clip:
    return Clip(id=cid, media=Media(source=media), srcInF=src_in, srcOutF=src_out, tlStartF=tl,
                anchor=anchor, seams=seams)


def _word(idx: int) -> Anchor:
    return Anchor(mode="word", ref={"media": _SRC, "wordIdx": idx})


def _project(speech: list[Clip], *extra: Track) -> Project:
    return Project(name="t", tracks=[Track(id="speech", kind="speech", clips=speech), *extra],
                   scenes=_scenes())


def _speech(proj: Project) -> list[tuple[str, int, int, int]]:
    track = next(t for t in proj.tracks if t.kind == "speech")
    return [(c.id, c.srcInF, c.srcOutF, c.tlStartF) for c in track.clips]


def _seams(proj: Project) -> dict[str, list[int]]:
    track = next(t for t in proj.tracks if t.kind == "speech")
    return {c.id: [s["atF"] for s in c.seams or []] for c in track.clips}


def _end(proj: Project) -> int:
    ends = [c.tlStartF + c.srcOutF - c.srcInF for t in proj.tracks for c in t.clips]
    return max(ends + [i.tlStartF + i.durF for t in proj.tracks for i in t.items])


def _text(tid: str, tl: int, dur: int, anchor: Anchor | None = None) -> TextItem:
    return TextItem(id=tid, text=tid, preset="lower_third", pos={"x": 0.5, "y": 0.8}, tlStartF=tl,
                    durF=dur, anchor=anchor)


# ── the pure edit ──────────────────────────────────────────────────────────────
def test_resequences_clips_so_scenes_play_in_the_new_order() -> None:
    proj = _project([_clip("c", 0, 210, 0)])
    out, warnings = edits.reorder_scenes(proj, ["s2", "s1", "s3"], word_frames=_FRAMES)
    assert [s.id for s in out.scenes] == ["s2", "s1", "s3"]
    # Split at the scene boundaries (source 63, 123); s2's material now opens the timeline.
    assert _speech(out) == [("c-s63", 63, 123, 0), ("c", 0, 63, 60), ("c-s123", 123, 210, 123)]
    assert _end(out) == _end(proj) == 210
    # Both new joins are source jumps, so each side gets a fade seam.
    assert _seams(out) == {"c-s63": [60], "c": [0, 63], "c-s123": [0]}
    assert warnings == []
    P.validate(out)
    assert _speech(proj) == [("c", 0, 210, 0)]  # pure: the input is untouched


def test_word_anchored_items_follow_their_words_and_time_pinned_ones_are_flagged() -> None:
    broll = _clip("b1", 0, 30, 93, media=f"data/source/{_PREFIX}broll.mp4", anchor=_word(3))  # word 3 ∈ s2
    title = _text("t1", 3, 20, anchor=_word(0))                                   # word 0 ∈ s1
    pinned = _text("t2", 90, 10)                                                  # time-pinned, over s2
    still = _text("t3", 150, 10)                                                  # time-pinned, over s3
    proj = _project([_clip("c", 0, 210, 0)],
                    Track(id="video", kind="video", clips=[broll]),
                    Track(id="text", kind="text", items=[title, pinned, still]))
    out, warnings = edits.reorder_scenes(proj, ["s2", "s1", "s3"], word_frames=_FRAMES)
    video = next(t for t in out.tracks if t.kind == "video").clips[0]
    items = {i.id: i.tlStartF for i in next(t for t in out.tracks if t.kind == "text").items}
    assert video.tlStartF == 30            # frame 93 now plays at 0 + (93 - 63)
    assert items == {"t1": 63, "t2": 90, "t3": 150}
    assert warnings == [{"type": "time-pinned-drift", "itemId": "t2"}]
    assert _end(out) == 210
    P.validate(out)


def test_cut_material_moves_with_its_scene_and_restoring_the_order_rejoins_seams() -> None:
    # Word 3 (source 93..108) already cut: a ripple cut left seams on both sides of the join.
    proj = _project([_clip("a", 0, 93, 0, seams=[{"atF": 93, "fadeMs": 30}]),
                     _clip("b", 108, 210, 93, seams=[{"atF": 0, "fadeMs": 30}])])
    out, _ = edits.reorder_scenes(proj, ["s3", "s1", "s2"], word_frames=_FRAMES)
    assert _speech(out) == [("b-s123", 123, 210, 0), ("a", 0, 63, 87), ("a-s63", 63, 93, 150),
                            ("b", 108, 123, 180)]
    assert _end(out) == _end(proj) == 195
    seams = _seams(out)
    assert seams["a-s63"] == [30] and seams["b"] == [0]   # the cut's own join survives the move
    assert seams["b-s123"] == [87] and seams["a"] == [0]  # the new s3→s1 jump
    P.validate(out)

    back, _ = edits.reorder_scenes(out, ["s1", "s2", "s3"], word_frames=_FRAMES)
    assert _speech(back) == [("a", 0, 63, 0), ("a-s63", 63, 93, 63), ("b", 108, 123, 93),
                             ("b-s123", 123, 210, 108)]
    seams = _seams(back)
    assert 63 not in seams["a"] and 0 not in seams["a-s63"]       # continuous again: no fade dip
    assert 15 not in seams["b"] and 0 not in seams["b-s123"]
    assert seams["a-s63"] == [30] and seams["b"] == [0]           # the real cut keeps its fade
    P.validate(back)


def test_gaps_keep_their_slots_and_other_media_rides_with_the_scene_before_it() -> None:
    tail = _clip("t", 0, 30, 247, media=f"data/source/{_PREFIX}outro.mp4")
    proj = _project([_clip("a", 0, 63, 0), _clip("b", 63, 210, 100), tail])
    out, _ = edits.reorder_scenes(proj, ["s2", "s3", "s1"], word_frames=_FRAMES)
    # Slots: [s1 0..63] gap 37 [s2 100..160] [s3 160..247 + outro 247..277].
    assert _speech(out) == [("b", 63, 123, 0), ("b-s123", 123, 210, 97), ("t", 0, 30, 184),
                            ("a", 0, 63, 214)]
    assert _end(out) == _end(proj) == 277
    P.validate(out)


def test_same_order_is_a_no_op_and_a_bad_order_is_rejected() -> None:
    proj = _project([_clip("c", 0, 210, 0)])
    out, warnings = edits.reorder_scenes(proj, ["s1", "s2", "s3"], word_frames=_FRAMES)
    assert _speech(out) == _speech(proj) and warnings == []
    for bad in (["s1", "s2"], ["s1", "s2", "s2"], ["s1", "s2", "zz"]):
        with pytest.raises(ValueError):
            edits.reorder_scenes(proj, bad, word_frames=_FRAMES)
    with pytest.raises(ValueError):
        edits.reorder_scenes(proj, ["s3", "s2", "s1"], word_frames={})


def test_unaligned_words_borrow_their_neighbours_frame() -> None:
    timings: list[tuple[float | None, float | None]] = [
        (None, None), (1.0, 1.5), (None, None), (None, 2.0), (3.0, 3.5)]
    assert edits.resolve_word_frames(timings, 30) == [30, 30, 30, 30, 90]
    assert edits.resolve_word_frames([(None, None)], 30) == [0]


# ── the verb ────────────────────────────────────────────────────────────────────
def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


def _request(method: str, path: str, body: object) -> httpx.Response:
    async def go() -> httpx.Response:
        async with _client() as c:
            return await c.request(method, path, json=body)
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def wipe() -> None:
        for d in (config.TRANSCRIPTS_DIR, config.PROJECTS_DIR):
            if d.exists():
                for f in d.glob(f"{_PREFIX}*"):
                    f.unlink(missing_ok=True)
    config.TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    wipe()
    words = [{"word": f" w{i}", "start": s, "end": s + 0.4} for i, s in enumerate(_WORD_STARTS)]
    words[2] = {"word": " 2"}  # unaligned: s2's boundary resolves to word 1's frame (33)
    (config.TRANSCRIPTS_DIR / f"{_PREFIX}src.words.json").write_text(
        json.dumps({"segments": [{"start": 0.1, "end": 5.5, "words": words}]}), encoding="utf-8")
    yield
    wipe()


def _on_disk(revision: int) -> dict:
    doc = P.to_dict(_project([_clip("c", 0, 210, 0)]))
    doc.update(name=f"{_PREFIX}proj", revision=revision)
    (config.PROJECTS_DIR / f"{_PREFIX}proj.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_verb_returns_the_reordered_doc_without_committing() -> None:
    doc = _on_disk(revision=3)
    r = _request("POST", f"/api/verbs/reorder_scenes/{_PREFIX}proj",
                 {"project": doc, "order": ["s2", "s1", "s3"], "baseRevision": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["id"] for s in body["project"]["scenes"]] == ["s2", "s1", "s3"]
    clips = body["project"]["tracks"][0]["clips"]
    assert [(c["srcInF"], c["srcOutF"], c["tlStartF"]) for c in clips] == [(33, 123, 0), (0, 33, 90), (123, 210, 123)]
    assert body["warnings"] == [] and body["project"]["revision"] == 3
    assert json.loads((config.PROJECTS_DIR / f"{_PREFIX}proj.json").read_text(encoding="utf-8"))["revision"] == 3

    saved = _request("PUT", f"/api/project/{_PREFIX}proj", {"project": body["project"], "baseRevision": 3})
    assert saved.status_code == 200 and saved.json()["revision"] == 4


def test_verb_refuses_a_stale_revision_with_409() -> None:
    doc = _on_disk(revision=5)
    r = _request("POST", f"/api/verbs/reorder_scenes/{_PREFIX}proj",
                 {"project": doc, "order": ["s2", "s1", "s3"], "baseRevision": 4})
    assert r.status_code == 409 and r.json() == {"currentRevision": 5, "error": "The request conflicts with the current state.", "code": "conflict"}
    stale_put = _request("PUT", f"/api/project/{_PREFIX}proj", {"project": doc, "baseRevision": 4})
    assert stale_put.status_code == 409


def test_verb_rejects_a_bad_order_and_a_missing_field() -> None:
    doc = _on_disk(revision=1)
    bad = _request("POST", f"/api/verbs/reorder_scenes/{_PREFIX}proj",
                   {"project": doc, "order": ["s1"], "baseRevision": 1})
    assert bad.status_code == 400
    missing = _request("POST", f"/api/verbs/reorder_scenes/{_PREFIX}proj", {"project": doc, "order": ["s1"]})
    assert missing.status_code == 400
