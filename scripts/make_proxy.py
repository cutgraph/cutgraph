r"""Encode a scrub-friendly 540p H.264 proxy of one source clip.

    .venv\Scripts\python scripts\make_proxy.py <source> [--dest <path>]

Writes outputs/proxies/<stem>_540.mp4 (short-GOP so the editor's <video> seeks smoothly). VFR
sources are conformed to CFR 30 first; encoding follows the shared H.264 ladder. Runs where the
source video and ffmpeg are available; the studio's ingest hook calls the same verb on upload.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import proxy  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", help="path to the source video")
    p.add_argument("--dest", help="output path (default outputs/proxies/<stem>_540.mp4)")
    args = p.parse_args()

    dest = Path(args.dest) if args.dest else None
    result = proxy.make_proxy(Path(args.source), dest=dest)
    if result.status == "ok":
        print(f"[proxy] ok: {result.output_path}  {result.metrics}")
    else:
        print(f"[proxy] error: {result.reason}")
        if result.details:
            print(result.details)
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
