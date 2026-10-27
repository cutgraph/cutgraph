"""Collect the same verified unsigned assets for hosted builds and local rehearsals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer.manifest import digest
from installer.sources import SOURCE_ARCHIVE

UNSIGNED_FILES = ("SBOM.cdx.json", "THIRD-PARTY-NOTICES.txt", SOURCE_ARCHIVE,
                  "bundle-manifest.json", "installer-build.json")


def collect(source: Path, out: Path) -> list[str]:
    receipt = json.loads((source / "installer-build.json").read_text(encoding="utf-8"))
    name = receipt["installer"]
    if not isinstance(name, str) or not re.fullmatch(r"CutgraphStudio-[0-9][0-9A-Za-z.+-]*-setup\.exe", name):
        raise ValueError("Invalid installer filename")
    if receipt.get("unsigned") is not True:
        raise ValueError("Expected an unsigned installer")
    files = (name, *UNSIGNED_FILES)
    for filename in files:
        path = source / filename
        if path.is_symlink() or path.is_junction() or not path.is_file():
            raise ValueError("Missing or linked release asset")
    if digest(source / name) != receipt["sha256"] or (source / name).stat().st_size != receipt["size"]:
        raise ValueError("Installer does not match its build receipt")
    archive = source / SOURCE_ARCHIVE
    if (digest(archive) != receipt["ffmpegSource"]["sha256"]
            or archive.stat().st_size != receipt["ffmpegSource"]["size"]):
        raise ValueError("Corresponding source does not match the build receipt")
    if out.exists():
        raise ValueError("The release asset output must not exist")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-assets-", dir=out.parent) as temporary:
        stage = Path(temporary) / "assets"
        stage.mkdir()
        for filename in files:
            shutil.copyfile(source / filename, stage / filename)
        stage.rename(out)
    return list(files)


def checksums(directory: Path) -> None:
    files = sorted(path for path in directory.iterdir() if path.name != "SHA256SUMS.txt")
    if not files:
        raise ValueError("No release assets")
    for path in files:
        if (not path.is_file() or path.is_symlink() or path.is_junction()
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", path.name)):
            raise ValueError("Release assets must be regular files with safe filenames")
    content = "".join(f"{digest(path)}  {path.name}\n" for path in files)
    (directory / "SHA256SUMS.txt").write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    copying = commands.add_parser("collect")
    copying.add_argument("source", type=Path)
    copying.add_argument("--out", type=Path, required=True)
    hashing = commands.add_parser("checksums")
    hashing.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "collect":
            collect(args.source, args.out)
        else:
            checksums(args.directory)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Release assets failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
