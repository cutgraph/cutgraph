"""Silence detection and source-frame proposal tests.

Import-light: torch is never imported (the module keeps it lazy) so this collects without optional inference dependencies. The
pure reducer `gaps_to_proposals` is exercised directly; the silencedetect ffmpeg path is covered by a
mocked-subprocess logging test plus a `skipif(ffmpeg)`-guarded real integration.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

import config
from pipeline import silence
from pipeline.timebase import sec_to_frame

FPS = 30
_HAS_FFMPEG = shutil.which("ffmpeg") is not None


# ── gaps_to_proposals: the pure, frame-quantized reducer ─────────────────────────
def test_breath_padding_shrinks_the_interior() -> None:
    # A 2.0 s gap [1.0, 3.0]: 150 ms is kept each side, so the cut is the interior only.
    cuts = silence.gaps_to_proposals([(1.0, 3.0)], FPS)["cuts"]
    assert len(cuts) == 1
    c = cuts[0]
    assert c["srcStartF"] > sec_to_frame(1.0, FPS)   # padded inward from the gap start
    assert c["srcEndF"] < sec_to_frame(3.0, FPS)     # and inward from the gap end
    assert c["srcStartF"] == sec_to_frame(1.15, FPS) == 35
    assert c["srcEndF"] == sec_to_frame(2.85, FPS) == 86


def test_sub_min_pause_gap_dropped() -> None:
    # 0.5 s < SILENCE_MIN_PAUSE_S (0.9): not a proposal at all.
    assert silence.gaps_to_proposals([(1.0, 1.5)], FPS)["cuts"] == []


def test_gap_at_exactly_min_pause_dropped() -> None:
    # The boundary is exclusive: gap == min_pause is not "longer than", so it is dropped.
    assert silence.gaps_to_proposals([(0.0, config.SILENCE_MIN_PAUSE_S)], FPS)["cuts"] == []


def test_kept_range_overlap_filtered() -> None:
    # A user-kept range covering the proposed cut suppresses it (never re-propose a dismissed pause).
    base = silence.gaps_to_proposals([(1.0, 3.0)], FPS)["cuts"][0]
    kept = [(base["srcStartF"] + 1, base["srcEndF"] - 1)]
    assert silence.gaps_to_proposals([(1.0, 3.0)], FPS, kept_ranges=kept)["cuts"] == []
    # A kept range that does NOT overlap leaves it untouched.
    far = [(base["srcEndF"] + 100, base["srcEndF"] + 200)]
    assert len(silence.gaps_to_proposals([(1.0, 3.0)], FPS, kept_ranges=far)["cuts"]) == 1


def test_frame_quantization_matches_timebase() -> None:
    cuts = silence.gaps_to_proposals([(0.0, 2.0)], FPS)["cuts"]
    c = cuts[0]
    assert c["srcStartF"] == sec_to_frame(0.15, FPS)
    assert c["srcEndF"] == sec_to_frame(1.85, FPS)
    assert isinstance(c["srcStartF"], int) and isinstance(c["srcEndF"], int)


def test_conf_formula_scales_with_gap_length() -> None:
    # conf = min(1.0, (g1-g0) / (2*min_pause)); 1.35 s = 0.75, ≥1.8 s saturates at 1.0.
    assert silence.gaps_to_proposals([(0.0, 1.35)], FPS)["cuts"][0]["conf"] == 0.75
    assert silence.gaps_to_proposals([(0.0, 5.0)], FPS)["cuts"][0]["conf"] == 1.0


def test_proposal_shape_is_source_space() -> None:
    c = silence.gaps_to_proposals([(1.0, 3.0)], FPS)["cuts"][0]
    # field names are explicitly SOURCE-space (srcStartF/srcEndF), not timeline startF/endF.
    assert set(c) == {"srcStartF", "srcEndF", "durSec", "kind", "conf"}
    assert c["kind"] == "pause"
    assert c["durSec"] == 1.7


# ── _speech_to_gaps: pure Silero-complement transform (was uncovered) ────────
def test_speech_to_gaps_returns_interior_gaps_only() -> None:
    # Speech spans [0,1],[2,3],[5,6]: interior gaps are (1,2) and (3,5); the leading (<0) and
    # trailing (>6) edge silence are NOT pauses to cut, so they never appear.
    assert silence._speech_to_gaps([(0.0, 1.0), (2.0, 3.0), (5.0, 6.0)]) == [(1.0, 2.0), (3.0, 5.0)]


def test_speech_to_gaps_drops_touching_and_out_of_order_spans() -> None:
    # Back-to-back spans (s1 == e0) leave no gap; an overlapping/out-of-order pair (s1 < e0) is
    # guarded out by s1 > e0 rather than emitting a negative-length gap.
    assert silence._speech_to_gaps([(0.0, 1.0), (1.0, 2.0)]) == []
    assert silence._speech_to_gaps([(0.0, 2.0), (1.5, 3.0)]) == []


def test_speech_to_gaps_empty_and_single() -> None:
    assert silence._speech_to_gaps([]) == []
    assert silence._speech_to_gaps([(0.0, 1.0)]) == []


def test_silero_loads_wheel_model_and_returns_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    model, audio = object(), object()

    def read(path, *, sampling_rate):
        assert path == "sample.wav"
        assert sampling_rate == 16000
        return audio

    def timestamps(received_audio, received_model, **kwargs):
        assert received_audio is audio and received_model is model
        assert kwargs == {"sampling_rate": 16000, "return_seconds": True}
        return [{"start": 0, "end": 1}, {"start": 3, "end": 4}]

    monkeypatch.setitem(sys.modules, "silero_vad", types.SimpleNamespace(
        load_silero_vad=lambda **kwargs: model, read_audio=read, get_speech_timestamps=timestamps,
    ))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(get_num_threads=lambda: 12, set_num_threads=lambda _: None))
    from pipeline import silero_runtime

    monkeypatch.setattr(silero_runtime, "read_audio", lambda path, **kwargs: read(str(path), **kwargs))
    assert silence._silero_speech(Path("sample.wav")) == [(0.0, 1.0), (3.0, 4.0)]


@pytest.mark.parametrize("installed", [True, False])
def test_auto_engine_requires_silero_wheel(monkeypatch: pytest.MonkeyPatch, installed: bool) -> None:
    def find(name):
        assert name == "silero_vad"
        return object() if installed else None

    calls = []
    monkeypatch.setattr(silence.importlib.util, "find_spec", find)
    monkeypatch.setattr(silence, "_ensure_wav", lambda source: source)
    monkeypatch.setattr(silence, "_silero_speech", lambda _wav: calls.append("silero") or [(0, 1), (3, 4)])
    monkeypatch.setattr(silence, "_silencedetect_gaps", lambda _source, **_kwargs: calls.append("ffmpeg") or [(1, 3)])
    assert len(silence.detect(Path("sample.wav"), FPS)["cuts"]) == 1
    assert calls == (["silero"] if installed else ["ffmpeg"])


# ── silencedetect parsing + ffmpeg logging (CPU fallback) ─────────────────────
def test_parse_silencedetect_pairs_stderr() -> None:
    stderr = (
        "[silencedetect @ 0x1] silence_start: 1.234\n"
        "[silencedetect @ 0x1] silence_end: 3.210 | silence_duration: 1.976\n"
    )
    assert silence._parse_silencedetect(stderr) == [(1.234, 3.21)]


def test_silencedetect_logs_ffmpeg_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Repo rule: every ffmpeg invocation is logged to run.log BEFORE it runs (silencedetect path)."""
    log = tmp_path / "run.log"
    monkeypatch.setattr(config, "RUN_LOG", log)

    def _fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "", "silence_start: 1.0\nsilence_end: 3.0\n")

    monkeypatch.setattr(silence.subprocess, "run", _fake_run)
    out = silence.detect(Path("nonexistent.mp4"), FPS, engine="silencedetect")
    assert out["cuts"] and out["cuts"][0]["kind"] == "pause"
    logged = log.read_text(encoding="utf-8")
    assert "ffmpeg" in logged and "silencedetect" in logged


# ── real-ffmpeg integration (sine + gap) ─────────────────────────────────────────
@pytest.mark.skipif(not _HAS_FFMPEG, reason="needs ffmpeg on PATH")
def test_silencedetect_gaps_on_synthetic_clip(tmp_path: Path) -> None:
    # 1 s sine, 2 s silence, 1 s sine → one detectable gap in the middle.
    wav = tmp_path / "gap.wav"
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1[a]", "-map", "[a]", str(wav)],
        check=True, capture_output=True, text=True,
    )
    gaps = silence._silencedetect_gaps(wav)
    assert any(g1 - g0 > config.SILENCE_MIN_PAUSE_S for g0, g1 in gaps)
