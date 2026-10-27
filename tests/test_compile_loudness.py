"""Export loudness (pipeline/compile.py): measure the mix, then normalise it with one constant gain.

Pure tests pin both loudnorm stages, the measurement parser and every fallback to the single-pass
dynamic stage, with ffmpeg stubbed. The guarded real-ffmpeg tests export a quiet-then-loud signal and
check what actually lands in the file, which is what single-pass loudnorm got wrong.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import config
from pipeline import encoder_ladder
from pipeline import compile as compile_mod

_REAL_PROBE_HAS_AUDIO = compile_mod._probe_has_audio  # the real probe, before the autouse stub replaces it

# ffmpeg 8.1 stderr for a loudnorm analysis run to a null sink: banner-less log lines, CRLF endings
# on Windows, and the JSON block between other output.
_MEASURE_STDERR = "\r\n".join([
    "[aist#0:0/pcm_f32le @ 000002186a31ad00] Guessed Channel Layout: stereo",
    "Input #0, wav, from 'src.wav':",
    "  Metadata:",
    "    encoder         : Lavf62.12.101",
    "  Duration: 00:00:20.00, bitrate: 3072 kb/s",
    "  Stream #0:0: Audio: pcm_f32le ([3][0][0][0] / 0x0003), 48000 Hz, stereo, flt, 3072 kb/s",
    "Stream mapping:",
    "  Stream #0:0 (pcm_f32le) -> atrim:default",
    "  loudnorm:default -> Stream #0:0 (pcm_s16le)",
    "Press [q] to stop, [?] for help",
    "Output #0, null, to 'pipe:':",
    "  Stream #0:0: Audio: pcm_s16le, 192000 Hz, stereo, s16, 6144 kb/s",
    "[Parsed_loudnorm_3 @ 000002186a315e00] ",
    "{",
    '\t"input_i" : "-18.31",',
    '\t"input_tp" : "-4.22",',
    '\t"input_lra" : "9.80",',
    '\t"input_thresh" : "-28.74",',
    '\t"output_i" : "-13.98",',
    '\t"output_tp" : "-1.50",',
    '\t"output_lra" : "6.00",',
    '\t"output_thresh" : "-24.03",',
    '\t"normalization_type" : "dynamic",',
    '\t"target_offset" : "-0.02"',
    "}",
    "[out#0/null @ 000002186a316140] video:0KiB audio:1500KiB subtitle:0KiB other streams:0KiB",
    "size=N/A time=00:00:02.00 bitrate=N/A speed=39.8x elapsed=0:00:00.05    ",
    "",
])
_MEASURED = compile_mod.Loudness(integrated=-18.31, true_peak=-4.22, lra=9.8, threshold=-28.74, offset=-0.02)
_DYNAMIC_STAGE = "loudnorm=I=-14:TP=-1.5:LRA=11"
_LINEAR_STAGE = ("loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-18.31:measured_LRA=9.80:measured_TP=-4.22"
                 ":measured_thresh=-28.74:offset=-0.02:linear=true:print_format=summary")


@pytest.fixture(autouse=True)
def _stubbed_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda _path: "")
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda _path: True)


def _doc(*, with_audio: bool = True) -> dict:
    """B-roll, speech and a music bed: three audible stems, so the mix has four inputs with the silence bed."""
    tracks: list[dict] = [{"id": "vid", "kind": "video", "clips": [
        {"id": "v1", "media": {"source": "media/clip.mp4"}, "srcInF": 0, "srcOutF": 90, "tlStartF": 0}]}]
    if with_audio:
        tracks.append({"id": "sp", "kind": "speech", "clips": [
            {"id": "s1", "media": {"source": "media/rec.mp4"}, "srcInF": 0, "srcOutF": 90, "tlStartF": 0}]})
        tracks.append({"id": "mus", "kind": "audio", "clips": [
            {"id": "m1", "media": {"source": "media/music.mp3"}, "srcInF": 0, "srcOutF": 90, "tlStartF": 0,
             "gainDb": -6.0}]})
    return {"schemaVersion": 1, "name": "zzloud", "fps": 30, "width": 1280, "height": 720,
            "revision": 1, "tracks": tracks}


def _silent_plan(monkeypatch: pytest.MonkeyPatch) -> compile_mod.CompilePlan:
    """A project whose only media is silent video: nothing to mix, so nothing to measure."""
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda _path: False)
    return compile_mod.build_plan(_doc(with_audio=False))


# ── the two loudnorm stages ───────────────────────────────────────────────────────
def test_single_pass_stage_is_the_fallback() -> None:
    assert compile_mod.loudnorm_filter() == _DYNAMIC_STAGE
    assert compile_mod.loudnorm_filter(None) == _DYNAMIC_STAGE


def test_measure_stage_prints_json() -> None:
    assert compile_mod.loudnorm_measure_filter() == f"{_DYNAMIC_STAGE}:print_format=json"


def test_apply_stage_is_linear_with_the_measured_values() -> None:
    assert compile_mod.loudnorm_filter(_MEASURED) == _LINEAR_STAGE


def test_targets_come_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LOUDNORM_I", -16.0)
    monkeypatch.setattr(config, "LOUDNORM_TP", -2.0)
    monkeypatch.setattr(config, "LOUDNORM_LRA", 7.0)
    assert compile_mod.loudnorm_filter() == "loudnorm=I=-16:TP=-2:LRA=7"
    assert compile_mod.loudnorm_measure_filter() == "loudnorm=I=-16:TP=-2:LRA=7:print_format=json"
    assert compile_mod.loudnorm_filter(_MEASURED).startswith("loudnorm=I=-16:TP=-2:LRA=9.8:measured_I=")


def test_wide_dynamics_raise_the_lra_target_so_ffmpeg_stays_linear() -> None:
    """ffmpeg reverts to dynamic when the measured LRA exceeds the LRA target, so a mix with more
    range than the target raises it; in linear mode the target does nothing else."""
    stage = compile_mod.loudnorm_filter(replace(_MEASURED, lra=12.34))
    assert stage.startswith("loudnorm=I=-14:TP=-1.5:LRA=12.34:")
    assert ":measured_LRA=12.34:" in stage
    assert compile_mod.loudnorm_filter(replace(_MEASURED, lra=80.0)).startswith("loudnorm=I=-14:TP=-1.5:LRA=50:")


# ── measurement parsing ───────────────────────────────────────────────────────────
def test_parse_realistic_ffmpeg_stderr() -> None:
    assert compile_mod.parse_loudness(_MEASURE_STDERR) == _MEASURED


def test_parse_uses_the_last_json_block() -> None:
    first = _MEASURE_STDERR.replace("-18.31", "-30.00")
    assert compile_mod.parse_loudness(first + _MEASURE_STDERR) == _MEASURED


def test_parse_accepts_compact_json_without_log_lines() -> None:
    raw = ('{"input_i":"-23.50","input_tp":"-1.00","input_lra":"3.10","input_thresh":"-33.60",'
           '"target_offset":"0.40"}')
    assert compile_mod.parse_loudness(raw) == compile_mod.Loudness(-23.5, -1.0, 3.1, -33.6, 0.4)


@pytest.mark.parametrize("stderr", [
    "",
    "Conversion failed!",
    '{"input_i" : "-20.00", "input_tp" : }',
    '{"input_i" : "-20.00"}',
    '{"input_i":"abc","input_tp":"-1.0","input_lra":"3.0","input_thresh":"-30.0","target_offset":"0.0"}',
    '{"input_i":"nan","input_tp":"-1.0","input_lra":"3.0","input_thresh":"-30.0","target_offset":"0.0"}',
    # digital silence: every level is -inf, and loudnorm would refuse -inf as a measured value
    '{"input_i":"-inf","input_tp":"-inf","input_lra":"0.00","input_thresh":"-inf","target_offset":"inf"}',
    # outside what loudnorm accepts for measured_I (at most 0)
    '{"input_i":"1.50","input_tp":"-1.0","input_lra":"3.0","input_thresh":"-30.0","target_offset":"0.0"}',
])
def test_parse_rejects_what_loudnorm_would_not_accept_back(stderr: str) -> None:
    assert compile_mod.parse_loudness(stderr) is None


# ── plan and commands stay pure ───────────────────────────────────────────────────
def test_plan_without_a_measurement_is_todays_single_pass() -> None:
    plan = compile_mod.build_plan(_doc())
    assert plan.loudness is None
    assert f"amix=inputs=4:duration=first:normalize=0,{_DYNAMIC_STAGE},aresample=48000[aout]" in plan.filter_complex
    assert "linear=true" not in plan.filter_complex


def test_measurement_turns_the_closing_stage_linear_and_nothing_else() -> None:
    plan = compile_mod.build_plan(_doc())
    measured = replace(plan, loudness=_MEASURED)
    assert f"normalize=0,{_LINEAR_STAGE},aresample=48000[aout]" in measured.filter_complex
    assert measured.filter_complex.replace(_LINEAR_STAGE, _DYNAMIC_STAGE) == plan.filter_complex
    assert plan.loudness is None  # building the second plan left the first untouched


def test_measure_cmd_renders_audio_only() -> None:
    plan = compile_mod.build_plan(_doc())
    cmd = compile_mod.build_measure_cmd(plan)
    graph = cmd[cmd.index("-filter_complex") + 1]

    assert cmd[-5:] == ["-map", "[amix]", "-f", "null", "-"]
    assert cmd[cmd.index("-i"):cmd.index("-filter_complex")] == plan.inputs
    assert graph.endswith(f"normalize=0,{_DYNAMIC_STAGE}:print_format=json[amix]")
    assert "atrim=" in graph and "amix=inputs=4" in graph
    for video_only in ("[vout]", "overlay", "color=", "[aout]", "linear=true"):
        assert video_only not in graph


def test_no_stems_means_no_mix_and_no_loudnorm(monkeypatch) -> None:
    plan = _silent_plan(monkeypatch)
    assert plan.audio_mix == ""
    assert "loudnorm" not in plan.filter_complex
    assert "[asilence]aresample=48000[aout]" in plan.filter_complex


# ── measuring: every failure keeps the single-pass stage and says why ─────────────
@pytest.fixture
def run_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    log = tmp_path / "run.log"
    monkeypatch.setattr(config, "RUN_LOG", log)
    return log


def _fake_ffmpeg(monkeypatch: pytest.MonkeyPatch, *, measure, encode=None) -> list[list[str]]:
    """Stub subprocess.run: `measure(cmd)` answers the analysis command, `encode(cmd)` the encode."""
    calls: list[list[str]] = []

    def run(cmd, **_kw):
        calls.append(list(cmd))
        answer = encode if "-c:v" in cmd and encode is not None else measure
        return answer(cmd)

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(compile_mod.subprocess, "run", run)
    return calls


def _ok(stderr: str):
    return lambda cmd: subprocess.CompletedProcess(cmd, 0, "", stderr)


def _fail(stderr: str):
    def raise_failure(cmd):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr=stderr)
    return raise_failure


def test_measure_returns_the_parsed_values_and_logs_the_command(monkeypatch, run_log) -> None:
    calls = _fake_ffmpeg(monkeypatch, measure=_ok(_MEASURE_STDERR))
    plan = compile_mod.build_plan(_doc())

    assert compile_mod.measure_loudness(plan, cwd=run_log.parent) == _MEASURED
    assert calls == [compile_mod.build_measure_cmd(plan)]
    assert run_log.read_text(encoding="utf-8") == " ".join(calls[0]) + "\n"


def test_measure_skips_ffmpeg_when_there_is_no_audio_to_mix(monkeypatch, run_log) -> None:
    calls = _fake_ffmpeg(monkeypatch, measure=_ok(_MEASURE_STDERR))

    assert compile_mod.measure_loudness(_silent_plan(monkeypatch), cwd=run_log.parent) is None
    assert calls == []
    assert not run_log.exists()


@pytest.mark.parametrize(("failure", "reason"), [
    (_fail("Error initializing filters\r\nConversion failed!\r\n"), "Conversion failed!"),
    (_ok("size=N/A time=00:00:02.00"), "no usable measurement"),
    (_ok('{"input_i":"-inf","input_tp":"-inf","input_lra":"0.00","input_thresh":"-inf","target_offset":"inf"}'),
     "no usable measurement"),
])
def test_failed_measurement_returns_none_and_logs_why(monkeypatch, run_log, failure, reason) -> None:
    _fake_ffmpeg(monkeypatch, measure=failure)

    assert compile_mod.measure_loudness(compile_mod.build_plan(_doc()), cwd=run_log.parent) is None
    note = run_log.read_text(encoding="utf-8").splitlines()[-1]
    assert note.startswith("# loudness measurement failed (")
    assert reason in note
    assert note.endswith("falling back to single-pass dynamic loudnorm")


def test_missing_ffmpeg_is_a_failed_measurement_not_a_crash(monkeypatch, run_log) -> None:
    def missing(_cmd, **_kw):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(compile_mod.subprocess, "run", missing)
    assert compile_mod.measure_loudness(compile_mod.build_plan(_doc()), cwd=run_log.parent) is None
    assert "# loudness measurement failed (ffmpeg)" in run_log.read_text(encoding="utf-8")


# ── compile_project runs measure, then apply ──────────────────────────────────────
def _compile(monkeypatch, tmp_path, run_log, *, measure, encode=None):
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    calls = _fake_ffmpeg(monkeypatch, measure=measure, encode=encode or _ok(""))
    result = compile_mod.compile_project(_doc(), (tmp_path / "final") / "zzloud.compiled.mp4")
    assert result.status == "ok"
    return calls


def _graph(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def test_export_measures_then_applies_linear_gain(monkeypatch, tmp_path, run_log) -> None:
    calls = _compile(monkeypatch, tmp_path, run_log, measure=_ok(_MEASURE_STDERR))

    measure, encode = calls
    assert "-c:v" not in measure and "print_format=json" in _graph(measure)
    assert _LINEAR_STAGE in _graph(encode)
    assert "print_format=json" not in _graph(encode)
    logged = run_log.read_text(encoding="utf-8")
    assert " ".join(measure) + "\n" in logged and " ".join(encode) + "\n" in logged
    assert logged.index(" ".join(measure)) < logged.index(" ".join(encode))


def test_export_falls_back_to_single_pass_when_measuring_fails(monkeypatch, tmp_path, run_log) -> None:
    calls = _compile(monkeypatch, tmp_path, run_log, measure=_fail("Conversion failed!"))

    encode = calls[-1]
    assert f"normalize=0,{_DYNAMIC_STAGE},aresample=48000[aout]" in _graph(encode)
    assert "linear=true" not in _graph(encode)
    assert "# loudness measurement failed (Conversion failed!)" in run_log.read_text(encoding="utf-8")


def _summary(kind: str) -> str:
    return ("Input Integrated:    -18.3 LUFS\r\nInput True Peak:      -0.9 dBTP\r\n\r\n"
            f"Output Integrated:   -14.0 LUFS\r\n\r\nNormalization Type:   {kind}\r\nTarget Offset:        -0.1 LU\r\n")


def test_export_logs_that_the_second_pass_ran_linear(monkeypatch, tmp_path, run_log) -> None:
    _compile(monkeypatch, tmp_path, run_log, measure=_ok(_MEASURE_STDERR), encode=_ok(_summary("Linear")))
    assert "# loudnorm second pass ran linear" in run_log.read_text(encoding="utf-8")


def test_export_logs_when_ffmpeg_reverts_to_dynamic(monkeypatch, tmp_path, run_log) -> None:
    """One constant gain that would break the true-peak target makes ffmpeg fall back by itself."""
    _compile(monkeypatch, tmp_path, run_log, measure=_ok(_MEASURE_STDERR), encode=_ok(_summary("Dynamic")))
    note = [line for line in run_log.read_text(encoding="utf-8").splitlines() if line.startswith("# loudnorm")]
    assert note == ["# loudnorm second pass ran dynamic: ffmpeg reverted because one constant gain "
                    "cannot meet the true-peak target"]


def test_single_pass_export_logs_no_normalization_note(monkeypatch, tmp_path, run_log) -> None:
    _compile(monkeypatch, tmp_path, run_log, measure=_fail("Conversion failed!"))
    assert "# loudnorm second pass" not in run_log.read_text(encoding="utf-8")


# ── real ffmpeg: what lands in the exported file ──────────────────────────────────
_NEEDS_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
_QUIET_DB, _LOUD_DB = -30.0, -18.0   # LUFS of the two sections of the dynamic test signal


def _ebur128(path: Path, *, start: float = 0.0, end: float | None = None) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a stretch of a file, from ffmpeg's EBU R128 meter."""
    window = f"atrim=start={start}" + (f":end={end}" if end is not None else "")
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", f"{window},ebur128=peak=true",
         "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    ).stderr
    summary = out[out.rindex("Summary:"):]
    return _reading(r"I:[ \t]+(-?[\d.]+) LUFS", summary), _reading(r"Peak:[ \t]+(-?[\d.]+) dBFS", summary)


def _reading(pattern: str, text: str) -> float:
    found = re.search(pattern, text)
    assert found, text
    return float(found.group(1))


def _make_source(path: Path, expression: str, seconds: int) -> None:
    """A 320x240 test video whose mono audio is `expression`, an ffmpeg aevalsrc expression of t."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"aevalsrc=exprs='{expression}':s=48000:d={seconds}",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
         str(path)],
        check=True, capture_output=True, text=True,
    )


def _sine_amplitude(lufs: float) -> float:
    """Peak amplitude of a mono 997 Hz sine that reads as `lufs`: a sine's mean square is half its peak
    squared, and K-weighting is +0.69 dB there against the meter's -0.691 offset."""
    return 10 ** ((lufs + 3.01) / 20)


def _export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, seconds: int) -> Path:
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    doc = {"schemaVersion": 1, "name": "zzdynamics", "fps": 30, "width": 320, "height": 240, "revision": 1,
           "tracks": [{"id": "sp", "kind": "speech", "clips": [
               {"id": "c1", "media": {"source": "media/rec.mp4"}, "srcInF": 0, "srcOutF": 30 * seconds,
                "tlStartF": 0}]}]}
    dest = (tmp_path / "final") / "zzdynamics.compiled.mp4"
    result = compile_mod.compile_project(doc, dest, project_dir=tmp_path)
    assert result.status == "ok", result.details
    return dest


@_NEEDS_FFMPEG
def test_export_keeps_the_level_step_between_quiet_and_loud_sections(tmp_path, monkeypatch) -> None:
    (tmp_path / "media").mkdir()
    source = tmp_path / "media" / "rec.mp4"
    _make_source(source, f"(lt(t,10)*{_sine_amplitude(_QUIET_DB):.5f}+gte(t,10)*{_sine_amplitude(_LOUD_DB):.5f})"
                         "*sin(2*PI*997*t)", seconds=20)
    dest = _export(tmp_path, monkeypatch, seconds=20)

    quiet_in, _ = _ebur128(source, start=1, end=9)
    loud_in, _ = _ebur128(source, start=11, end=19)
    assert (quiet_in, loud_in) == (pytest.approx(_QUIET_DB, abs=1.5), pytest.approx(_LOUD_DB, abs=1.5))

    quiet_out, _ = _ebur128(dest, start=1, end=9)
    loud_out, _ = _ebur128(dest, start=11, end=19)
    assert (loud_out - quiet_out) == pytest.approx(loud_in - quiet_in, abs=1.0)

    integrated, true_peak = _ebur128(dest)
    assert integrated == pytest.approx(config.LOUDNORM_I, abs=0.5)
    assert true_peak <= config.LOUDNORM_TP

    notes = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "# loudnorm second pass ran linear" in notes


@_NEEDS_FFMPEG
def test_export_logs_the_dynamic_reversion_when_peaks_rule_out_a_constant_gain(tmp_path, monkeypatch) -> None:
    """A quiet mix with a full-scale click: the gain that reaches -14 LUFS would put the click over the
    true-peak ceiling, so ffmpeg itself reverts to dynamic and the run log must say so."""
    (tmp_path / "media").mkdir()
    source = tmp_path / "media" / "rec.mp4"
    _make_source(source, "(0.02+0.99*lt(t,0.02))*sin(2*PI*997*t)", seconds=6)
    _export(tmp_path, monkeypatch, seconds=6)

    notes = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "# loudnorm second pass ran dynamic" in notes


@_NEEDS_FFMPEG
def test_measurement_leaves_looped_stills_and_silent_video_alone(tmp_path, monkeypatch) -> None:
    """The analysis graph is audio only, yet it receives every export input: a looped still and a
    video with no audio track must neither stall it nor break its stem labels."""
    monkeypatch.setattr(compile_mod, "_probe_has_audio", _REAL_PROBE_HAS_AUDIO)
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    media = tmp_path / "media"
    media.mkdir()
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=80x80:d=0.1", "-frames:v", "1",
                    str(media / "card.png")], check=True, capture_output=True, text=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media / "silent.mp4")],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=3",
                    str(media / "bed.wav")], check=True, capture_output=True, text=True)
    doc = {"schemaVersion": 2, "name": "zzstills", "fps": 30, "width": 320, "height": 240, "revision": 1,
           "tracks": [
               {"id": "film", "kind": "video", "clips": [
                   {"id": "f1", "media": {"source": "media/silent.mp4"}, "srcInF": 0, "srcOutF": 90, "tlStartF": 0}]},
               {"id": "still", "kind": "video", "clips": [
                   {"id": "card", "media": {"source": "media/card.png"}, "srcInF": 0, "srcOutF": 30, "tlStartF": 15}]},
               {"id": "a", "kind": "audio", "clips": [
                   {"id": "bed", "media": {"source": "media/bed.wav"}, "srcInF": 0, "srcOutF": 90, "tlStartF": 0}]},
           ]}

    measured = compile_mod.measure_loudness(compile_mod.build_plan(doc, project_dir=tmp_path), cwd=tmp_path)

    assert measured is not None
    assert -60 < measured.integrated < 0
