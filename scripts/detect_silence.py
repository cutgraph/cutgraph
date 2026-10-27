r"""Propose ripple cuts over the long silent pauses in one source clip.

    .venv\Scripts\python scripts\detect_silence.py <source> [--fps 30] [--engine silero|silencedetect]

Prints the {"cuts":[...]} proposal JSON (never auto-applied — the studio accepts them as a batch).
Auto-routes to Silero VAD when its wheel is installed, otherwise to ffmpeg silencedetect.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from pipeline import silence  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", help="path to the source video/audio")
    p.add_argument("--fps", type=int, default=config.DEFAULT_FPS, help="project fps (frame quantization)")
    p.add_argument("--engine", choices=["silero", "silencedetect"], help="override auto-detection")
    p.add_argument("--min-pause", type=float, default=config.SILENCE_MIN_PAUSE_S)
    p.add_argument("--out", help="write the JSON here instead of stdout")
    args = p.parse_args()

    result = silence.detect(Path(args.source), args.fps, engine=args.engine, min_pause=args.min_pause)
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[silence] {len(result['cuts'])} proposal(s) -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
