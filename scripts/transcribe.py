r"""CLI entry point for the transcribe stage. Uses CUDA when available, else the CPU:

    .venv\Scripts\python scripts\transcribe.py data\source\clip.mp4
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import addons, transcribe as transcribe_stage  # noqa: E402


def main() -> int:
    addons.activate()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="path to the source video")
    args = parser.parse_args()

    result = transcribe_stage.transcribe(args.video)
    print(json.dumps(dataclasses.asdict(result), indent=2))
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
