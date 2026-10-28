"""Reproducible, hash-pinned FFmpeg corresponding-source release artifact."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile
import subprocess

from installer.build import download, outside_checkout
from installer.manifest import digest

SOURCE_ARCHIVE = "ffmpeg-corresponding-source.zip"


def load_inputs(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not data.get("inputs"):
        raise ValueError("Source and notice inputs are required")
    names = set()
    for item in data["inputs"]:
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item["name"])
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or not item["url"].startswith("https://")
                or not str(item.get("mirror", "https://")).startswith("https://")):
            raise ValueError("Source inputs require safe names, HTTPS and SHA-256")
        if item["name"] in names:
            raise ValueError("Duplicate source input")
        names.add(item["name"])
        if not item.get("license") or not item.get("version"):
            raise ValueError("Source inputs need versions and licences")
    return data


def verify_binary(executable: Path, inputs: dict, env: dict[str, str]) -> None:
    result = subprocess.run([str(executable), "-version"], capture_output=True, text=True,
                            check=True, timeout=30, env=env)
    lines = result.stdout.splitlines()
    if (not lines or not lines[0].startswith("ffmpeg version " + inputs["ffmpegVersion"] + " ")
            or "configuration: " + inputs["configure"] not in lines):
        raise ValueError("FFmpeg version/configuration differs from corresponding-source evidence")


def write_archive(path: Path, files: dict[str, Path | bytes]) -> None:
    """Stable ZIP metadata; stream large inputs without loading them into memory."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with bundle.open(info, "w") as target:
                if isinstance(value, bytes):
                    target.write(value)
                else:
                    with value.open("rb") as source:
                        shutil.copyfileobj(source, target)


def produce(inputs: dict, cache: Path, out: Path, *, offline: bool = False) -> dict:
    """Publish the exact source archives and recipe, plus a path-free receipt."""
    out = outside_checkout(out, Path(__file__).resolve().parents[1])
    cache = outside_checkout(cache, Path(__file__).resolve().parents[1])
    if out.exists() or out == cache or out in cache.parents or cache in out.parents:
        raise ValueError("Source output must be new and separate from the cache")
    if inputs.get("unresolved"):
        raise ValueError("Corresponding source has unresolved inputs")
    if any(item.get("group") in {"ffmpeg", "recipe"}
           and not item.get("noticeOnly") and not item.get("correspondingSource")
           for item in inputs["inputs"]):
        raise ValueError("All FFmpeg dependency sources must accompany the distribution")
    cache.mkdir(parents=True, exist_ok=True)
    selected = [item for item in inputs["inputs"] if item.get("correspondingSource")]
    if not {"ffmpeg-source", "ffmpeg-recipe"} <= {item["name"] for item in selected}:
        raise ValueError("Exact FFmpeg and build recipe sources are required")
    files: dict[str, Path | bytes] = {}
    records = []
    for item in selected:
        archive = download(item, cache, offline=offline)
        name = "sources/" + item["name"] + (".zip" if zipfile.is_zipfile(archive) else ".tar.gz")
        files[name] = archive
        records.append({**item, "path": name, "size": archive.stat().st_size})
    receipt = {"schemaVersion": 1, "binarySha256": inputs["binarySha256"],
               "ffmpegVersion": inputs["ffmpegVersion"], "recipeCommit": inputs["recipeCommit"],
               "recipeEvidence": inputs["recipeEvidence"], "configure": inputs["configure"],
               "sources": records,
               "libraries": [{key: item[key] for key in ("name", "version", "license", "recipe") if key in item}
                             for item in inputs["inputs"] if item["group"] == "ffmpeg"]}
    files["manifest.json"] = (json.dumps(receipt, indent=2) + "\n").encode()
    files["configure.txt"] = (inputs["configure"] + "\n").encode()
    files["README.txt"] = (
        "FFmpeg corresponding source\n\n"
        "The source archives retain upstream trees. The build recipe contains patches and\n"
        "build instructions; use its win64-lgpl-shared variant and 8.1 add-in.\n"
        "The configure line was measured from the pinned executable. Dependency sources\n"
        "include both copyleft and permissive libraries in the pinned build.\n"
        "manifest.json lists their exact revisions and SHA-256 values.\n"
        "Publish this archive beside the binary distribution.\n"
    ).encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ffmpeg-source-", dir=out.parent) as temporary:
        stage = Path(temporary)
        artifact = stage / SOURCE_ARCHIVE
        write_archive(artifact, files)
        receipt["artifact"] = {"name": SOURCE_ARCHIVE, "sha256": digest(artifact), "size": artifact.stat().st_size}
        (stage / "manifest.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        stage.rename(out)
    return receipt
