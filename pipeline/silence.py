"""Silence / pause detection: turn long silent gaps into ripple-cut PROPOSALS.

Two detection engines feed ONE pure, fully-tested reducer (`gaps_to_proposals`):
  - Silero VAD: speech spans -> their complement gaps. The optional wheel is imported lazily
    inside `_silero_speech` so this module imports without inference dependencies.
  - ffmpeg silencedetect (CPU fallback): -30 dB threshold, gaps parsed from stderr.
`detect` auto-routes to Silero iff `silero_vad` is importable, else silencedetect.

Proposals are NEVER auto-applied: `detect` returns {"cuts":[...]} JSON only; accepting is a separate
batch ripple_cut in the UI (one accept = one revision = one undo checkpoint). 150 ms of silence is
kept as breath padding each side of every cut, and any gap overlapping a user-kept range is dropped.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import config
from pipeline.timebase import sec_to_frame
from pipeline.runtime_tools import tool_path

_VAD_SR = 16000  # Silero VAD wants 16 kHz mono (same rate transcribe already extracts)


def _log_ffmpeg(cmd: list[str]) -> None:
    with open(config.RUN_LOG, "a", encoding="utf-8") as fh:
        fh.write(" ".join(cmd) + "\n")


def gaps_to_proposals(
    gaps: list[tuple[float, float]], fps: int, *,
    pad_ms: int = config.SILENCE_PAD_MS,
    min_pause: float = config.SILENCE_MIN_PAUSE_S,
    kept_ranges: list[tuple[int, int]] | None = None,
) -> dict:
    """PURE, torch-free reducer (both engines feed it). For each silent gap [g0, g1] longer than
    `min_pause`, keep `pad_ms` of silence each side and propose cutting the interior. Frames are
    SOURCE-space (measured on the original media audio) — hence `srcStartF`/`srcEndF`; the field
    names make the space unambiguous. Conversion to timeline frames happens only at accept
    time in the UI. `conf` scales with how far the gap exceeds one min_pause:
    min(1.0, (g1-g0)/(2*min_pause)) — a gap of exactly min_pause reads 0.5, a gap of 2·min_pause
    (or longer) reads a full 1.0. `kept_ranges` are source-space too: any proposal overlapping one is
    dropped so a dismissed pause is never re-proposed."""
    pad = pad_ms / 1000.0
    kept = kept_ranges or []
    cuts: list[dict] = []
    for g0, g1 in gaps:
        if (g1 - g0) <= min_pause:
            continue
        cut_start, cut_end = g0 + pad, g1 - pad
        if cut_end <= cut_start:
            continue
        srcStartF, srcEndF = sec_to_frame(cut_start, fps), sec_to_frame(cut_end, fps)
        if srcEndF <= srcStartF:
            continue
        if any(ks < srcEndF and srcStartF < ke for ks, ke in kept):
            continue
        cuts.append({
            "srcStartF": srcStartF,
            "srcEndF": srcEndF,
            "durSec": round(cut_end - cut_start, 3),
            "kind": "pause",
            "conf": round(min(1.0, (g1 - g0) / (2 * min_pause)), 3),
        })
    return {"cuts": cuts}


def _speech_to_gaps(speech: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Complement of speech spans: the silent interval between each consecutive pair. Leading and
    trailing silence at the clip edges are not pauses to cut, so only interior gaps are returned."""
    return [(e0, s1) for (_s0, e0), (s1, _e1) in zip(speech, speech[1:]) if s1 > e0]


def _parse_silencedetect(stderr: str) -> list[tuple[float, float]]:
    """silencedetect logs `silence_start: <t>` / `silence_end: <t>` to STDERR; pair them in order."""
    starts: list[float] = []
    ends: list[float] = []
    for line in stderr.splitlines():
        ms = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if ms:
            starts.append(float(ms.group(1)))
        me = re.search(r"silence_end:\s*(-?[\d.]+)", line)
        if me:
            ends.append(float(me.group(1)))
    return list(zip(starts, ends))


def _silencedetect_gaps(source: Path, min_pause: float = config.SILENCE_MIN_PAUSE_S) -> list[tuple[float, float]]:
    cmd = [
        tool_path("ffmpeg"), "-i", str(source),
        "-af", f"silencedetect=n={config.SILENCE_NOISE_DB}dB:d={min_pause}",
        "-f", "null", "-",
    ]
    _log_ffmpeg(cmd)
    # silencedetect logs to stderr and exits 0; capture without check so a benign non-zero
    # (e.g. `-f null -`) surfaces the parsed gaps rather than raising.
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return _parse_silencedetect(proc.stderr)


def _ensure_wav(source: Path) -> Path:
    """Reuse AUDIO_DIR/<slug>.wav if transcribe already produced one, else extract it (16 kHz mono
    pcm_s16le — the transcribe recipe) so Silero reads a clean wav."""
    source = Path(source)
    wav = config.AUDIO_DIR / f"{source.stem}.wav"
    if wav.exists():
        return wav
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        tool_path("ffmpeg"), "-y", "-i", str(source),
        "-ar", str(_VAD_SR), "-ac", "1", "-c:a", "pcm_s16le", str(wav),
    ]
    _log_ffmpeg(cmd)
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return wav


def _silero_speech(wav: Path, sr: int = _VAD_SR) -> list[tuple[float, float]]:
    from pipeline import silero_runtime

    silero, model = silero_runtime.load()
    audio = silero_runtime.read_audio(wav, sampling_rate=sr)
    spans = silero.get_speech_timestamps(audio, model, sampling_rate=sr, return_seconds=True)
    return [(float(s["start"]), float(s["end"])) for s in spans]


def detect(
    source: Path, fps: int, *,
    kept_ranges: list[tuple[int, int]] | None = None,
    engine: str | None = None,
    min_pause: float = config.SILENCE_MIN_PAUSE_S,
) -> dict:
    """Returns {"cuts":[{srcStartF, srcEndF, durSec, kind:"pause", conf}]} in SOURCE-frame space.
    `engine` None ⇒ auto: Silero if its wheel is importable, else the ffmpeg
    silencedetect fallback."""
    engine = engine or ("silero" if importlib.util.find_spec("silero_vad") else "silencedetect")
    if engine == "silero":
        gaps = _speech_to_gaps(_silero_speech(_ensure_wav(source)))
    else:
        gaps = _silencedetect_gaps(source, min_pause=min_pause)
    return gaps_to_proposals(gaps, fps, min_pause=min_pause, kept_ranges=kept_ranges)
