"""Compile stage (pipeline/compile.py): project.json → one ffmpeg filter_complex → NVENC final.

Isolated, no GPU: every ffmpeg/ffprobe call is stubbed so the tests assert the generated command
STRINGS (the filtergraph, the exact NVENC argv, the libx264 fallback, run.log logging, source-only
resolution, the HDR tonemap chain). The single guarded test that shells ffmpeg is skipped where the
binary is absent. Hardware encoding and source probing are not exercised here.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline.errors import ValidationError
from pipeline.runtime_tools import tool_path

import config
from pipeline import encoder_ladder
from pipeline import compile as compile_mod
from pipeline import layout
from pipeline import project as project_mod

# NVENC final-render argv tail, the encoder portion onward. The one place the command is locked.
_NVENC_TAIL = [
    "-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "19",
    "-spatial-aq", "1", "-temporal-aq", "1",
    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
]


@pytest.fixture(autouse=True)
def _assume_fixture_media_has_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pure graph fixtures use placeholder paths; individual tests override this for silent media."""
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda _path: True)


def _doc(*, proxy: str | None = None) -> dict:
    """A 2-speech + 1-broll + 1-text + 1-music project. The two speech clips share ONE source so the
    deduped-input split path is exercised. validate() passes (fps 30, no in-track overlap, speech has
    no anchor, seams in range)."""
    return {
        "schemaVersion": 1, "name": "zzcompile", "fps": 30, "width": 1920, "height": 1080,
        "revision": 1, "transcript": None,
        "tracks": [
            {"id": "sp", "kind": "speech", "clips": [
                {"id": "c1", "media": {"source": "media/rec.mp4", "proxy": proxy},
                 "srcInF": 0, "srcOutF": 120, "tlStartF": 0, "gainDb": 0.0,
                 "seams": [{"atF": 120, "fadeMs": 30}]},
                {"id": "c2", "media": {"source": "media/rec.mp4", "proxy": None},
                 "srcInF": 200, "srcOutF": 260, "tlStartF": 120, "gainDb": 0.0,
                 "seams": [{"atF": 0, "fadeMs": 30}]},
            ]},
            {"id": "vid", "kind": "video", "clips": [
                {"id": "b1", "media": {"source": "media/broll.mp4", "proxy": None},
                 "srcInF": 0, "srcOutF": 90, "tlStartF": 60, "gainDb": 0.0},
            ]},
            {"id": "txt", "kind": "text", "items": [
                {"id": "t1", "text": "Hook!", "preset": "hook",
                 "pos": {"x": 0.5, "y": 0.18}, "tlStartF": 0, "durF": 45},
            ]},
            {"id": "mus", "kind": "audio", "clips": [
                {"id": "m1", "media": {"source": "media/music.mp3", "proxy": None},
                 "srcInF": 0, "srcOutF": 300, "tlStartF": 0, "gainDb": -6.0, "duckDb": -10.0},
            ]},
        ],
    }


def test_fixture_validates() -> None:
    project_mod.validate(project_mod.from_dict(_doc()))


# ── filtergraph string assertions ────────────────────────────────────────────────
def test_build_plan_filtergraph_bits(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    plan = compile_mod.build_plan(_doc())
    fg = plan.filter_complex

    # Visuals keep their complete source windows and absolute project-clock starts. The later video
    # track overlays speech; it no longer carves/replaces the primary footage in a concat spine.
    assert "trim=start=0.000000:end=4.000000,setpts=PTS-STARTPTS+0.000000/TB" in fg
    assert "trim=start=6.666667:end=8.666667,setpts=PTS-STARTPTS+4.000000/TB" in fg
    assert "[1:v]trim=start=0.000000:end=3.000000,setpts=PTS-STARTPTS+2.000000/TB" in fg
    assert "overlay=x='960.000-w/2':y='540.000-h/2':eof_action=pass:repeatlast=0" in fg
    assert "concat=" not in fg

    # Speech audio keeps the same absolute clock, while audible imported video is a real mix stem.
    assert "atrim=start=0.000000:end=4.000000,asetpts=PTS-STARTPTS" in fg      # c1 full audio
    assert "atrim=start=6.666667:end=8.666667,asetpts=PTS-STARTPTS" in fg      # c2 full audio
    assert "[1:a]atrim=start=0.000000:end=3.000000" in fg                     # imported video audio
    assert "amix=inputs=5:duration=first:normalize=0" in fg

    # drawtext ties to the shared layout golden: exact resolved pixels.
    box = layout.resolve_layout({"preset": "hook", "pos": {"x": 0.5, "y": 0.18}}, 1920, 1080)
    assert box == {"x_px": 960, "y_px": 194, "font_px": 81, "align": "center"}
    assert "drawtext=" in fg
    assert f"fontsize={box['font_px']}" in fg
    assert f"x='({box['x_px']})-text_w/2':y='({box['y_px']})-text_h/2'" in fg

    # seam fades: c1 tail (atF==dur) fades out ending at clip end; c2 head fades in at t=0.
    assert "afade=t=out:st=3.970:d=0.030" in fg
    assert "afade=t=in:st=0:d=0.030" in fg

    # Music: fixed-precision gain plus deterministic overlap-only attenuation, mastered once at -14.
    assert "volume=-6.000dB" in fg
    assert "volume=-10.000dB:enable='between(t,0.000000,6.000000)'" in fg
    assert "sidechaincompress" not in fg
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in fg

    assert plan.maps == ["-map", "[vout]", "-map", "[aout]"]


def test_broll_video_track_layers_above_speech(monkeypatch) -> None:
    """Document track order, not timeline sort, controls z-order."""
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    fg = compile_mod.build_plan(_doc()).filter_complex
    assert fg.index("[vclipc2]") < fg.index("[vclipb1]")
    assert "setpts=PTS-STARTPTS+2.000000/TB" in fg


def test_speech_audio_continuous_across_covered_span(monkeypatch) -> None:
    """Both speech clips' FULL audio survives even though the b-roll covers frames 60..150 of the
    video — the dialogue is never segmented, so nothing under the cover goes silent."""
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    fg = compile_mod.build_plan(_doc()).filter_complex
    assert "atrim=start=0.000000:end=4.000000" in fg   # c1 audio full (not trimmed to [0,60))
    assert "atrim=start=6.666667:end=8.666667" in fg   # c2 audio full
    assert "asetpts=PTS+4.000000/TB" in fg              # second clip keeps timeline placement


def test_seam_fades_run_before_absolute_pts_shift(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    fg = compile_mod.build_plan(_doc()).filter_complex
    c2 = fg[fg.index("atrim=start=6.666667:end=8.666667"):]
    assert c2.index("asetpts=PTS-STARTPTS") < c2.index("afade=t=in:st=0:d=0.030")
    assert c2.index("afade=t=in:st=0:d=0.030") < c2.index("asetpts=PTS+4.000000/TB")


def test_deduped_inputs_and_split(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    plan = compile_mod.build_plan(_doc())
    # rec.mp4 (2 speech), broll.mp4, music.mp3 → 3 deduped inputs; rec.mp4 feeds two clips → split/asplit.
    assert plan.inputs.count("-i") == 3
    assert "[0:v]split=2" in plan.filter_complex
    assert "[0:a]asplit=2" in plan.filter_complex


# ── exact final-render argv ───────────────────────────────────────────────────────
def test_build_encode_cmd_nvenc_argv(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    plan = compile_mod.build_plan(_doc())
    dest = config.FINAL_DIR / "zzcompile.compiled.mp4"
    cmd = compile_mod.build_encode_cmd(plan, dest)
    assert cmd[:2] == [tool_path("ffmpeg"), "-y"]
    assert "-filter_complex" in cmd
    assert cmd[cmd.index("-c:v"):] == _NVENC_TAIL + [str(dest)]


def test_build_encode_cmd_libx264_fallback_argv(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    plan = compile_mod.build_plan(_doc())
    dest = config.FINAL_DIR / "zzcompile.compiled.mp4"
    cmd = compile_mod.build_encode_cmd(plan, dest, encoder="libx264")
    tail = cmd[cmd.index("-c:v"):]
    assert tail[:6] == ["-c:v", "libx264", "-crf", str(config.ENCODE_CRF), "-preset", config.ENCODE_PRESET]
    assert "h264_nvenc" not in cmd
    assert "-cq" not in cmd  # NVENC-only flag absent on the libx264 path


# ── SOURCE only, never the proxy, in a final-render command ───────────────────
def test_build_plan_resolves_source_not_proxy(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    plan = compile_mod.build_plan(_doc(proxy="proxies/rec_540.mp4"))
    joined = " ".join(plan.inputs)
    assert "rec.mp4" in joined
    assert "rec_540.mp4" not in joined   # the proxy basename must never reach a final render


# ── HDR tonemap chain ─────────────────────────────────────────────────────────────
def test_build_plan_hdr_inserts_tonemap(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "arib-std-b67")
    fg = compile_mod.build_plan(_doc()).filter_complex
    assert "tonemap=tonemap=hable" in fg
    assert compile_mod._TONEMAP in fg
    assert compile_mod._TONEMAP + ",scale=1920:1080" in fg  # tonemap ends yuv420p, then scale/pad


def test_build_plan_sdr_no_tonemap(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "bt709")
    assert "tonemap=tonemap=hable" not in compile_mod.build_plan(_doc()).filter_complex


def test_build_plan_mixed_hdr_sdr_per_input_tonemap(monkeypatch) -> None:
    """Realistic project: HDR phone speech (rec.mp4) + SDR stock b-roll (broll.mp4). Only the HDR
    source is tonemapped — a regression that hoists the decision to `any(hdr)` would wrongly
    double-tonemap the SDR b-roll, and this per-input mix is the only case that catches it."""
    monkeypatch.setattr(compile_mod, "_probe_transfer",
                        lambda p: "arib-std-b67" if Path(p).stem == "rec" else "bt709")
    fg = compile_mod.build_plan(_doc()).filter_complex
    # the rec.mp4 speech segments carry the tonemap right before scale …
    assert "setpts=PTS-STARTPTS+0.000000/TB," + compile_mod._TONEMAP + ",scale=1920:1080" in fg
    # … while the SDR b-roll segment goes straight from setpts to scale (no tonemap injected).
    assert "[1:v]trim=start=0.000000:end=3.000000,setpts=PTS-STARTPTS+2.000000/TB,scale=1920:1080" in fg


# ── multi-music amix + first-clip duckDb selection ────────────────────────────────
def _two_music_doc() -> dict:
    """The fixture + a SECOND music bed on its own audio track with a DIFFERENT duckDb, so the
    'first music clip's duckDb wins' rule and the amix path are both observable."""
    doc = _doc()
    doc["tracks"].append({"id": "mus2", "kind": "audio", "clips": [
        {"id": "m2", "media": {"source": "media/music2.mp3", "proxy": None},
         "srcInF": 0, "srcOutF": 300, "tlStartF": 0, "gainDb": -3.0, "duckDb": -4.0}]})
    return doc


def test_build_plan_multi_music_uses_per_clip_deterministic_duck(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    fg = compile_mod.build_plan(_two_music_doc()).filter_complex
    assert "sidechaincompress" not in fg
    assert "volume=-10.000dB:enable='between(t,0.000000,6.000000)'" in fg
    assert "volume=-4.000dB:enable='between(t,0.000000,6.000000)'" in fg
    assert "volume=-3.000dB" in fg
    assert "amix=inputs=6:duration=first:normalize=0" in fg


# ── no-music / no-text branches: assert the exact filtergraph tail (not just a smoke render) ──
def _no_music_doc() -> dict:
    doc = _doc()
    doc["tracks"] = [t for t in doc["tracks"] if t["kind"] != "audio"]
    return doc


def _no_text_doc() -> dict:
    doc = _doc()
    doc["tracks"] = [t for t in doc["tracks"] if t["kind"] != "text"]
    return doc


def _video_only_doc() -> dict:
    """A valid project may have a placed video and title but no source dialogue or music."""
    doc = _doc()
    doc["tracks"] = [track for track in doc["tracks"] if track["kind"] in {"video", "text"}]
    return doc


def test_build_plan_no_music_audio_branch(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    fg = compile_mod.build_plan(_no_music_doc()).filter_complex
    assert "amix=inputs=4:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11" in fg
    assert "sidechaincompress" not in fg


def test_build_plan_no_text_video_branch(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    fg = compile_mod.build_plan(_no_text_doc()).filter_complex
    assert "null[vout]" in fg
    assert "drawtext=" not in fg


def test_build_plan_video_only_emits_silent_audio(monkeypatch) -> None:
    """Video-only title projects still produce a playable MP4 rather than failing for no audio."""
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda p: False)
    fg = compile_mod.build_plan(_video_only_doc()).filter_complex
    assert "anullsrc=channel_layout=stereo:sample_rate=48000:d=5.000000" in fg
    assert "[asilence]aresample=48000[aout]" in fg
    assert "amix=" not in fg
    assert "drawtext=" in fg


# ── validate() rejects injection / out-of-range doc values BEFORE the graph is built ──
def test_validate_rejects_nonnumeric_gaindb() -> None:
    doc = _doc()
    doc["tracks"][3]["clips"][0]["gainDb"] = "3dB,amovie=/etc/passwd"   # a filtergraph-injection payload
    with pytest.raises(ValidationError):
        project_mod.validate(project_mod.from_dict(doc))


def test_validate_rejects_out_of_range_duckdb() -> None:
    doc = _doc()
    doc["tracks"][3]["clips"][0]["duckDb"] = -999.0
    with pytest.raises(ValidationError):
        project_mod.validate(project_mod.from_dict(doc))


def test_validate_rejects_bool_gaindb() -> None:
    doc = _doc()
    doc["tracks"][3]["clips"][0]["gainDb"] = True                       # bool must not coerce to 1 dB
    with pytest.raises(ValidationError):
        project_mod.validate(project_mod.from_dict(doc))


def test_validate_rejects_bad_id_charset() -> None:
    doc = _doc()
    doc["tracks"][2]["items"][0]["id"] = "a,drawtext=text=PWNED"
    with pytest.raises(ValidationError):
        project_mod.validate(project_mod.from_dict(doc))


def test_build_plan_rejects_injection_doc(monkeypatch) -> None:
    """The injection can't even reach the filtergraph: build_plan validates first."""
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    doc = _doc()
    doc["tracks"][3]["clips"][0]["gainDb"] = "3dB,amovie=/etc/passwd"
    with pytest.raises(ValidationError):
        compile_mod.build_plan(doc)


def test_build_plan_textfile_named_by_enumerated_index(monkeypatch) -> None:
    """The overlay textfile is named by an enumerated index, never by the doc-controlled item.id."""
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    doc = _doc()
    doc["tracks"][2]["items"][0]["id"] = "distinctiveid"
    fg = compile_mod.build_plan(doc).filter_complex
    assert "_text0.txt" in fg
    assert "distinctiveid" not in fg


# ── run.log logging + NVENC→libx264 fallback runner ───────────────────────────────
def test_compile_project_logs_and_encodes(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    log = tmp_path / "run.log"
    monkeypatch.setattr(config, "RUN_LOG", log)

    calls: list[list[str]] = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(compile_mod.subprocess, "run", _fake_run)

    dest = (tmp_path / "final") / "zzcompile.compiled.mp4"
    r = compile_mod.compile_project(_doc(), dest)
    assert r.status == "ok"
    # The loudness measurement runs first (its empty stderr is unusable, so the encode keeps the
    # single-pass stage); the single encode follows. Both commands reach run.log.
    assert len(calls) == 2
    assert "-c:v" not in calls[0]
    assert calls[1][calls[1].index("-c:v") + 1] == "h264_nvenc"
    logged = log.read_text(encoding="utf-8")
    assert " ".join(calls[0]) + "\n" in logged
    assert " ".join(calls[1]) + "\n" in logged


def test_compile_project_falls_back_to_libx264(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")

    calls: list[list[str]] = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))
        if "h264_nvenc" in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="No capable devices found")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(compile_mod.subprocess, "run", _fake_run)

    dest = (tmp_path / "final") / "zzcompile.compiled.mp4"
    r = compile_mod.compile_project(_doc(), dest)
    assert r.status == "ok"
    assert len(calls) == 3  # measurement, NVENC attempt, libx264 retry
    assert calls[2][calls[2].index("-c:v") + 1] == "libx264"


def test_compile_project_non_init_error_surfaces(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")

    calls: list[list[str]] = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="moov atom not found")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(compile_mod.subprocess, "run", _fake_run)

    dest = (tmp_path / "final") / "zzcompile.compiled.mp4"
    r = compile_mod.compile_project(_doc(), dest)
    assert r.status == "error"
    # measurement + one encode: a genuine encode error must NOT trigger a needless libx264 retry
    assert len(calls) == 2
    assert [c for c in calls if "-c:v" in c] == [calls[1]]


def test_compile_project_rejects_out_of_tree_dest(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    r = compile_mod.compile_project(_doc(), Path("C:/tmp/escape.mp4"))
    assert r.status == "error"
    assert "out-of-tree" in (r.reason or "")


# ── guarded real-ffmpeg smoke (falls back to libx264 where NVENC is absent) ────────
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_compile_project_real_smoke(tmp_path, monkeypatch) -> None:
    media = tmp_path / "media"
    media.mkdir()
    src = media / "rec.mp4"
    subprocess.run(
        # a real sine tone (NOT anullsrc): loudnorm on pure digital silence emits NaN and aborts aac.
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
        check=True, capture_output=True, text=True,
    )
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    doc = {
        "schemaVersion": 1, "name": "zzsmoke", "fps": 30, "width": 320, "height": 240,
        "revision": 1, "transcript": None,
        "tracks": [{"id": "sp", "kind": "speech", "clips": [
            {"id": "c1", "media": {"source": "media/rec.mp4", "proxy": None},
             "srcInF": 0, "srcOutF": 30, "tlStartF": 0, "gainDb": 0.0}]}],
    }
    dest = (tmp_path / "final") / "zzsmoke.compiled.mp4"
    r = compile_mod.compile_project(doc, dest, project_dir=tmp_path)
    assert r.status == "ok", r.details
    assert dest.exists() and dest.stat().st_size > 0
