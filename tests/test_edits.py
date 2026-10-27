"""Unit tests for the pure ripple-cut engine (pipeline/edits.py).

No ffmpeg/torch/numpy — timeline surgery only. Models are built directly from the project.py
dataclasses. Every case asserts the returned Project passes validate() (ripple_cut calls it, so an
invalid result raises), plus the specific frame/seam/warning contract for that cut shape.
"""
from __future__ import annotations

import config
from pipeline import edits
from pipeline import project as P
from pipeline.project import Anchor, Clip, GraphicItem, Media, Project, TextItem, Track


def _clip(cid: str, src_in: int, src_out: int, tl: int, *, media: str = "data/source/a.mp4",
          anchor: Anchor | None = None, seams: list[dict] | None = None) -> Clip:
    return Clip(id=cid, media=Media(source=media), srcInF=src_in, srcOutF=src_out,
                tlStartF=tl, anchor=anchor, seams=seams)


def _speech(clips: list[Clip], extra: list[Track] | None = None) -> Project:
    return Project(name="t", tracks=[Track(id="s", kind="speech", clips=clips), *(extra or [])])


def _all_seams(proj: Project) -> list[dict]:
    return [sm for tr in proj.tracks for c in tr.clips for sm in (c.seams or [])]


def _speech_clips(proj: Project) -> list[Clip]:
    return next(t for t in proj.tracks if t.kind == "speech").clips


# 1 ── cut fully inside one clip splits it in two ────────────────────────────────
def test_cut_inside_clip_splits():
    proj = _speech([_clip("c1", 0, 100, 0)])
    out, warns = edits.ripple_cut(proj, 40, 60)
    clips = _speech_clips(out)
    assert [c.id for c in clips] == ["c1", "c1-r40"]
    left, right = clips
    assert (left.srcInF, left.srcOutF, left.tlStartF) == (0, 40, 0)
    assert (right.srcInF, right.srcOutF, right.tlStartF) == (60, 100, 40)
    assert right.tlStartF == 40  # join point == startF
    assert warns == []
    P.validate(out)


# 2 ── cut spanning two adjacent clips: tail trim + head trim + shift ─────────────
def test_cut_spanning_two_clips():
    proj = _speech([
        _clip("c1", 0, 50, 0),
        _clip("c2", 100, 150, 50),
        _clip("c3", 200, 240, 100),
    ])
    out, _ = edits.ripple_cut(proj, 30, 70)
    c1, c2, c3 = _speech_clips(out)
    assert c1.srcOutF == 30 and c1.tlStartF == 0          # tail trimmed
    assert c2.srcInF == 120 and c2.tlStartF == 30         # head trimmed, lands at join
    assert c3.tlStartF == 60                              # shifted left by 40
    assert c1.seams and c1.seams[-1]["atF"] == 30         # tail seam
    assert c2.seams and c2.seams[0]["atF"] == 0           # head seam


# 3 ── cut aligned to a clip boundary: clean tail trim, single seam ──────────────
def test_cut_at_clip_boundary_one_seam():
    proj = _speech([_clip("c1", 0, 50, 0), _clip("c2", 100, 150, 50)])
    out, _ = edits.ripple_cut(proj, 30, 50)
    c1, c2 = _speech_clips(out)
    assert c1.srcOutF == 30
    assert c2.tlStartF == 30                              # shifted left by 20
    assert c2.seams is None                               # pure shift adds no seam
    assert len(_all_seams(out)) == 1


# 4 ── cut removing a whole clip drops it and shifts later clips by its length ────
def test_cut_removes_whole_clip():
    proj = _speech([
        _clip("c1", 0, 30, 0),
        _clip("c2", 100, 150, 30),
        _clip("c3", 200, 240, 80),
    ])
    out, _ = edits.ripple_cut(proj, 30, 80)
    clips = _speech_clips(out)
    assert [c.id for c in clips] == ["c1", "c3"]
    assert clips[1].tlStartF == 30                        # shifted left by 50


# 5 ── cut of the head of the first clip advances srcInF, normalizes tlStartF ─────
def test_cut_head_of_first_clip():
    proj = _speech([_clip("c1", 10, 60, 0)])
    out, _ = edits.ripple_cut(proj, 0, 20)
    (c1,) = _speech_clips(out)
    assert c1.srcInF == 30 and c1.tlStartF == 0
    assert c1.seams and c1.seams[0]["atF"] == 0


# 6 ── cut of the tail of the last clip drops srcOutF, no later clips ─────────────
def test_cut_tail_of_last_clip():
    proj = _speech([_clip("c1", 0, 50, 0), _clip("c2", 100, 160, 50)])
    out, _ = edits.ripple_cut(proj, 80, 110)
    c1, c2 = _speech_clips(out)
    assert c2.srcOutF == 130
    assert c2.seams and c2.seams[-1]["atF"] == c2.srcOutF - c2.srcInF


# 7 ── cutting an anchor word orphans it; the item does not move ─────────────────
def test_cut_anchor_word_orphans():
    broll = Track(id="v", kind="video", clips=[
        _clip("b1", 0, 30, 200, media="data/source/broll.mp4",
              anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 1})),
    ])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[broll])
    out, warns = edits.ripple_cut(proj, 40, 60, word_frames={"data/source/a.mp4": [10, 50, 90]})
    assert {"type": "orphaned-anchor", "itemId": "b1"} in warns
    b1 = next(t for t in out.tracks if t.kind == "video").clips[0]
    assert b1.tlStartF == 200                             # unmoved


# 8 ── word-anchored item after the cut moves with its surviving word ────────────
def test_word_anchor_recomputes_after_cut():
    broll = Track(id="v", kind="video", clips=[
        _clip("b1", 0, 30, 200, media="data/source/broll.mp4",
              anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 2})),
    ])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[broll])
    out, warns = edits.ripple_cut(proj, 20, 40, word_frames={"data/source/a.mp4": [10, 50, 90]})
    b1 = next(t for t in out.tracks if t.kind == "video").clips[0]
    assert b1.tlStartF == 70                              # word 90, shifted left by 20
    assert warns == []


def test_word_anchor_recomputes_graphic_after_cut():
    graphic = GraphicItem(
        id="g1", componentId="callout", componentVersion=1,
        props={"label": "Note", "body": "Read the notes", "accent": "#4cc9f0"},
        pos={"x": 0.5, "y": 0.5}, scale=1, tlStartF=70, durF=20,
        anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 2}),
    )
    graphics = Track(id="graphics", kind="text", items=[graphic])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[graphics])
    out, warns = edits.ripple_cut(proj, 20, 40, word_frames={"data/source/a.mp4": [10, 50, 90]})
    result = next(t for t in out.tracks if t.kind == "text").items[0]
    assert isinstance(result, GraphicItem)
    assert result.tlStartF == 70
    assert warns == []
    P.validate(out)


# 9 ── time-pinned item overlapping the cut warns and stays put ──────────────────
def test_time_pinned_crossing_warns():
    text = Track(id="tx", kind="text", items=[
        TextItem(id="t1", text="hi", preset="p", pos={"x": 0.5, "y": 0.5},
                 tlStartF=40, durF=20, anchor=None),
    ])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[text])
    out, warns = edits.ripple_cut(proj, 50, 55)
    assert {"type": "time-pinned-crossing", "itemId": "t1"} in warns
    t1 = next(t for t in out.tracks if t.kind == "text").items[0]
    assert t1.tlStartF == 40                              # unmoved


# 9b ── time-pinned item AFTER the cut drifts: content ripples under it, it warns and stays put ──
def test_time_pinned_drift_when_cut_before():
    text = Track(id="tx", kind="text", items=[
        TextItem(id="t1", text="hi", preset="p", pos={"x": 0.5, "y": 0.5},
                 tlStartF=200, durF=30, anchor=None),
    ])
    proj = _speech([_clip("c1", 0, 300, 0)], extra=[text])
    out, warns = edits.ripple_cut(proj, 50, 90)
    assert {"type": "time-pinned-drift", "itemId": "t1"} in warns
    assert {"type": "time-pinned-crossing", "itemId": "t1"} not in warns
    t1 = next(t for t in out.tracks if t.kind == "text").items[0]
    assert t1.tlStartF == 200                             # does NOT move — the silent-drift signal
    P.validate(out)


# 9c ── time-pinned item BEFORE the cut is undisturbed: no drift, no crossing, no warning ────────
def test_time_pinned_no_warning_when_cut_after():
    text = Track(id="tx", kind="text", items=[
        TextItem(id="t1", text="hi", preset="p", pos={"x": 0.5, "y": 0.5},
                 tlStartF=40, durF=20, anchor=None),
    ])
    proj = _speech([_clip("c1", 0, 300, 0)], extra=[text])
    out, warns = edits.ripple_cut(proj, 80, 90)
    assert warns == []                                    # cut is entirely after the item
    t1 = next(t for t in out.tracks if t.kind == "text").items[0]
    assert t1.tlStartF == 40
    P.validate(out)


# 10 ── two touching cuts collapse to one join (seams dedup) ─────────────────────
def test_adjacent_cuts_single_join():
    proj = _speech([_clip("c1", 0, 100, 0)])
    out, _ = edits.apply_cuts(proj, [(10, 20), (20, 30)])
    left, right = _speech_clips(out)
    assert left.tlStartF + (left.srcOutF - left.srcInF) == right.tlStartF   # they meet
    assert len(_all_seams(out)) == 2                       # one tail + one head, no stacking
    assert left.seams and left.seams[-1]["atF"] == left.srcOutF - left.srcInF
    assert right.seams and right.seams[0]["atF"] == 0


# 11 ── ripple_cut never mutates its input ──────────────────────────────────────
def test_input_not_mutated():
    proj = _speech([_clip("c1", 0, 100, 0)])
    before = P.to_dict(proj)
    edits.ripple_cut(proj, 40, 60)
    assert P.to_dict(proj) == before


# 12 ── every recorded seam uses the default fade and a legal in-clip offset ─────
def test_seam_fade_and_bounds():
    proj = _speech([_clip("c1", 0, 100, 0)])
    out, _ = edits.ripple_cut(proj, 40, 60)
    seams_seen = 0
    for c in _speech_clips(out):
        dur = c.srcOutF - c.srcInF
        for sm in c.seams or []:
            assert sm["fadeMs"] == config.EDITS_SEAM_FADE_MS
            assert 0 <= sm["atF"] <= dur
            seams_seen += 1
    assert seams_seen == 2
    P.validate(out)


# 13 ── apply_cuts folds right-to-left, matching sequential ripple_cuts ──────────
def test_apply_cuts_equals_sequential():
    proj = _speech([_clip("c1", 0, 300, 0)])
    ranges = [(20, 40), (100, 120), (200, 220)]
    batched, _ = edits.apply_cuts(proj, ranges)
    seq = proj
    for rng in sorted(ranges, reverse=True):
        seq, _ = edits.ripple_cut(seq, *rng)
    assert P.to_dict(batched) == P.to_dict(seq)


# 14 ── b-roll on a video track is untouched when its anchor word is before the cut
def test_broll_untouched_when_word_outside_cut():
    broll = Track(id="v", kind="video", clips=[
        _clip("b1", 0, 30, 10, media="data/source/broll.mp4",
              anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 0})),
    ])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[broll])
    out, warns = edits.ripple_cut(proj, 60, 80, word_frames={"data/source/a.mp4": [10, 50, 90]})
    b1 = next(t for t in out.tracks if t.kind == "video").clips[0]
    assert (b1.srcInF, b1.srcOutF) == (0, 30)             # b-roll media never cut
    assert b1.tlStartF == 10                              # word 10 unmoved -> same position
    assert warns == []


# ── ripple_cut must NEVER raise on a valid input project ─────────────────────
# two word-anchored b-roll clips, one orphaned (word cut) and one shifted left
# into it. The recompute collides; the moved clip reverts to its old slot with an anchor-collision
# warning rather than letting validate() raise 'overlap in track v' (which the verb turns into 400).
def test_anchor_collision_reverts_moved_clip_instead_of_raising():
    broll = Track(id="v", kind="video", clips=[
        _clip("b1", 0, 10, 0, media="data/source/broll.mp4",
              anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 0})),   # word 8 → cut
        _clip("b2", 0, 10, 20, media="data/source/broll.mp4",
              anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 1})),   # word 12 → survives
    ])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[broll])
    out, warns = edits.ripple_cut(proj, 5, 10, word_frames={"data/source/a.mp4": [8, 12]})
    by = {c.id: c for c in next(t for t in out.tracks if t.kind == "video").clips}
    assert {"type": "orphaned-anchor", "itemId": "b1"} in warns   # word 8 was inside [5,10)
    assert {"type": "anchor-collision", "itemId": "b2"} in warns   # would-be overlap reverted
    assert by["b1"].tlStartF == 0                                  # orphan pinned at its old slot
    assert by["b2"].tlStartF == 20                                 # reverted to its old slot
    P.validate(out)                                               # invariant holds, no raise


# a negative anchor.offsetF whose word survives a left-shift would drive
# tlStartF below 0 (validate: 'tlStartF must be an int >= 0'); the clamp keeps the cut applying.
def test_negative_offset_anchor_clamped_to_zero():
    broll = Track(id="v", kind="video", clips=[
        _clip("b1", 0, 30, 50, media="data/source/broll.mp4",
              anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 0}, offsetF=-45)),
    ])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[broll])
    out, warns = edits.ripple_cut(proj, 10, 20, word_frames={"data/source/a.mp4": [50]})
    b1 = next(t for t in out.tracks if t.kind == "video").clips[0]
    assert b1.tlStartF == 0                                        # 10 + (50-20) + (-45) = -5 → 0
    assert warns == []
    P.validate(out)


# ── apply_cuts normalizes a strictly-overlapping batch to the union (no over-removal) ──
def test_apply_cuts_overlapping_batch_equals_merged_single():
    proj = _speech([_clip("c1", 0, 300, 0)])
    over, _ = edits.apply_cuts(proj, [(50, 150), (100, 200)])
    union, _ = edits.ripple_cut(proj, 50, 200)
    assert P.to_dict(over) == P.to_dict(union)      # merged first → exactly the union cut, no drift


def test_apply_cuts_unsorted_overlapping_batch_still_merges():
    proj = _speech([_clip("c1", 0, 300, 0)])
    a, _ = edits.apply_cuts(proj, [(100, 200), (50, 150), (120, 130)])
    union, _ = edits.ripple_cut(proj, 50, 200)
    assert P.to_dict(a) == P.to_dict(union)


# ── ripple_restore: the inverse of ripple_cut (Script lens ↺ Restore) ────────────
def test_restore_undoes_a_cut_inside_one_clip():
    proj = _speech([_clip("c1", 0, 100, 0)])
    cut, _ = edits.ripple_cut(proj, 40, 60)
    back, warns = edits.ripple_restore(cut, "data/source/a.mp4", 40, 60)
    assert P.to_dict(back) == P.to_dict(proj)
    assert warns == []


def test_restore_part_of_a_cut_inserts_a_piece_with_seams():
    proj = _speech([_clip("c1", 0, 100, 0)])
    cut, _ = edits.ripple_cut(proj, 20, 60)                # c1 [0,20)@0, c1-r20 [60,100)@20
    out, _ = edits.ripple_restore(cut, "data/source/a.mp4", 30, 40)
    clips = _speech_clips(out)
    assert [(c.srcInF, c.srcOutF, c.tlStartF) for c in clips] == [(0, 20, 0), (30, 40, 20), (60, 100, 30)]
    assert clips[1].id == "c1-u30"
    assert clips[1].seams == [{"atF": 0, "fadeMs": config.EDITS_SEAM_FADE_MS},
                              {"atF": 10, "fadeMs": config.EDITS_SEAM_FADE_MS}]


def test_restore_extends_the_next_clip_when_nothing_precedes():
    proj = _speech([_clip("c1", 0, 100, 0)])
    cut, _ = edits.ripple_cut(proj, 0, 30)
    out, _ = edits.ripple_restore(cut, "data/source/a.mp4", 10, 30)
    (only,) = _speech_clips(out)
    assert (only.id, only.srcInF, only.srcOutF, only.tlStartF) == ("c1", 10, 100, 0)
    assert only.seams is None


def test_restore_skips_source_that_is_still_kept():
    proj = _speech([_clip("c1", 0, 100, 0)])
    cut, _ = edits.apply_cuts(proj, [(10, 20), (40, 50)])
    out, _ = edits.ripple_restore(cut, "data/source/a.mp4", 0, 100)
    assert P.to_dict(out) == P.to_dict(proj)


def test_restore_brings_an_orphaned_word_anchor_back():
    broll = Track(id="v", kind="video", clips=[
        _clip("b1", 0, 10, 45, media="data/source/broll.mp4",
              anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 1})),
        _clip("b2", 0, 10, 70, media="data/source/broll.mp4",
              anchor=Anchor(mode="word", ref={"media": "data/source/a.mp4", "wordIdx": 2})),
    ])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[broll])
    frames = {"data/source/a.mp4": [10, 50, 90]}
    cut, warns = edits.ripple_cut(proj, 40, 60, word_frames=frames)
    assert {"type": "orphaned-anchor", "itemId": "b1"} in warns
    back, warns = edits.ripple_restore(cut, "data/source/a.mp4", 40, 60, word_frames=frames)
    by = {c.id: c for c in next(t for t in back.tracks if t.kind == "video").clips}
    assert (by["b1"].tlStartF, by["b2"].tlStartF) == (50, 90)
    assert warns == []


def test_restore_warns_time_pins_after_the_insert():
    title = Track(id="t", kind="text", items=[
        TextItem(id="x1", text="Hi", preset="lower-third", pos={"x": 0.5, "y": 0.8},
                 tlStartF=50, durF=10, anchor=Anchor(mode="time")),
    ])
    proj = _speech([_clip("c1", 0, 100, 0)], extra=[title])
    cut, _ = edits.ripple_cut(proj, 20, 40)
    out, warns = edits.ripple_restore(cut, "data/source/a.mp4", 20, 40)
    assert {"type": "time-pinned-drift", "itemId": "x1"} in warns
    assert next(t for t in out.tracks if t.kind == "text").items[0].tlStartF == 50


def test_restore_never_mutates_its_input():
    cut, _ = edits.ripple_cut(_speech([_clip("c1", 0, 100, 0)]), 40, 60)
    before = P.to_dict(cut)
    edits.ripple_restore(cut, "data/source/a.mp4", 40, 60)
    assert P.to_dict(cut) == before
