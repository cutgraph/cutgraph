"""Windowed compile for judge/choose previews (pipeline/compile.py).

Isolated, no GPU: `build_plan(window=...)` is a pure builder — assert it filters clips to the window,
clamps each to it, and rebases every timeline frame to the window origin. `compile_preview_segment`'s
dest guard (PREVIEW_DIR/FINAL_DIR only) is asserted with ffmpeg stubbed. Hardware encoding is not exercised here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import config
from pipeline import encoder_ladder
from pipeline import compile as compile_mod


@pytest.fixture(autouse=True)
def _assume_fixture_media_has_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda _path: True)


def _doc() -> dict:
    """One long speech clip (src 0..300 @ tl 0), a text item at tl [100,160), and a second speech clip
    far outside the preview window (tl [300,360)) so windowing must DROP it."""
    return {
        "schemaVersion": 1, "name": "zzpreview", "fps": 30, "width": 1920, "height": 1080,
        "revision": 1, "transcript": None,
        "tracks": [
            {"id": "sp", "kind": "speech", "clips": [
                {"id": "c1", "media": {"source": "media/rec.mp4", "proxy": None},
                 "srcInF": 0, "srcOutF": 300, "tlStartF": 0, "gainDb": 0.0},
                {"id": "c2", "media": {"source": "media/other.mp4", "proxy": None},
                 "srcInF": 400, "srcOutF": 460, "tlStartF": 300, "gainDb": 0.0},
            ]},
            {"id": "txt", "kind": "text", "items": [
                {"id": "t1", "text": "Hook!", "preset": "hook",
                 "pos": {"x": 0.5, "y": 0.18}, "tlStartF": 100, "durF": 60},
            ]},
        ],
    }


def test_window_clamps_rebases_and_drops(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    # Window timeline frames [90, 180): c1's [90,180) slice → src 90..180, rebased to tl 0; the text
    # item [100,160) → tl [10,70) after the -90 rebase; c2 (tl 300+) is entirely outside → dropped.
    plan = compile_mod.build_plan(_doc(), window=(90, 180))
    fg = plan.filter_complex

    # c1 clamped to the window: both video and audio trim to src 3.0s..6.0s (frames 90..180 @ 30fps).
    assert "trim=start=3.000000:end=6.000000,setpts=PTS-STARTPTS" in fg      # video
    assert "atrim=start=3.000000:end=6.000000,asetpts=PTS-STARTPTS" in fg    # audio

    # Only ONE speech clip survives (c2 dropped), over the exact three-second preview canvas.
    assert plan.inputs.count("-i") == 1
    assert "other.mp4" not in " ".join(plan.inputs)
    assert "color=c=black:s=1920x1080:r=30:d=3.000000" in fg
    assert fg.count("overlay=") == 1
    assert "concat=" not in fg

    # Text enable rebased by -90 frames: tl [100,160) → t [10/30, 70/30) = [0.333, 2.333), NOT [3.333,…).
    assert "enable='between(t,0.333,2.333)'" in fg


def test_window_straddles_a_cut_between_two_clips(monkeypatch) -> None:
    """A window across the c1→c2 boundary exercises the MULTI-clip windowed path —
    the trailing clip's srcInF re-derived from its OWN origin (400 + (300-300)) and a two-input
    concat=n=2 — which the single-clip preview tests never touch. A regression rebasing srcInF against
    the window origin instead of the clip origin would move c2's source frames and be caught here."""
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    # Window [270,330) straddles the boundary at tl 300: c1 tail [270,300) + c2 head [300,330).
    plan = compile_mod.build_plan(_doc(), window=(270, 330))
    fg = plan.filter_complex

    assert plan.inputs.count("-i") == 2               # both clips' sources survive the window
    assert "other.mp4" in " ".join(plan.inputs)       # the trailing clip is kept, not dropped
    # c1 tail: clamped src frames 270..300 → 9.0..10.0s.
    assert "atrim=start=9.000000:end=10.000000" in fg
    # c2 head: srcInF re-derived from c2's OWN origin (400 + (300 - 300)) → 400..430 → 13.333333..14.333333s
    # NOT 400 + (300 - 270) (window origin), which would wrongly start at src frame 430.
    assert "atrim=start=13.333333:end=14.333333" in fg
    # Both clocked clips overlay the same two-second preview canvas without collapsing the cut.
    assert "color=c=black:s=1920x1080:r=30:d=2.000000" in fg
    assert fg.count("overlay=") == 2
    assert "concat=" not in fg


def test_no_window_is_unchanged(monkeypatch) -> None:
    """Absent a window the plan is identical to the full compile (the window parameter is purely additive)."""
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    assert compile_mod.build_plan(_doc()).filter_complex == \
        compile_mod.build_plan(_doc(), window=None).filter_complex


def test_window_preserves_trailing_and_all_gap_duration(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    doc = _doc()
    doc["tracks"][0]["clips"] = doc["tracks"][0]["clips"][:1]

    trailing = compile_mod.build_plan(doc, window=(270, 330)).filter_complex
    assert "color=c=black:s=1920x1080:r=30:d=2.000000" in trailing
    assert "trim=start=9.000000:end=10.000000" in trailing

    empty = compile_mod.build_plan(doc, window=(360, 420))
    assert empty.inputs == []
    assert "color=c=black:s=1920x1080:r=30:d=2.000000" in empty.filter_complex
    assert "anullsrc=channel_layout=stereo:sample_rate=48000:d=2.000000" in empty.filter_complex


def test_preview_segment_permits_preview_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    monkeypatch.setattr(config, "PREVIEW_DIR", tmp_path / "preview")
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    calls: list[list[str]] = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(compile_mod.subprocess, "run", _fake_run)

    dest = (tmp_path / "preview") / "abc123.mp4"
    r = compile_mod.compile_preview_segment(_doc(), 90, 180, dest)
    assert r.status == "ok"
    assert r.output_path == str(dest)
    assert calls and calls[0][calls[0].index("-c:v") + 1] == "h264_nvenc"


def test_preview_segment_rejects_out_of_tree_dest(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    r = compile_mod.compile_preview_segment(_doc(), 90, 180, Path("C:/tmp/escape.mp4"))
    assert r.status == "error"
    assert "out-of-tree" in (r.reason or "")


def test_preview_segment_rejects_empty_window(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    monkeypatch.setattr(config, "PREVIEW_DIR", tmp_path / "preview")
    r = compile_mod.compile_preview_segment(_doc(), 180, 180, (tmp_path / "preview") / "x.mp4")
    assert r.status == "error"
    assert "empty preview window" in (r.reason or "")
