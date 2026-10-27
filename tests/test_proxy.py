"""Tests for the proxy stage (pipeline/proxy.py).

Import-light: no torch/whisperx, no GPU. Every default-run test mocks subprocess so no real encode
(and no NVENC) is required — argv shape, the run.log append, the VFR parse/conform, and the libx264
fallback are all asserted against a recorded command list. The real-ffmpeg encode is guarded behind
shutil.which("ffmpeg") (it naturally falls back to libx264 where NVENC is absent).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import config
from pipeline import encoder_ladder
from pipeline import proxy
from pipeline.runtime_tools import tool_path


class _Recorder:
    """Fake subprocess.run: record each argv and create the output file so output_path exists."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"proxy")
        return subprocess.CompletedProcess(cmd, 0, "", "")


_SDR_INFO = {"r_frame_rate": "30/1", "avg_frame_rate": "30/1", "pix_fmt": "yuv420p",
             "color_transfer": "bt709"}
_HLG_INFO = {"r_frame_rate": "30/1", "avg_frame_rate": "30/1", "pix_fmt": "yuv420p10le",
             "color_transfer": "arib-std-b67"}
_VFR_INFO = {"r_frame_rate": "1000/1", "avg_frame_rate": "30000/1001", "pix_fmt": "yuv420p",
             "color_transfer": "bt709"}


# ── exact NVENC argv (the reference command + the yuv420p 10-bit deviation) + run.log append ──────────
def test_make_proxy_builds_exact_nvenc_argv(tmp_path, monkeypatch) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "clip_540.mp4"
    log = tmp_path / "run.log"
    monkeypatch.setattr(config, "RUN_LOG", log)
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_SDR_INFO))
    rec = _Recorder()
    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", rec)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "ok"
    assert result.metrics == {"vfr": False, "height": 540, "hdr": False, "encoder": "h264_nvenc"}
    assert result.output_path == str(dest)
    assert rec.calls == [[
        tool_path("ffmpeg"), "-y", "-i", str(src),
        "-vf", "scale=-2:540,format=yuv420p",
        "-c:v", "h264_nvenc", "-g", "12", "-bf", "0", "-cq", "28",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", str(tmp_path / "clip_540.part.mp4"),
    ]]
    assert dest.read_bytes() == b"proxy"
    assert not (tmp_path / "clip_540.part.mp4").exists()
    assert " ".join(rec.calls[0]) + "\n" in log.read_text(encoding="utf-8")


def test_make_proxy_default_dest_is_540_suffix_in_proxies_dir(tmp_path, monkeypatch) -> None:
    src = tmp_path / "myclip.mp4"
    src.write_bytes(b"video")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(config, "PROXIES_DIR", tmp_path / "proxies")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_SDR_INFO))
    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", _Recorder())

    result = proxy.make_proxy(src)

    assert result.output_path == str(tmp_path / "proxies" / "myclip_540.mp4")


def test_make_proxy_missing_source_errors(tmp_path) -> None:
    result = proxy.make_proxy(tmp_path / "nope.mp4")
    assert result.status == "error"
    assert result.stage == "proxy"


# ── probe parse + _is_vfr / _needs_tonemap decision cases ───────────────────────
def test_probe_stream_parses_key_value_lines(tmp_path, monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        assert "ffprobe" in cmd[0]
        out = ("r_frame_rate=1000/1\navg_frame_rate=30000/1001\n"
               "pix_fmt=yuv420p10le\ncolor_transfer=arib-std-b67\n")
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", fake_run)
    info = proxy._probe_stream(tmp_path / "x.mp4")
    assert info == {"r_frame_rate": "1000/1", "avg_frame_rate": "30000/1001",
                    "pix_fmt": "yuv420p10le", "color_transfer": "arib-std-b67"}


def test_is_vfr_true_when_rates_differ() -> None:
    assert proxy._is_vfr({"r_frame_rate": "1000/1", "avg_frame_rate": "30000/1001"}) is True


def test_is_vfr_false_when_rates_equal() -> None:
    assert proxy._is_vfr({"r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001"}) is False


def test_is_vfr_false_on_missing_rate() -> None:
    assert proxy._is_vfr({"r_frame_rate": "30/1"}) is False


@pytest.mark.parametrize("transfer,expected", [
    ("arib-std-b67", True),   # HLG (phone video)
    ("smpte2084", True),      # PQ
    ("bt709", False),
    ("", False),
])
def test_needs_tonemap_by_transfer(transfer, expected) -> None:
    assert proxy._needs_tonemap({"color_transfer": transfer}) is expected


# ── VFR path inserts the -fps_mode cfr -r 30 conform step ─────────────────────────
def test_make_proxy_vfr_inserts_conform_step(tmp_path, monkeypatch) -> None:
    src = tmp_path / "vfr.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "vfr_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(config, "PROXIES_DIR", tmp_path)  # conform's default mezzanine dest
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_VFR_INFO))
    rec = _Recorder()
    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", rec)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "ok"
    assert result.metrics["vfr"] is True
    assert len(rec.calls) == 2
    conform_cmd, proxy_cmd = rec.calls
    # conform mezzanine: CFR 30
    assert "-fps_mode" in conform_cmd
    assert conform_cmd[conform_cmd.index("-fps_mode") + 1] == "cfr"
    assert conform_cmd[conform_cmd.index("-r") + 1] == "30"
    mezz = conform_cmd[-1]
    # mezzanine lands in a subdir so the flat GET /media/proxy/{stem} route can never web-serve it
    assert mezz == str(tmp_path / "mezzanine" / "vfr_cfr.mp4")
    # proxy encodes FROM the mezzanine, still NVENC
    assert proxy_cmd[proxy_cmd.index("-i") + 1] == mezz
    assert proxy_cmd[proxy_cmd.index("-c:v") + 1] == "h264_nvenc"
    assert proxy_cmd[-1] == str(tmp_path / "vfr_540.part.mp4")
    assert dest.is_file()


# ── libx264 fallback ONLY on NVENC-init failure ───────────────────────────────────
def test_make_proxy_falls_back_to_libx264_on_nvenc_init_failure(tmp_path, monkeypatch) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "clip_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_SDR_INFO))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "h264_nvenc" in cmd:
            raise subprocess.CalledProcessError(
                1, cmd, output="", stderr="[h264_nvenc @ 0x0] Cannot load nvcuda.dll")
        Path(cmd[-1]).write_bytes(b"proxy")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "ok"
    assert len(calls) == 2
    fallback = calls[1]
    assert fallback[fallback.index("-c:v") + 1] == "libx264"
    assert "veryfast" in fallback
    assert "-crf" in fallback
    assert "-cq" not in fallback  # NVENC-only flag must not appear on the libx264 path
    # short-GOP scrub property preserved
    assert fallback[fallback.index("-g") + 1] == "12"
    assert fallback[fallback.index("-bf") + 1] == "0"
    assert "+faststart" in fallback


def test_make_proxy_non_nvenc_failure_does_not_fall_back(tmp_path, monkeypatch) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "clip_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_SDR_INFO))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="moov atom not found")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "error"
    assert len(calls) == 1  # no libx264 retry on a genuine encode error


def test_make_proxy_failure_leaves_neither_the_proxy_nor_its_partial_file(tmp_path, monkeypatch) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "clip_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_SDR_INFO))

    def half_written(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"half a")
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="Conversion failed!")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", half_written)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "error"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip.mp4", "run.log"]


# ── the banner-mentions-nvenc trap: a real error must NOT be masked by a needless fallback ────────
# REUSE-IgnoreStart
_BANNER = (
    "ffmpeg version 7.0 Copyright (c) the FFmpeg developers\n"
    "  configuration: --enable-gpl --enable-nvenc --enable-cuda\n"
)
# REUSE-IgnoreEnd


def test_make_proxy_real_error_with_nvenc_banner_does_not_fall_back(tmp_path, monkeypatch) -> None:
    # ffmpeg prints '--enable-nvenc' in its banner on EVERY run; a bare 'nvenc' substring match would
    # falsely fall back and swallow the real error. Only genuine init signatures may trigger fallback.
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "clip_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_SDR_INFO))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        raise subprocess.CalledProcessError(
            1, cmd, output="", stderr=_BANNER + "[in#0 @ 0x0] moov atom not found")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "error"
    assert len(calls) == 1  # banner alone must not mask the real error
    assert "moov atom not found" in (result.details or "")


@pytest.mark.parametrize("signature", [
    "Unknown encoder 'h264_nvenc'",
    "[h264_nvenc @ 0x0] Cannot load nvcuda.dll",
    "[h264_nvenc @ 0x0] Cannot load libcuda.so.1",
    "[h264_nvenc @ 0x0] No NVENC capable devices found",
    # ffmpeg 8.x phrasing observed when processing HLG footage
    "[h264_nvenc @ 0x0] 10 bit encode not supported\n[h264_nvenc @ 0x0] No capable devices found",
    "[h264_nvenc @ 0x0] Cannot init CUDA",
    "[h264_nvenc @ 0x0] Driver does not support the required nvenc API version",
    "[h264_nvenc @ 0x0] OpenEncodeSessionEx failed: out of memory (10)",
])
def test_make_proxy_each_init_signature_falls_back(tmp_path, monkeypatch, signature) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "clip_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_SDR_INFO))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "h264_nvenc" in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr=_BANNER + signature)
        Path(cmd[-1]).write_bytes(b"proxy")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "ok"
    assert len(calls) == 2
    assert calls[1][calls[1].index("-c:v") + 1] == "libx264"


# ── HDR / 10-bit handling ─────────────────────────────────────────────────────────
def test_make_proxy_hdr_source_gets_tonemap_chain(tmp_path, monkeypatch) -> None:
    src = tmp_path / "hlg.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "hlg_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_HLG_INFO))
    rec = _Recorder()
    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", rec)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "ok"
    assert result.metrics == {"vfr": False, "height": 540, "hdr": True, "encoder": "h264_nvenc"}
    vf = rec.calls[0][rec.calls[0].index("-vf") + 1]
    assert vf.startswith("zscale=t=linear")
    assert "tonemap=tonemap=hable" in vf
    assert vf.endswith("format=yuv420p,scale=-2:540")


def test_make_proxy_hdr_missing_zscale_retries_plain_chain(tmp_path, monkeypatch) -> None:
    # An ffmpeg without zimg fails the tonemap chain with "No such filter" — retry ONCE with the
    # plain 8-bit chain so the proxy still lands (colors washed beats no proxy at all).
    src = tmp_path / "hlg.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "hlg_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_HLG_INFO))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        vf = cmd[cmd.index("-vf") + 1]
        if vf.startswith("zscale"):
            raise subprocess.CalledProcessError(
                1, cmd, output="", stderr="[AVFilterGraph @ 0x0] No such filter: 'zscale'")
        Path(cmd[-1]).write_bytes(b"proxy")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "ok"
    assert len(calls) == 2
    assert calls[1][calls[1].index("-vf") + 1] == "scale=-2:540,format=yuv420p"


def test_make_proxy_hdr_real_error_does_not_retry_plain(tmp_path, monkeypatch) -> None:
    src = tmp_path / "hlg.mp4"
    src.write_bytes(b"video")
    dest = tmp_path / "hlg_540.mp4"
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proxy, "_probe_stream", lambda s: dict(_HLG_INFO))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="moov atom not found")

    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    result = proxy.make_proxy(src, dest=dest)

    assert result.status == "error"
    assert len(calls) == 1
    assert "moov atom not found" in (result.details or "")


# ── guarded real-ffmpeg encode (falls back to libx264 where NVENC is absent) ──────
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_make_proxy_real_encode(tmp_path) -> None:
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=1",
         "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-t", "1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
        check=True, capture_output=True, text=True,
    )
    dest = tmp_path / "src_540.mp4"
    result = proxy.make_proxy(src, dest=dest)
    assert result.status == "ok", result.details
    assert dest.exists()
    assert dest.stat().st_size > 0
    assert result.metrics["height"] == 540
