"""A video from a written script, then its narration.

- the script -> scenes/words parser against the golden fixture the UI preview shares;
- the estimated timing against motion-core's own `timeline` numbers (the same fixture);
- alignment of a transcribed take onto the script words (insertions, deletions, substitutions);
- the narrated project: wordIdx space, scenes, cuts, reorders and word anchors survive, and it validates.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.errors import ValidationError

import config
from pipeline import contracts
from pipeline import edits
from pipeline import project as project_mod
from pipeline import script_video as sv
from pipeline.timebase import sec_to_frame

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "script_video_golden.json").read_text(encoding="utf-8"))
CASES = GOLDEN["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_parser_matches_the_golden(case: dict) -> None:
    got = [{"title": s.title, "role": s.role, "words": s.words} for s in sv.parse_script(case["script"])]
    assert got == case["scenes"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_estimate_matches_motion_core_timeline(case: dict) -> None:
    spans, duration = sv.estimate([sv.ScriptScene(**s) for s in case["scenes"]])
    flat = [t for scene in spans for span in scene for t in span]
    assert flat == pytest.approx([t for span in case["timings"] for t in span], abs=1e-9)
    assert duration == pytest.approx(case["duration"], abs=1e-9)


def test_word_dur_ports_the_motion_core_rule() -> None:
    assert sv.word_dur("um,") == 0.42
    assert sv.word_dur("lemon") == pytest.approx(0.17 + 0.047 * 5)
    assert sv.word_dur("lemon:") == pytest.approx(0.17 + 0.047 * 5 + 0.22)
    assert sv.word_dur("done?") == pytest.approx(0.17 + 0.047 * 4 + 0.38)
    assert sv.word_dur("don't") == pytest.approx(0.17 + 0.047 * 5)


SCRIPT = """## Hook: A quick supper
Have you ever cooked pasta with fresh garlic and a little lemon?

## Prepare the ingredients
Boil the water while someone chops garlic beside the kitchen sink.

## Wrap: Serve the pasta
Fresh lemon adds brightness. That is a simple supper to share."""


def _scenes() -> list[sv.ScriptScene]:
    return sv.parse_script(SCRIPT)


def test_script_transcript_is_the_script_in_sentences_with_estimated_timing() -> None:
    scenes = _scenes()
    result = sv.script_transcript(scenes)
    result.validate()
    words = [w for seg in result.segments for w in seg.words]
    assert [w.word for w in words] == [w for s in scenes for w in s.words]
    assert [seg.text for seg in result.segments] == [
        "Have you ever cooked pasta with fresh garlic and a little lemon?",
        "Boil the water while someone chops garlic beside the kitchen sink.",
        "Fresh lemon adds brightness.",
        "That is a simple supper to share.",
    ]
    spans, duration = sv.estimate(scenes)
    assert words[0].start == pytest.approx(sv.LEAD, abs=1e-3)
    assert [t for w in words for t in (w.start, w.end)] == pytest.approx(
        [t for scene in spans for span in scene for t in span], abs=6e-4)
    assert result.duration == pytest.approx(duration, abs=1e-3)


def test_build_project_is_an_animatic_that_validates() -> None:
    scenes = _scenes()
    proj = sv.build_project("pasta", scenes, "data/source/pasta_script.mp4", 300, "garden")
    project_mod.validate(proj)
    assert proj.narration == "estimated"
    assert proj.captions == {"enabled": True}
    assert proj.workspaceId == "garden"
    assert [(s.title, s.role, s.startWord, s.shot) for s in proj.scenes] == [
        ("A quick supper", "Hook", 0, {"mode": "animation"}),
        ("Prepare the ingredients", "Beat", 12, {"mode": "animation"}),
        ("Serve the pasta", "Wrap", 23, {"mode": "animation"}),
    ]
    clip = proj.tracks[0].clips[0]
    assert (proj.tracks[0].kind, clip.srcInF, clip.srcOutF, clip.tlStartF) == ("speech", 0, 300, 0)
    assert project_mod.to_dict(project_mod.from_dict(project_mod.to_dict(proj))) == project_mod.to_dict(proj)


def test_build_project_stores_the_script_title() -> None:
    proj = sv.build_project("pasta", _scenes(), "data/source/pasta_script.mp4", 300, None, title="A simple pasta supper")
    project_mod.validate(proj)
    assert proj.title == "A simple pasta supper"
    assert proj.name == "pasta"


def test_build_project_omits_title_when_none() -> None:
    proj = sv.build_project("pasta", _scenes(), "data/source/pasta_script.mp4", 300, None)
    assert proj.title is None
    assert "title" not in project_mod.to_dict(proj)


def test_narration_must_be_a_known_state() -> None:
    proj = sv.build_project("pasta", _scenes(), "data/source/pasta_script.mp4", 300, None)
    proj.narration = "guessed"
    with pytest.raises(ValidationError, match="narration"):
        project_mod.validate(proj)


# ── alignment ──────────────────────────────────────────────────────────────────

def _tok(word: str, start: float, end: float, score: float = 0.9) -> contracts.WordToken:
    return contracts.WordToken(word=word, start=start, end=end, score=score)


def test_align_tolerates_insertions_deletions_and_substitutions() -> None:
    script = "So start with one fresh lemon: the garlic, the pasta.".split()
    take = [
        _tok("Okay,", 0.0, 0.3),       # inserted ad-lib
        _tok("so", 0.5, 0.7),
        _tok("start", 0.7, 1.0),
        _tok("with", 1.0, 1.2),
        # "one" was never said
        _tok("fresh", 1.4, 1.7),
        _tok("melon.", 1.7, 2.1),      # "lemon:" misheard, one for one
        _tok("The", 2.6, 2.7),
        _tok("garlic,", 2.7, 3.1),
        _tok("um", 3.1, 3.3),          # inserted filler
        _tok("the", 3.4, 3.5),
        _tok("pasta.", 3.5, 4.1),
    ]
    aligned = sv.align(script, take, 5.0)
    assert [w.word for w in aligned.words] == script
    assert aligned.matched == 8
    timing = {w.word: (w.start, w.end) for w in aligned.words}
    assert timing["So"] == (0.5, 0.7)
    assert timing["fresh"] == (1.4, 1.7)
    assert timing["lemon:"] == (1.7, 2.1)
    assert timing["pasta."] == (3.5, 4.1)
    assert timing["one"] == (1.2, 1.4)  # interpolated between its heard neighbours
    starts = [w.start for w in aligned.words]
    assert starts == sorted(starts)


def test_align_spreads_unheard_runs_by_estimated_duration_and_to_the_edges() -> None:
    script = "Hello there so start with a longer ending here".split()
    take = [_tok("so", 2.0, 2.2), _tok("start", 2.2, 2.6), _tok("with", 2.6, 2.8)]
    aligned = sv.align(script, take, 6.0)
    hello, there = aligned.words[0], aligned.words[1]
    assert hello.start == 0.0 and there.end == pytest.approx(2.0)
    assert (there.end - there.start) / (hello.end - hello.start) == pytest.approx(
        sv.word_dur("there") / sv.word_dur("Hello"), abs=0.02)
    tail = aligned.words[5:]
    assert tail[0].start == pytest.approx(2.8) and tail[-1].end == pytest.approx(6.0)
    assert all(w.score == 0.0 for w in [hello, there, *tail])


# ── the narrated project ───────────────────────────────────────────────────────

SOURCE = "data/source/pasta_script.mp4"
TAKE = "data/source/pasta_take1.mp4"


def _estimated() -> tuple[project_mod.Project, contracts.TranscriptResult]:
    scenes = _scenes()
    script = sv.script_transcript(scenes)
    proj = sv.build_project("pasta", scenes, SOURCE, sec_to_frame(script.duration, config.DEFAULT_FPS), None)
    return proj, script


def _take_of(script: contracts.TranscriptResult, *, skip: frozenset[int] = frozenset()) -> tuple[contracts.TranscriptResult, int]:
    """A 'recording' of the script: slower than the estimate, starting late, skipping some words."""
    words = [w for seg in script.segments for w in seg.words]
    tokens = [
        _tok(w.word.lower(), round(1.0 + w.start * 1.3, 3), round(1.0 + w.end * 1.3, 3))
        for i, w in enumerate(words) if i not in skip
    ]
    duration = round(1.0 + script.duration * 1.3 + 0.5, 3)
    take = contracts.TranscriptResult("en", duration, [contracts.TranscriptSegment(0.0, duration, "", tokens)])
    return take, sec_to_frame(duration, 30)


def _placement(proj: project_mod.Project, media: str, words: list[contracts.WordToken]) -> list[int | None]:
    """Each word's timeline frame through the speech clips of `media`, or None when it is cut."""
    clips = next(t for t in proj.tracks if t.kind == "speech").clips
    out: list[int | None] = []
    for w in words:
        f = sec_to_frame(w.start, proj.fps)
        clip = next((c for c in clips if c.media.source == media and c.srcInF <= f < c.srcOutF), None)
        out.append(None if clip is None else clip.tlStartF + f - clip.srcInF)
    return out


def test_narration_keeps_the_word_space_and_scenes_and_retimes_the_words() -> None:
    proj, script = _estimated()
    proj.scenes[1].shot = {"mode": "animation", "cueWord": 20}
    take, frames = _take_of(script, skip=frozenset({3, 17}))
    narrated, retimed, stats = sv.apply_narration(proj, script, take, TAKE, frames, "outputs/proxies/pasta_take1_540.mp4")
    old = [w for seg in script.segments for w in seg.words]
    new = [w for seg in retimed.segments for w in seg.words]
    assert [w.word for w in new] == [w.word for w in old]
    assert [len(seg.words) for seg in retimed.segments] == [len(seg.words) for seg in script.segments]
    assert new[0].start == pytest.approx(1.0 + old[0].start * 1.3, abs=1e-3)
    assert new[3].start > new[2].start and new[3].end <= new[4].start  # a skipped word is interpolated in place
    assert stats == {"matched": len(old) - 2, "total": len(old)}
    assert retimed.duration == pytest.approx(frames / 30, abs=1e-3)
    assert [(s.id, s.startWord, s.role, s.title, s.shot) for s in narrated.scenes] == \
        [(s.id, s.startWord, s.role, s.title, s.shot) for s in proj.scenes]
    assert narrated.narration == "recorded"
    [clip] = narrated.tracks[0].clips
    assert (clip.media.source, clip.media.proxy, clip.srcInF, clip.srcOutF) == \
        (TAKE, "outputs/proxies/pasta_take1_540.mp4", 0, frames)
    project_mod.validate(narrated)


@pytest.mark.parametrize(("text", "rounds_down"), [("Pasta", True), ("Garlic", False)])
def test_narration_maps_the_rounded_source_end_to_the_recording_end(text: str, rounds_down: bool) -> None:
    scenes = sv.parse_script(text)
    script = sv.script_transcript(scenes)
    source_frames = sec_to_frame(script.duration, config.DEFAULT_FPS)
    assert (source_frames < script.duration * config.DEFAULT_FPS) is rounds_down
    proj = sv.build_project("supper", scenes, SOURCE, source_frames, None)
    take = contracts.TranscriptResult("en", 4.0, [contracts.TranscriptSegment(
        0.5, 1.0, text, [_tok(text, 0.5, 1.0)],
    )])
    take_frames = sec_to_frame(take.duration, proj.fps)

    narrated, _, _ = sv.apply_narration(proj, script, take, TAKE, take_frames, None)

    [clip] = narrated.tracks[0].clips
    assert (clip.srcInF, clip.srcOutF) == (0, take_frames)
    assert clip.tlStartF + clip.srcOutF - clip.srcInF == take_frames


def test_narration_keeps_cuts_reorders_and_word_anchors() -> None:
    proj, script = _estimated()
    old_words = [w for seg in script.segments for w in seg.words]
    fps = proj.fps
    frames_of = {SOURCE: [sec_to_frame(w.start, fps) for w in old_words]}
    # Cut "ever cooked pasta" (words 2..4) and play the Wrap before "Prepare the ingredients".
    proj, _ = edits.ripple_cut(proj, frames_of[SOURCE][2], frames_of[SOURCE][5], word_frames=frames_of)
    proj, _ = edits.reorder_scenes(proj, ["scene-1", "scene-3", "scene-2"], word_frames=frames_of)
    anchored = project_mod.TextItem(
        id="t1", text="Brief", preset="lower-third", pos={"x": 0.5, "y": 0.8}, tlStartF=0, durF=30,
        anchor=project_mod.Anchor(mode="word", ref={"media": SOURCE, "wordIdx": 16}, offsetF=0),
    )
    proj.tracks.append(project_mod.Track(id="text", kind="text", items=[anchored]))
    before = _placement(proj, SOURCE, old_words)
    assert len(proj.tracks[0].clips) > 1

    take, frames = _take_of(script)
    narrated, retimed, _stats = sv.apply_narration(proj, script, take, TAKE, frames, None)
    new_words = [w for seg in retimed.segments for w in seg.words]
    after = _placement(narrated, TAKE, new_words)

    assert [a is None for a in after] == [b is None for b in before]  # the same words are cut
    kept_before = {i: f for i, f in enumerate(before) if f is not None}
    kept_after = {i: f for i, f in enumerate(after) if f is not None}
    assert sorted(kept_before, key=kept_before.__getitem__) == sorted(kept_after, key=kept_after.__getitem__)
    assert all(c.media.source == TAKE for c in narrated.tracks[0].clips)
    item = narrated.tracks[1].items[0]
    assert item.anchor is not None and item.anchor.ref == {"media": TAKE, "wordIdx": 16}
    assert item.tlStartF == after[16]
    project_mod.validate(narrated)


def test_a_take_of_something_else_is_refused() -> None:
    proj, script = _estimated()
    words = "completely unrelated audio about cooking pasta with garlic and olive oil tonight".split()
    take = contracts.TranscriptResult("en", 9.0, [contracts.TranscriptSegment(
        0.0, 9.0, "", [_tok(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(words)])])
    with pytest.raises(sv.NarrationError, match="script words were heard"):
        sv.apply_narration(proj, script, take, TAKE, 270, None)


def test_load_transcript_reads_the_sidecar_it_is_written_as(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import transcribe

    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path)
    result = sv.script_transcript(_scenes())
    transcribe._write_outputs(result, "pasta_script")  # noqa: SLF001 - the sidecar format itself
    assert sv.load_transcript(tmp_path / "pasta_script.words.json") == result
    assert (tmp_path / "pasta_script.srt").read_text(encoding="utf-8").startswith("1\n00:00:00,450 --> ")
