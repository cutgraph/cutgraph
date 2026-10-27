r"""Build a waveform-peaks sidecar for one source clip (reused by the speech track).

    .venv\Scripts\python scripts\make_peaks.py <source> [--out <path>]

Pipes 8 kHz mono s16le PCM out of ffmpeg and reduces it to one [min, max] pair per 50 ms window,
writing outputs/transcripts/<slug>.peaks.json = {"sampleRate":8000,"windowMs":50,"peaks":[[min,max],…]}.
Deliberately stdlib-only (`array`, no numpy) so the module imports without scientific computing dependencies.
"""
from __future__ import annotations

import argparse
import array
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from pipeline.runtime_tools import tool_path


def _log_ffmpeg(cmd: list[str]) -> None:
    with open(config.RUN_LOG, "a", encoding="utf-8") as fh:
        fh.write(" ".join(cmd) + "\n")


def _reduce(buf: bytes) -> list[list[int]]:
    """Reduce raw s16le mono PCM to [min, max] pairs, one per PEAKS_WINDOW_MS window. `array('h')`
    is native-endian; supported x86-64 platforms are little-endian, matching ffmpeg's s16le."""
    win = config.PEAKS_SAMPLE_RATE * config.PEAKS_WINDOW_MS // 1000  # 8000 * 50 / 1000 = 400
    samples = array.array("h")
    samples.frombytes(buf[: len(buf) - (len(buf) % 2)])  # ignore a stray trailing byte
    peaks: list[list[int]] = []
    for i in range(0, len(samples), win):
        window = samples[i:i + win]
        if window:
            peaks.append([min(window), max(window)])
    return peaks


def make_peaks(source: Path, out: Path | None = None) -> Path:
    source = Path(source)
    out = Path(out) if out else config.TRANSCRIPTS_DIR / f"{source.stem}.peaks.json"
    cmd = [
        tool_path("ffmpeg"), "-v", "error", "-i", str(source),
        "-ac", "1", "-ar", str(config.PEAKS_SAMPLE_RATE), "-f", "s16le", "-",
    ]
    _log_ffmpeg(cmd)
    proc = subprocess.run(cmd, check=True, capture_output=True)
    peaks = _reduce(proc.stdout)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({
            "sampleRate": config.PEAKS_SAMPLE_RATE,
            "windowMs": config.PEAKS_WINDOW_MS,
            "peaks": peaks,
        }),
        encoding="utf-8",
    )
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", help="path to the source video/audio")
    p.add_argument("--out", help="output path (default outputs/transcripts/<slug>.peaks.json)")
    args = p.parse_args()

    out = make_peaks(Path(args.source), Path(args.out) if args.out else None)
    print(f"[peaks] ok: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
