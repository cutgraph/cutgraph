"""Generate distribution notices and prove licence/SBOM coverage of every origin."""
from __future__ import annotations

from importlib import metadata
import base64
import json
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile
from typing import Mapping, cast
import zipfile

from installer.build import download
from installer.manifest import digest
from scripts import sbom

NOTICE_FILE = "THIRD-PARTY-NOTICES.txt"
SBOM_FILE = "SBOM.cdx.json"
INVENTORY = "licenses/inventory.json"


def license_file(name: str) -> bool:
    return bool(re.match(r"^(licen[cs]e|copying|notice|authors|copyright)(?:[._-]|$)",
                         PurePosixPath(name).name, re.IGNORECASE))


def archive_notices(path: Path) -> list[tuple[str, bytes]]:
    """Read notices without extracting or executing upstream build scripts."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            return [(item.filename, archive.read(item)) for item in archive.infolist()
                    if not item.is_dir() and license_file(item.filename)]
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            result = []
            for item in archive:
                if item.isfile() and license_file(item.name):
                    stream = archive.extractfile(item)
                    if stream is not None:
                        result.append((item.name, stream.read()))
            return result
    return [(path.name, path.read_bytes())]


def font_version(path: Path) -> str:
    data = path.read_bytes()
    tables = struct.unpack_from(">H", data, 4)[0]
    for index in range(tables):
        tag, _, offset, _ = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        if tag != b"name":
            continue
        _, count, strings = struct.unpack_from(">HHH", data, offset)
        for row in range(count):
            platform, _, _, name, length, start = struct.unpack_from(">HHHHHH", data, offset + 6 + row * 12)
            if name == 5:
                raw = data[offset + strings + start:offset + strings + start + length]
                return raw.decode("utf-16-be" if platform in {0, 3} else "mac_roman").strip()
    raise ValueError(f"Font has no version: {path.name}")


def validate_coverage(root: Path, origins: Mapping[str, str], entries: list[dict], document: dict) -> None:
    """A named licence alone cannot stand in for a missing SBOM or file owner."""
    refs = {component["purl"] for component in document["components"]}
    covered = set()
    for entry in entries:
        component = sbom._component(entry["ecosystem"], entry["name"], entry["version"], entry["license"])
        if component["purl"] not in refs:
            raise ValueError(f"Missing SBOM component: {entry['name']}")
        if (not entry.get("notices") or any(not (root / name).is_file()
                                            or not (root / name).stat().st_size for name in entry["notices"])):
            raise ValueError(f"Missing licence entry: {entry['name']}")
        for row in entry["files"]:
            if row["path"] not in origins or digest(root / row["path"]) != row["sha256"]:
                raise ValueError(f"Invalid component file evidence: {entry['name']}")
            covered.add(row["path"])
    missing = {name for name, origin in origins.items() if origin != "generated:licensing"} - covered
    if missing:
        raise ValueError(f"Missing licence/SBOM ownership: {sorted(missing)}")


def generate(root: Path, origins: dict[str, str], pins: dict[str, dict], inputs: dict,
             cache: Path, *, offline: bool, ui: Path | None = None) -> dict:
    """Use installed metadata, never the build interpreter's distribution metadata."""
    entries: list[dict] = []
    notices = ["THIRD-PARTY NOTICES", "", "Cutgraph Studio", "See LICENSE and licenses/product/.", "",
               "This software uses code of FFmpeg licensed under the LGPLv3.",
               "The bundled FFmpeg contains H.264 code and OpenH264 code (BSD-2-Clause).",
               "H.264 may be covered by patents in some countries. No patent licence is granted.",
               "FFmpeg is a separate program invoked through command lines.",
               "FFmpeg source is published beside this distribution in ffmpeg-corresponding-source.zip.",
               "FFmpeg and other third-party components retain their own terms and copyright notices.", ""]

    def save(name: str, content: bytes) -> str:
        relative = "licenses/" + name
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        origins[relative] = "generated:licensing"
        return relative

    def texts(name: str, paths: list[Path]) -> list[str]:
        return [save(name + f"/{index:03d}-" + path.name, path.read_bytes())
                for index, path in enumerate(sorted(paths)) if path.is_file()]

    def add(name: str, version: str, license: str, files: list[str], licenses: list[str],
            ecosystem: str = "generic", kind: str = "library") -> None:
        if not files:
            raise ValueError(f"Shipped component has no files: {name}")
        entry = {"name": name, "version": version, "license": license, "ecosystem": ecosystem,
                 "type": kind, "notices": licenses,
                 "files": [{"path": path, "sha256": digest(root / path)} for path in sorted(set(files))]}
        entries.append(entry)
        notices.extend([f"{name} {version} — {license}", *licenses, ""])

    supplements: dict[str, list[str]] = {}
    archives = {item["name"]: download(item, cache, offline=offline) for item in inputs["inputs"]}
    for item in inputs["inputs"]:
        archive = archives[item.get("noticeFrom", item["name"])]
        extracted = ([(item["name"], base64.b64decode(b"".join(archive.read_bytes().split()), validate=True))]
                     if item.get("encoding") == "base64" else archive_notices(archive))
        if item.get("headerNotice"):
            extracted = [(name, data.split(b"*/", 1)[0] + b"*/\n") for name, data in extracted]
        if not extracted:
            raise ValueError(f"No upstream licence texts: {item['name']}")
        sections = []
        for name, data in sorted(extracted):
            sections.extend([("\n===== " + name + " =====\n").encode(), data])
        path = save("upstream/" + item["name"] + ".txt", b"\n".join(sections))
        supplements.setdefault(item["group"], []).append(path)
        notices.extend([f"{item['name']} {item['version']} — {item['license']}", item["url"], path, ""])

    original = {name: origin for name, origin in origins.items() if origin != "generated:licensing"}
    for name in ("python", "node", "ffmpeg", "pip"):
        files = [path for path, origin in original.items() if origin == "pinned input:" + name]
        if name == "python":
            files += [path for path, origin in original.items() if origin == "generated:embedded import paths"]
        item = pins[name]
        paths = ([root / "python/LICENSE.txt"] if name == "python" else
                 [root / "node/LICENSE"] if name == "node" else
                 [p for p in (root / "ffmpeg").rglob("*") if p.is_file() and license_file(p.name)]
                 if name == "ffmpeg" else [])
        copied = texts(name, paths)
        if name == "pip":
            wheel = root / files[0]
            copied = [save(f"pip/{index:03d}.txt", data)
                      for index, (_, data) in enumerate(archive_notices(wheel))]
        if name == "ffmpeg":
            copied += supplements.get("ffmpeg", []) + supplements.get("recipe", [])
        elif name == "python":
            copied += supplements.get("python", [])
        add(name, item["version"], item["license"], files, copied, kind="application")

    target = root / "python/Lib/site-packages"
    claimed: set[str] = set()
    for info in sorted(target.glob("*.dist-info")):
        dist = metadata.PathDistribution(info)
        license = sbom.resolve_license(cast(Mapping[str, str], dist.metadata))
        files = []
        for file in dist.files or []:
            path = (target / file).resolve()
            if not path.is_relative_to(target.resolve()):
                raise ValueError("Wheel RECORD points outside site-packages")
            relative = path.relative_to(root.resolve()).as_posix()
            if relative in original:
                files.append(relative)
        # Precompiled bytecode is derived from the owning distribution's source.
        source_files = set(files)
        for path in original:
            if "/__pycache__/" in path:
                source = re.sub(r"/__pycache__/([^/]+)\.cpython-312\.pyc$", r"/\1.py", path)
                if source in source_files:
                    files.append(path)
        copied = texts("python-packages/" + info.name, [p for p in info.rglob("*")
                                                       if p.is_file() and license_file(p.name)])
        add(dist.metadata["Name"], dist.version, license, files, copied, "pypi")
        claimed.update(files)
    unclaimed = {path for path in original if path.startswith("python/Lib/site-packages/")} - claimed
    if unclaimed:
        raise ValueError(f"Wheel files missing RECORD ownership: {sorted(unclaimed)}")

    npm_root = root / "app/motion-core"
    for package in sorted((npm_root / "node_modules").rglob("package.json")):
        info = json.loads(package.read_text(encoding="utf-8"))
        if not info.get("name") or not info.get("version"):
            continue
        folder = package.parent
        files = [p for p in original if p.startswith(folder.relative_to(root).as_posix() + "/")]
        copied = texts("npm/" + info["name"].replace("/", "-"),
                       [p for p in folder.iterdir() if p.is_file() and license_file(p.name)])
        if not copied and info["name"].startswith("@napi-rs/canvas-"):
            copied = texts("canvas-native", [npm_root / "node_modules/@napi-rs/canvas/LICENSE"])
        add(info["name"], info["version"], sbom._npm_license(info.get("license")), files, copied, "npm")
        native = [p for p in files if p.endswith((".node", ".dat"))]
        if native:
            if "canvasScan" in inputs:
                binary = next(root / p for p in native if p.endswith(".node"))
                if digest(binary) != inputs["canvasScan"]["nativeSha256"]:
                    raise ValueError("Canvas native binary does not match the notice scan")
                supplements.setdefault("canvas", []).append(save(
                    "canvas-scan.json", (json.dumps(inputs["canvasScan"], indent=2) + "\n").encode()))
            native_license = " AND ".join("(" + value + ")" for value in sorted(
                {"MIT", "BSD-3-Clause", *(item["license"] for item in inputs["inputs"] if item["group"] == "canvas")}))
            add("canvas-native", info["version"], native_license, native,
                copied + supplements.get("canvas", []))

    ui_files = [path for path, origin in original.items() if origin.startswith("generated:ui build")
                and Path(path).suffix in {".js", ".css"}]
    if ui is not None:
        lock = json.loads((ui / "package-lock.json").read_text(encoding="utf-8"))
        for location, info in sorted(lock["packages"].items()):
            if not location or info.get("dev") or not (ui / location).is_dir():
                continue
            package = json.loads((ui / location / "package.json").read_text(encoding="utf-8"))
            copied = texts("ui/" + package["name"].replace("/", "-"),
                           [p for p in (ui / location).iterdir() if p.is_file() and license_file(p.name)])
            add(package["name"], package["version"], sbom._npm_license(package.get("license")),
                ui_files, copied, "npm")

    font_files = []
    for font in sorted((root / "app/assets/fonts").rglob("*.ttf")):
        relative = font.relative_to(root).as_posix()
        files = [relative, *[name for name in original if name.startswith("app/ui/dist/")
                            and Path(name).suffix == ".ttf" and digest(root / name) == digest(font)]]
        ofl = (font.parent / "OFL.txt").relative_to(root).as_posix()
        if ofl in original:
            files.append(ofl)
        font_files.extend(files)
        copied = texts("fonts/" + font.parent.name, [font.parent / "OFL.txt"])
        add(font.stem, font_version(font), "OFL-1.1", files, copied)

    product = [path for path, origin in original.items() if (
        origin.startswith("exported path:") or origin.startswith("generated:ui build")
        or origin in {"generated:bundle runner", "generated:launcher"}
        or (origin == "generated:unchecked-hash bytecode" and path.startswith("app/")))]
    schema_files = [path for path in product if path.startswith("app/schema/")]
    product = [path for path in product if path not in font_files and path not in schema_files]
    package_inventory = "app/motion-core/node_modules/.package-lock.json"
    if package_inventory in original:
        product.append(package_inventory)
    copied = texts("product", [root / "LICENSE", root / "NOTICE", *list((root / "app/LICENSES").glob("*"))])
    version_file = root / "app/pipeline/version.py"
    version = re.search(r'__version__\s*=\s*["\']([^"\']+)', version_file.read_text(encoding="utf-8"))
    if version is None:
        raise ValueError("Product version is required")
    if schema_files:
        add("Cutgraph document schema", version[1], "Apache-2.0", schema_files,
            texts("schema", [root / "app/LICENSES/Apache-2.0.txt"]))
    add("Cutgraph Studio", version[1], "FSL-1.1-ALv2", product, copied, kind="application")
    if "Cutgraph Studio.exe" in original:
        add("Cutgraph Studio launcher", version[1], "FSL-1.1-ALv2", ["Cutgraph Studio.exe"], copied,
            kind="application")
    document = sbom.bundle_document(entries)
    validate_coverage(root, original, entries, document)
    save("inventory.json", (json.dumps({"components": entries}, indent=2) + "\n").encode())
    (root / SBOM_FILE).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    (root / NOTICE_FILE).write_text("\n".join(notices), encoding="utf-8")
    origins[SBOM_FILE] = origins[NOTICE_FILE] = "generated:licensing"
    return {"components": len(entries), "sha256": digest(root / SBOM_FILE)}


def refresh_launcher(root: Path, origins: dict[str, str]) -> None:
    """Preserve bundled-UI evidence when Setup replaces the portable entry point."""
    entries = json.loads((root / INVENTORY).read_text(encoding="utf-8"))["components"]
    for entry in entries:
        entry["files"] = [row for row in entry["files"] if row["path"] != "run-studio.cmd"]
    product = next(entry for entry in entries if entry["name"] == "Cutgraph Studio")
    entries.append({**product, "name": "Cutgraph Studio launcher", "files": [
        {"path": "Cutgraph Studio.exe", "sha256": digest(root / "Cutgraph Studio.exe")}]})
    entries = [entry for entry in entries if entry["files"]]
    document = sbom.bundle_document(entries)
    validate_coverage(root, origins, entries, document)
    (root / INVENTORY).write_text(json.dumps({"components": entries}, indent=2) + "\n", encoding="utf-8")
    (root / SBOM_FILE).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    with (root / NOTICE_FILE).open("a", encoding="utf-8") as stream:
        stream.write("\nCutgraph Studio launcher — FSL-1.1-ALv2\nSee licenses/product/.\n")
