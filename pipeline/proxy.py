"""Proxy stage: a small, scrub-friendly H.264 540p proxy of a source clip.

The editor scrubs the proxy, not the source: a short-GOP (-g 12, -bf 0) 540p H.264 seeks smoothly in
a <video> element. The shared H.264 ladder keeps the same short-GOP scrub
profile across GPU and software encoders.

VFR conform: a variable-frame-rate source is first conformed to a CFR 30 mezzanine so the
frame timebase the project.json assumes actually holds. ffmpeg only; runs where the source lives.

10-bit / HDR (real phone footage is yuv420p10le HLG): h264_nvenc encodes 8-bit only, so the filter
chain always ends in format=yuv420p (a no-op for SDR sources — a written deviation from the
reference -vf). HDR transfers additionally get the same HLG/PQ -> SDR tonemap chain the
studio's transcript-player proxy uses, so proxy colors match instead of looking washed out.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import config
from pipeline import contracts
from pipeline.encoder_ladder import EncoderDiscoveryError, NoH264Encoder, encoder_args, run_h264, uses_portable_profiles
from pipeline.runtime_tools import tool_path

STAGE = "proxy"


def _err(clip_id: str, reason: str, details: str | None = None) -> contracts.StageResult:
    return contracts.StageResult(status="error", stage=STAGE, clip_id=clip_id, reason=reason, details=details)


def _parse_rate(rate: str) -> float:
    """Parse an ffprobe frame-rate ('num/den' or a bare number) to a float; 0.0 on anything odd."""
    rate = rate.strip()
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            d = float(den)
            return float(num) / d if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def _probe_stream(source: Path) -> dict[str, str]:
    """One ffprobe call for everything the proxy decision needs: frame rates (VFR detection),
    pixel format and color transfer (10-bit / HDR handling). key=value lines, order-independent."""
    probe = subprocess.run(
        [tool_path("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,avg_frame_rate,pix_fmt,color_transfer",
         "-of", "default=noprint_wrappers=1", str(source)],
        check=True, capture_output=True, text=True,
    )
    info: dict[str, str] = {}
    for line in probe.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            info[key.strip()] = value.strip()
    return info


def _is_vfr(info: dict[str, str]) -> bool:
    """True when the stream's real (r_frame_rate) and average (avg_frame_rate) rates differ — the
    ffprobe signature of variable frame rate. Order-independent: we only test the two for equality."""
    r, a = info.get("r_frame_rate", ""), info.get("avg_frame_rate", "")
    if not r or not a:
        return False
    return _parse_rate(r) != _parse_rate(a)


# HLG (phone video) and PQ transfers: without a tonemap the 8-bit downconvert looks washed out.
_HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}


def _needs_tonemap(info: dict[str, str]) -> bool:
    return info.get("color_transfer", "") in _HDR_TRANSFERS


# The same HLG/PQ -> SDR chain the studio transcript-player proxy uses (needs an ffmpeg with zimg).
_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def _vf(*, hdr: bool) -> str:
    """The -vf chain. Always ends 8-bit yuv420p: h264_nvenc cannot encode 10-bit sources (real phone
    footage is yuv420p10le HLG), and it is a no-op for SDR — the one deviation from the reference
    -vf. HDR transfers get the tonemap chain so the downconvert keeps its colors."""
    scale = f"scale=-2:{config.PROXY_HEIGHT}"
    if hdr:
        return f"{_TONEMAP},{scale}"
    return f"{scale},format=yuv420p"


def _proxy_cmd(src: Path, out: Path, *, encoder: str, vf: str, portable: bool = False) -> list[str]:
    """The 540p scrub profile, with identical keyframe spacing on every rung."""
    return [
        tool_path("ffmpeg"), "-y", "-i", str(src), "-vf", vf,
        *encoder_args("proxy", encoder, {"gop": str(config.PROXY_GOP), "bframes": str(config.PROXY_BFRAMES),
                                         "cq": str(config.PROXY_CQ)}),
        "-c:a", "aac", "-b:a", config.PROXY_AUDIO_BR, *(["-ar", "48000"] if portable else []),
        "-movflags", "+faststart", str(out),
    ]


def conform_vfr(source: Path, *, dest: Path | None = None) -> contracts.StageResult:
    """Conform a source to a CFR 30 mezzanine (<stem>_cfr.mp4) so the project's fixed 30 timebase
    actually holds before the proxy encode. Uses the shared conform profile."""
    src = Path(source)
    if not src.exists():
        return _err(src.stem, "source not found", str(src))
    # Full-res mezzanine lives in a mezzanine/ subdir: the flat GET /media/proxy/{stem} route can't
    # reach it (its _safe check forbids path separators), so this intermediate is never web-served.
    dest = Path(dest) if dest is not None else config.PROXIES_DIR / "mezzanine" / f"{src.stem}_cfr.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)

    def command(encoder: str) -> list[str]:
        return [
            tool_path("ffmpeg"), "-y", "-i", str(src),
            "-fps_mode", "cfr", "-r", str(config.DEFAULT_FPS),
            *encoder_args("conform", encoder),
            *(["-pix_fmt", "yuv420p"] if portable else []),
            "-c:a", "aac", "-b:a", config.PROXY_AUDIO_BR, *(["-ar", "48000"] if portable else []),
            "-movflags", "+faststart", str(dest),
        ]
    try:
        ffmpeg = tool_path("ffmpeg")
        portable = uses_portable_profiles(ffmpeg)
        _, encoder = run_h264(ffmpeg, "conform", command)
    except (EncoderDiscoveryError, NoH264Encoder) as e:
        return _err(src.stem, str(e))
    except subprocess.CalledProcessError as e:
        return _err(src.stem, "ffmpeg vfr conform failed", e.stderr)
    return contracts.StageResult(
        status="ok", stage=STAGE, clip_id=src.stem, output_path=str(dest),
        metrics={"vfr": True, "height": config.PROXY_HEIGHT, "encoder": encoder},
    )


def _encode_proxy(encode_src: Path, dest: Path, vf: str, *, clip_id: str) -> contracts.StageResult:
    try:
        ffmpeg = tool_path("ffmpeg")
        portable = uses_portable_profiles(ffmpeg)
        _, encoder = run_h264(ffmpeg, "proxy",
                             lambda encoder: _proxy_cmd(encode_src, dest, encoder=encoder, vf=vf, portable=portable))
    except (EncoderDiscoveryError, NoH264Encoder) as e:
        return _err(clip_id, str(e))
    except subprocess.CalledProcessError as e:
        return _err(clip_id, "ffmpeg proxy encode failed", e.stderr)
    return contracts.StageResult(status="ok", stage=STAGE, clip_id=clip_id, metrics={"encoder": encoder})


def make_proxy(source: Path, *, dest: Path | None = None) -> contracts.StageResult:
    """Encode a scrub-friendly 540p proxy of `source` into PROXIES_DIR/<stem>_540.mp4.

    VFR sources are conformed to a CFR mezzanine first. Output is always 8-bit yuv420p; HDR
    sources are tonemapped (see _vf). The shared ladder preserves short-GOP spacing on every encoder.
    If the tonemap chain fails only because this ffmpeg build lacks zscale, retries once with the
    plain chain — any other error surfaces unchanged.
    """
    src = Path(source)
    if not src.exists():
        return _err(src.stem, "source not found", str(src))
    dest = Path(dest) if dest is not None else config.PROXIES_DIR / f"{src.stem}_540.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        info = _probe_stream(src)
    except subprocess.CalledProcessError as e:
        return _err(src.stem, "ffprobe failed", e.stderr)
    vfr = _is_vfr(info)
    hdr = _needs_tonemap(info)

    encode_src = src
    if vfr:
        conform = conform_vfr(src)
        if conform.status != "ok":
            return conform
        encode_src = Path(conform.output_path or "")

    # ffmpeg writes in place, and the studio serves this file while it exists: encode beside it and
    # rename, so `dest` is only ever absent or complete.
    part = dest.with_name(f"{dest.stem}.part{dest.suffix}")
    try:
        result = _encode_proxy(encode_src, part, _vf(hdr=hdr), clip_id=src.stem)
        if result.status != "ok" and hdr and "no such filter" in (result.details or "").lower():
            result = _encode_proxy(encode_src, part, _vf(hdr=False), clip_id=src.stem)
        if result.status != "ok":
            return result
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)

    return contracts.StageResult(
        status="ok", stage=STAGE, clip_id=src.stem, output_path=str(dest),
        metrics={"vfr": vfr, "height": config.PROXY_HEIGHT, "hdr": hdr, **result.metrics},
    )
