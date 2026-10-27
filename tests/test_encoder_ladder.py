"""Shared H.264 behavior, with build listings independent of the installed GPU."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

import pytest

import config
from pipeline import encoder_ladder as ladder
from pipeline import compile as compiler, proxy, script_video

FIXTURES = Path(__file__).with_name("fixtures")
PARITY = json.loads((FIXTURES / "encoder_ladder_parity.json").read_text(encoding="utf-8"))


def test_shared_profiles_and_failure_rules_match_parity() -> None:
    assert list(ladder.ORDER) == PARITY["order"]
    assert ladder.DEFINITION["orders"] == PARITY["orders"]
    assert ladder.UNAVAILABLE_MESSAGE == PARITY["unavailableMessage"]
    assert ladder.PROBE_TIMEOUT_MESSAGE == PARITY["probeTimeoutMessage"]
    assert ladder.PROBE_FAILURE_MESSAGE == PARITY["probeFailureMessage"]
    assert ladder.DEFINITION["initFailures"] == PARITY["initFailures"]
    assert set(ladder.DEFINITION["profiles"]) == set(PARITY["profiles"])
    for role, encoders in PARITY["profiles"].items():
        for encoder, expected in encoders.items():
            assert ladder.encoder_args(role, encoder) == expected


@pytest.mark.parametrize("case", PARITY["selections"], ids=lambda c: c["file"])
def test_recorded_build_selection(case: dict) -> None:
    listing = (FIXTURES / "encoders" / case["file"]).read_text(encoding="utf-8")
    for role, expected in case["expected"].items():
        assert ladder.select_encoders(ladder.parse_encoders(listing), role) == expected


@pytest.mark.parametrize("case", PARITY["restrictions"])
def test_restriction_never_adds_or_reorders_encoders(case: dict) -> None:
    assert ladder.select_encoders(case["available"], case["role"], case["value"]) == case["expected"]


@pytest.mark.parametrize("value", [",", "h264_nvenc,typo", "libopenh264"])
def test_invalid_restriction_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match=ladder.TEST_ENCODERS_ENV):
        ladder.select_encoders(ladder.ORDER, "export", value)


@pytest.mark.parametrize("encoder", ladder.ORDER)
@pytest.mark.parametrize("stderr", PARITY["genuineErrors"])
def test_banner_and_real_errors_do_not_allow_fallback(encoder: str, stderr: str) -> None:
    assert not ladder.is_init_failure(encoder, stderr)


@pytest.fixture
def isolated_ladder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(ladder, "_PROBES", {})
    monkeypatch.setattr(ladder, "_FAILED_INITS", {})
    monkeypatch.delenv(ladder.TEST_ENCODERS_ENV, raising=False)
    log = tmp_path / "run.log"
    monkeypatch.setattr(config, "RUN_LOG", log)
    return log


def test_probes_once_per_path_even_with_concurrent_callers(monkeypatch, isolated_ladder) -> None:
    calls = []
    listing = (FIXTURES / "encoders/lgpl.txt").read_text(encoding="utf-8")

    def run(cmd, **kw):
        calls.append(cmd)
        assert kw["timeout"] == 30
        return subprocess.CompletedProcess(cmd, 0, listing, "")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(ladder.probe_encoders, ["first"] * 8))
    assert all("h264_mf" in available for available in results)
    ladder.probe_encoders("second")
    assert [cmd[0] for cmd in calls] == ["first", "second"]
    assert isolated_ladder.read_text().splitlines() == ["first -hide_banner -encoders", "second -hide_banner -encoders"]


FAILURES = [(encoder, phrase) for encoder, phrases in PARITY["initFailures"].items() for phrase in phrases]
FAILURES += [(encoder, f"Unknown encoder '{encoder}'") for encoder in ladder.ORDER]


@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(1, ["ffmpeg"], stderr="tool could not start"),
    OSError("tool could not start"),
    subprocess.TimeoutExpired(["ffmpeg"], 30),
])
def test_failed_discovery_is_retried(monkeypatch, isolated_ladder, error) -> None:
    calls = []
    listing = (FIXTURES / "encoders/lgpl.txt").read_text(encoding="utf-8")

    def run(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 1:
            raise error
        return subprocess.CompletedProcess(cmd, 0, listing, "")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    message = ladder.PROBE_TIMEOUT_MESSAGE if isinstance(error, subprocess.TimeoutExpired) else ladder.PROBE_FAILURE_MESSAGE
    with pytest.raises(ladder.EncoderDiscoveryError) as caught:
        ladder.run_h264("ffmpeg", "export", lambda _: pytest.fail("discovery failed"))
    assert str(caught.value) == message
    assert caught.value.__cause__ is error
    assert "h264_mf" in ladder.probe_encoders("ffmpeg")
    ladder.probe_encoders("ffmpeg")
    assert calls == [["ffmpeg", "-hide_banner", "-encoders"]] * 2


@pytest.mark.parametrize("encoder,phrase", FAILURES)
def test_each_init_failure_advances_and_logs(monkeypatch, isolated_ladder, encoder, phrase) -> None:
    order = PARITY["orders"]["withoutLibx264"] if encoder != "libx264" else ["libx264"]
    index = order.index(encoder)
    following = order[index + 1:index + 2]
    available = {encoder, *following}
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: available)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[-1] == encoder:
            raise subprocess.CalledProcessError(1, cmd, stderr=phrase.upper())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    if following:
        _, selected = ladder.run_h264("ffmpeg", "export", lambda name: ["ffmpeg", name])
        assert selected == following[0]
    else:
        with pytest.raises(ladder.NoH264Encoder, match="Media Feature Pack"):
            ladder.run_h264("ffmpeg", "export", lambda name: ["ffmpeg", name])
    assert [cmd[-1] for cmd in calls] == [encoder, *following]
    assert isolated_ladder.read_text().splitlines() == [" ".join(cmd) for cmd in calls]


@pytest.mark.parametrize("available", [PARITY["order"], PARITY["orders"]["withoutLibx264"]])
def test_entire_ladder_exhaustion_is_actionable(monkeypatch, isolated_ladder, available) -> None:
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: available)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[-1])
        raise subprocess.CalledProcessError(1, cmd, stderr=f"Unknown encoder '{cmd[-1]}'")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    with pytest.raises(ladder.NoH264Encoder, match="Windows edition"):
        ladder.run_h264("ffmpeg", "export", lambda name: ["ffmpeg", name])
    assert calls == ladder.select_encoders(available, "export")


def test_empty_probe_never_attempts_an_encode(monkeypatch, isolated_ladder) -> None:
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: frozenset())
    with pytest.raises(ladder.NoH264Encoder, match="Media Feature Pack"):
        ladder.run_h264("ffmpeg", "export", lambda _: pytest.fail("no encoder may be tried"))


@pytest.mark.parametrize("stderr", PARITY["genuineErrors"])
def test_real_encode_failure_is_preserved(monkeypatch, isolated_ladder, stderr) -> None:
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: ladder.ORDER)
    calls = []
    error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr=stderr)

    def run(cmd, **kw):
        calls.append(cmd)
        raise error

    monkeypatch.setattr(ladder.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        ladder.run_h264("ffmpeg", "export", lambda name: ["ffmpeg", name])
    assert caught.value is error
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["export", "proxy", "script", "take", "conform"])
@pytest.mark.parametrize("encoder", ladder.ORDER)
def test_pipeline_paths_use_selected_profile(monkeypatch, isolated_ladder, tmp_path, role, encoder) -> None:
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: {encoder})
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "-show_entries" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "1.0", "")
        Path(cmd[-1]).write_bytes(b"encoded")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    dest = tmp_path / "output.mp4"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    if role == "export":
        plan = compiler.CompilePlan(inputs=[], video_graph="", audio_graph="")
        result = compiler._run_encode(plan, dest, "video", project_dir=tmp_path)
        assert result.status == "ok" and result.metrics["encoder"] == encoder
    elif role == "proxy":
        result = proxy._encode_proxy(source, dest, "format=yuv420p", clip_id="video")
        assert result.status == "ok" and result.metrics["encoder"] == encoder
    elif role == "conform":
        result = proxy.conform_vfr(source, dest=dest)
        assert result.status == "ok" and result.metrics["encoder"] == encoder
    elif role == "script":
        script_video.make_silent_source(dest, 30, 30, 320, 180)
    else:
        script_video.make_take_source(source, dest, 30, 320, 180)
    cmd = calls[-1]
    expected_role = {"take": "script"}.get(role, role)
    expected = PARITY["profiles"][expected_role][encoder]
    start = cmd.index("-c:v")
    assert cmd[start:start + len(expected)] == expected
    if role != "proxy" and (role != "conform" or encoder != "libx264"):
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    if encoder != "libx264":
        assert cmd[cmd.index("-ar") + 1] == "48000"
    else:
        assert "-ar" not in cmd
    assert "+faststart" in cmd


def test_init_failures_are_remembered_by_role_and_reset_when_binary_changes(monkeypatch, isolated_ladder) -> None:
    listing = (FIXTURES / "encoders/lgpl.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: ladder.parse_encoders(listing))
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[-1] != "h264_mf":
            raise subprocess.CalledProcessError(1, cmd, stderr=f"Unknown encoder '{cmd[-1]}'")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    for binary, role in [("first", "export"), ("first", "export"), ("first", "proxy"),
                         ("second", "export"), ("first", "export")]:
        _, selected = ladder.run_h264(binary, role, lambda encoder: [binary, role, encoder])
        assert selected == "h264_mf"
    assert calls == [
        *[["first", "export", encoder] for encoder in PARITY["orders"]["withoutLibx264"]],
        ["first", "export", "h264_mf"],
        *[["first", "proxy", encoder] for encoder in PARITY["orders"]["withoutLibx264"]],
        *[["second", "export", encoder] for encoder in PARITY["orders"]["withoutLibx264"]],
        *[["first", "export", encoder] for encoder in PARITY["orders"]["withoutLibx264"]],
    ]


@pytest.mark.parametrize("role", ["export", "proxy", "scene"])
@pytest.mark.parametrize("restriction", ["", "h264_nvenc"])
def test_gpl_encodes_always_retry_nvenc(monkeypatch, isolated_ladder, role, restriction) -> None:
    listing = (FIXTURES / "encoders/gpl.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: ladder.parse_encoders(listing))
    monkeypatch.setenv(ladder.TEST_ENCODERS_ENV, restriction)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[-1])
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, cmd, stderr="OpenEncodeSessionEx failed: out of memory (10)")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    if restriction:
        with pytest.raises(ladder.NoH264Encoder):
            ladder.run_h264("gpl", role, lambda name: ["gpl", name])
    else:
        assert ladder.run_h264("gpl", role, lambda name: ["gpl", name])[1] == "libx264"
    assert ladder.run_h264("gpl", role, lambda name: ["gpl", name])[1] == "h264_nvenc"
    assert calls == (["h264_nvenc", "h264_nvenc"] if restriction else ["h264_nvenc", "libx264", "h264_nvenc"])


@pytest.mark.parametrize("recover", [False, True])
def test_lgpl_exhausted_memory_retries_last_encoder(monkeypatch, isolated_ladder, recover) -> None:
    listing = (FIXTURES / "encoders/lgpl.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: ladder.parse_encoders(listing))
    calls = []
    order = PARITY["orders"]["withoutLibx264"]

    def run(cmd, **kw):
        calls.append(cmd[-1])
        if recover and len(calls) > len(order):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.CalledProcessError(1, cmd, stderr=f"Unknown encoder '{cmd[-1]}'")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    with pytest.raises(ladder.NoH264Encoder):
        ladder.run_h264("lgpl", "export", lambda name: ["lgpl", name])
    if recover:
        assert ladder.run_h264("lgpl", "export", lambda name: ["lgpl", name])[1] == "h264_mf"
    else:
        with pytest.raises(ladder.NoH264Encoder):
            ladder.run_h264("lgpl", "export", lambda name: ["lgpl", name])
    assert calls == [*order, "h264_mf"]


@pytest.mark.parametrize("role", ["script", "take", "conform"])
def test_gpl_script_and_conform_keep_source_arguments(monkeypatch, isolated_ladder, tmp_path, role) -> None:
    listing = (FIXTURES / "encoders/gpl.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ladder, "probe_encoders", lambda _: ladder.parse_encoders(listing))
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "-show_entries" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "1.0", "")
        Path(cmd[-1]).write_bytes(b"encoded")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ladder.subprocess, "run", run)
    dest = tmp_path / "output.mp4"
    source = tmp_path / "input.wav"
    source.touch()
    if role == "conform":
        assert proxy.conform_vfr(source, dest=dest).status == "ok"
        tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", config.PROXY_AUDIO_BR, "-movflags", "+faststart", str(dest)]
    else:
        if role == "script":
            script_video.make_silent_source(dest, 30, 30, 320, 180)
        else:
            script_video.make_take_source(source, dest, 30, 320, 180)
        tail = ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-c:a", "aac",
                *(["-b:a", "192k"] if role == "take" else []), str(dest)]
    cmd = calls[-1]
    assert cmd[cmd.index("-c:v"):] == tail


def test_unavailable_script_encoder_is_a_public_validation_error() -> None:
    from studio.errors import public_error

    assert public_error(ladder.NoH264Encoder(), 400) == ladder.UNAVAILABLE_MESSAGE


@pytest.mark.parametrize("prefix", ["", "scene render failed: "])
@pytest.mark.parametrize("message", [ladder.UNAVAILABLE_MESSAGE, ladder.PROBE_TIMEOUT_MESSAGE, ladder.PROBE_FAILURE_MESSAGE])
def test_encoder_guidance_reaches_job_and_error_responses(prefix: str, message: str) -> None:
    from studio import compute
    from studio.errors import JSONResponse

    detail = prefix + message
    job = compute._job_presentation({"kind": "render_final", "status": "error", "error": detail})
    assert job["error"] == message
    response = JSONResponse({"error": detail}, status_code=500)
    assert json.loads(bytes(response.body))["error"] == message


@pytest.mark.parametrize("message", [ladder.UNAVAILABLE_MESSAGE, ladder.PROBE_TIMEOUT_MESSAGE, ladder.PROBE_FAILURE_MESSAGE])
def test_encoding_guidance_whitelist_never_reflects_added_diagnostics(message: str) -> None:
    from studio import compute
    from studio.errors import JSONResponse

    detail = message + " Private encoder diagnostics"
    job = compute._job_presentation({"kind": "render_final", "status": "error", "error": detail})
    assert job["error"] == "The job failed during execution."
    response = JSONResponse({"error": detail}, status_code=500)
    assert json.loads(bytes(response.body))["error"] == "Studio could not complete the request."


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("message", [ladder.UNAVAILABLE_MESSAGE, ladder.PROBE_TIMEOUT_MESSAGE, ladder.PROBE_FAILURE_MESSAGE])
def test_proxy_responses_preserve_encoder_guidance(monkeypatch, tmp_path, inline: bool, message: str) -> None:
    import asyncio
    from pipeline.contracts import StageResult
    from studio.routes.jobs import _run_inline_verb
    from studio.routes.proxy import api_make_proxy

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "source.mp4").touch()
    monkeypatch.setattr(proxy, "make_proxy", lambda *args: StageResult(
        "error", "proxy", "source", reason=message,
    ))
    response = (asyncio.run(_run_inline_verb("make_proxy", "source", {})) if inline else api_make_proxy("source"))
    assert json.loads(bytes(response.body))["error"] == message


@pytest.mark.parametrize("role", ["export", "proxy", "conform", "script", "take"])
def test_probe_timeout_reaches_pipeline_callers(monkeypatch, isolated_ladder, tmp_path, role) -> None:
    error = subprocess.TimeoutExpired(["ffmpeg"], 30)

    def run(cmd, **kw):
        if "-show_entries" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "1.0", "")
        raise error

    monkeypatch.setattr(ladder.subprocess, "run", run)
    source, dest = tmp_path / "source.mp4", tmp_path / "output.mp4"
    source.touch()
    if role == "export":
        result = compiler._run_encode(compiler.CompilePlan(inputs=[], video_graph="", audio_graph=""),
                                      dest, "video", project_dir=tmp_path)
    elif role == "proxy":
        result = proxy._encode_proxy(source, dest, "format=yuv420p", clip_id="video")
    elif role == "conform":
        result = proxy.conform_vfr(source, dest=dest)
    else:
        with pytest.raises(ladder.EncoderDiscoveryError, match=ladder.PROBE_TIMEOUT_MESSAGE) as caught:
            if role == "script":
                script_video.make_silent_source(dest, 30, 30, 320, 180)
            else:
                script_video.make_take_source(source, dest, 30, 320, 180)
        from studio.errors import public_error

        assert public_error(caught.value, 400) == ladder.PROBE_TIMEOUT_MESSAGE
        return
    assert result.status == "error" and result.reason == ladder.PROBE_TIMEOUT_MESSAGE
