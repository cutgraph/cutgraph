"""List changed fixtures and pixel goldens, including uncommitted/untracked files.

Usage from the repository root: python tests/fixtures/diff_report.py [--base origin/main]
No network or Git writes. The base is a local ref and must resolve.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def is_fixture(path: str) -> bool:
    return path.startswith("tests/fixtures/") or (path.startswith("motion-core/test/") and ".golden." in path)


def digest(data: bytes | None) -> str:
    return sha256(data).hexdigest()[:12] if data is not None else "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    base = git("rev-parse", "--verify", f"{args.base}^{{commit}}").decode().strip()
    originals = {p for p in git("ls-tree", "-r", "--name-only", base).decode().splitlines() if is_fixture(p)}
    current = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests/fixtures").rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    current.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "motion-core/test").glob("*.golden.*json"))
    changed = 0
    print(f"Fixture diff against {args.base} ({base[:12]}):")
    for path in sorted(originals | current):
        before = git("show", f"{base}:{path}") if path in originals else None
        after = (ROOT / path).read_bytes() if path in current else None
        if before == after:
            continue
        status = "added" if before is None else "deleted" if after is None else "modified"
        print(f"{status:8} {path}  {digest(before)} -> {digest(after)}")
        changed += 1
    print(f"{changed} changed files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
