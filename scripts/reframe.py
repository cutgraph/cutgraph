r"""Face-track one source clip into 9:16 reframe metadata.

    .venv\Scripts\python scripts\reframe.py <source> [--fps 30] [--width 1080] [--height 1920]

Lazily imports the optional MediaPipe tracker and writes outputs/reframe/<stem>.reframe.json
with one crop rectangle per sampled frame. The compiler consumes it when `crop.mode` is "reframe"
and falls back to a static center crop when metadata is absent.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from pipeline import reframe  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", help="path to the source video")
    p.add_argument("--fps", type=int, default=config.DEFAULT_FPS, help="source fps (default 30)")
    p.add_argument("--width", type=int, default=1080, help="target crop width (default 1080)")
    p.add_argument("--height", type=int, default=1920, help="target crop height (default 1920)")
    args = p.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"[reframe] no source: {source}")
        return 1

    meta = reframe.analyze(source, fps=args.fps, target_w=args.width, target_h=args.height)
    print(f"[reframe] ok: {len(meta.frame_crops)} frame(s), "
          f"{meta.face_coverage_pct:.1f}% face coverage -> {config.REFRAME_DIR / f'{source.stem}.reframe.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
