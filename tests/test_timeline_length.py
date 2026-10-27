"""The video's length after a reorder, cut or restore: `edits.timeline_end_f`, the preview's
`projectDurationF` (ui/src/timeline/geometry.ts) and the export (`compile.build_plan`) share one rule.
A word-anchored cue follows its word but never lengthens the video; its tail past the end isn't played.

The cases live in tests/fixtures/timeline_length_parity.json, which ui/test/v3TimelineLength.test.tsx
holds the preview to. Six words, one per second at 30 fps (source frames 3, 33, 63, 93, 123, 153);
scenes s1@word0, s2@word2, s3@word4; a music bed on word 0 fills the 210-frame speech.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import compile as compile_mod
from pipeline import edits
from pipeline import project as P
from studio.routes import workspaces

FIXTURE = Path(__file__).parent / "fixtures" / "timeline_length_parity.json"
SRC = "data/source/talk.mp4"
FRAMES = {SRC: [3, 33, 63, 93, 123, 153]}


def _cases() -> dict[str, dict]:
    return {c["name"]: c for c in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]}


def _project(name: str) -> P.Project:
    return P.from_dict(_cases()[name]["project"])


def _speech_end(proj: P.Project) -> int:
    return max(c.tlStartF + c.srcOutF - c.srcInF for t in proj.tracks if t.kind == "speech" for c in t.clips)


def _raw_end(proj: P.Project) -> int:
    return max([c.tlStartF + c.srcOutF - c.srcInF for t in proj.tracks for c in t.clips]
               + [i.tlStartF + i.durF for t in proj.tracks for i in t.items])


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda _path: "")
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda _path: True)
    return lambda doc, **kw: compile_mod.build_plan(doc, **kw)


# ── the rule ────────────────────────────────────────────────────────────────────
def test_the_length_rule_matches_the_shared_parity_fixture() -> None:
    for name, case in _cases().items():
        assert edits.timeline_end_f(P.from_dict(case["project"])) == case["endF"], name


def test_the_fixture_edit_cases_are_what_the_verbs_produce() -> None:
    orig = _project("music-on-word-0")
    moved, _ = edits.reorder_scenes(orig, ["s2", "s3", "s1"], word_frames=FRAMES)
    cut, _ = edits.ripple_cut(orig, 63, 123, word_frames=FRAMES)
    restored, _ = edits.ripple_restore(cut, SRC, 63, 123, word_frames=FRAMES)
    cases = _cases()
    assert P.to_dict(moved) == cases["reorder-moves-the-music-word"]["project"]
    assert P.to_dict(cut) == cases["cut-shortens-the-speech"]["project"]
    assert P.to_dict(restored) == cases["restore-plays-the-bed-whole"]["project"]


def test_reordering_the_music_word_keeps_the_video_the_speech_length() -> None:
    orig = _project("music-on-word-0")
    moved, warnings = edits.reorder_scenes(orig, ["s2", "s3", "s1"], word_frames=FRAMES)
    bed = next(t for t in moved.tracks if t.kind == "audio").clips[0]
    # The bed follows word 0 (source frame 3), now 3 frames into s1's new slot at 147.
    assert bed.tlStartF == 150
    assert _raw_end(moved) == 357 > _speech_end(moved) == 210
    assert edits.timeline_end_f(moved) == edits.timeline_end_f(orig) == 210
    assert warnings == []


def test_a_cut_then_its_restore_plays_the_bed_whole_again() -> None:
    orig = _project("music-on-word-0")
    cut, _ = edits.ripple_cut(orig, 63, 123, word_frames=FRAMES)
    assert edits.timeline_end_f(cut) == _speech_end(cut) == 150
    restored, _ = edits.ripple_restore(cut, SRC, 63, 123, word_frames=FRAMES)
    bed = next(t for t in restored.tracks if t.kind == "audio").clips[0]
    assert (bed.tlStartF, bed.srcOutF - bed.srcInF) == (3, 207)
    assert edits.timeline_end_f(restored) == _raw_end(restored) == 210


def test_a_time_pin_past_the_speech_still_lengthens_the_video() -> None:
    assert edits.timeline_end_f(_project("time-pinned-bed-extends")) == 303
    assert edits.timeline_end_f(_project("unanchored-bed-extends")) == 303


# ── the export ──────────────────────────────────────────────────────────────────
def test_the_export_of_a_reordered_video_lasts_the_speech(plan) -> None:
    fg = plan(_cases()["reorder-moves-the-music-word"]["project"]).filter_complex
    assert "color=c=black:s=1920x1080:r=30:d=7.000000[vbase0]" in fg
    assert "anullsrc=channel_layout=stereo:sample_rate=48000:d=7.000000" in fg
    # The bed starts at 5 s and plays the 2 s left before the end, not its authored 6.9 s.
    assert "atrim=start=0.000000:end=2.000000,asetpts=PTS-STARTPTS" in fg
    assert "atrim=start=0.000000:end=6.900000" not in fg


def test_an_old_document_with_over_long_cues_is_clamped(plan) -> None:
    doc = _cases()["word-anchored-graphic-past-the-end"]["project"]
    doc["tracks"].append({"id": "titles", "kind": "text", "items": [{
        "id": "hook", "text": "Hook", "preset": "hook", "pos": {"x": 0.5, "y": 0.2}, "tlStartF": 150, "durF": 150,
        "anchor": {"mode": "word", "ref": {"media": SRC, "wordIdx": 5}, "offsetF": 0}}]})
    fg = plan(doc).filter_complex
    assert ":d=7.000000[vbase0]" in fg
    assert "enable='between(t,5.000,7.000)'" in fg
    # The graphic keeps its authored motion timing (120 frames) but is trimmed at the end, 30 frames in.
    assert "trim=start_frame=0:end_frame=30" in fg


def test_the_export_drops_a_cue_that_starts_after_the_end(plan) -> None:
    built = plan(_cases()["orphaned-cue-after-the-end"]["project"])
    assert not any("city.mp4" in arg for arg in built.inputs)
    assert ":d=7.000000[vbase0]" in built.filter_complex


def test_the_export_keeps_a_time_pinned_tail(plan) -> None:
    fg = plan(_cases()["time-pinned-bed-extends"]["project"]).filter_complex
    assert ":d=10.100000[vbase0]" in fg
    assert "atrim=start=0.000000:end=10.000000" in fg


def test_a_preview_window_past_the_end_plays_nothing_there(plan) -> None:
    fg = plan(_cases()["reorder-moves-the-music-word"]["project"], window=(150, 240)).filter_complex
    assert ":d=3.000000[vbase0]" in fg
    assert "atrim=start=0.000000:end=2.000000" in fg


def _band_db(path: Path, hz: int, start: float, dur: float) -> float:
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af",
         f"atrim=start={start}:duration={dur},bandpass=f={hz}:width_type=h:w=40,volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True)
    found = re.search(r"mean_volume:\s*(-?[\d.]+) dB", run.stderr)
    assert found, run.stderr
    return float(found.group(1))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_a_reordered_export_plays_each_piece_at_its_place(tmp_path: Path) -> None:
    """amix ignores timestamps: every stem must carry its offset as leading silence, or all of them
    play from 0 and the rest of the video is silent."""
    media = tmp_path / "media"
    media.mkdir()
    tone = "if(lt(t,1),0.3*sin(2*PI*440*t),if(gte(t,2),0.3*sin(2*PI*880*t),0))"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x36:r=30:d=3", "-f", "lavfi",
                    "-i", f"aevalsrc='{tone}':s=48000:d=3", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(media / "talk.mp4")], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=220:sample_rate=48000:d=3", str(media / "bed.wav")],
                   check=True, capture_output=True)
    doc = {
        "schemaVersion": 3, "name": "reordered", "fps": 30, "width": 64, "height": 36, "revision": 1,
        "tracks": [
            {"id": "speech", "kind": "speech", "clips": [
                {"id": "b", "media": {"source": "media/talk.mp4"}, "srcInF": 60, "srcOutF": 90, "tlStartF": 0},
                {"id": "a", "media": {"source": "media/talk.mp4"}, "srcInF": 0, "srcOutF": 30, "tlStartF": 30},
            ]},
            {"id": "music", "kind": "audio", "clips": [{
                "id": "bed", "media": {"source": "media/bed.wav"}, "srcInF": 0, "srcOutF": 60, "tlStartF": 45,
                "anchor": {"mode": "word", "ref": {"media": "media/talk.mp4", "wordIdx": 3}, "offsetF": 0}}]},
        ],
    }
    dest = tmp_path / "out.mp4"
    plan = compile_mod.build_plan(doc, project_dir=tmp_path)
    subprocess.run(compile_mod.build_encode_cmd(plan, dest, encoder="libx264"), check=True, capture_output=True)

    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(dest)],
                           check=True, capture_output=True, text=True)
    assert float(json.loads(probe.stdout)["format"]["duration"]) == pytest.approx(2.0, abs=0.05)
    # The second second of source (880 Hz) plays first, then the first (440 Hz), then the bed joins at 1.5 s.
    assert _band_db(dest, 880, 0.1, 0.8) > _band_db(dest, 440, 0.1, 0.8) + 12
    assert _band_db(dest, 440, 1.1, 0.3) > _band_db(dest, 880, 1.1, 0.3) + 12
    assert _band_db(dest, 220, 1.6, 0.3) > _band_db(dest, 220, 0.1, 0.8) + 12


# ── the listing ─────────────────────────────────────────────────────────────────
def test_the_video_listing_shows_the_video_length() -> None:
    assert workspaces._project_duration_s(_cases()["reorder-moves-the-music-word"]["project"]) == 7.0
    assert workspaces._project_duration_s(_cases()["empty"]["project"]) is None
    assert workspaces._project_duration_s({"tracks": "nope"}) is None
