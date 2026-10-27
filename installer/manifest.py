"""Fail-closed inventory of portable runtime files, independent of the builder."""
from __future__ import annotations

import hashlib
import json
from email.parser import Parser
from pathlib import Path, PurePosixPath
from collections.abc import Iterator, Mapping
import re
import stat
import unicodedata

from installer.build import is_source_path
from pipeline.runtime_bundle import BUNDLE_MANIFEST

MANIFEST = BUNDLE_MANIFEST
ROOTS = {"app", "python", "node", "ffmpeg", "LICENSES", "licenses", "addons"}
ROOT_FILES = {"LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.txt", "SBOM.cdx.json", "run-studio.cmd", "Cutgraph Studio.exe"}
FORBIDDEN = {
    ".git", ".venv", "venv", "virtualenv", ".cache", "model-cache", "model_cache", "models", "huggingface",
    "pyvenv.cfg", "id_rsa", "id_ed25519",
    "claude_agent_sdk", "mcp", "claude", "claude.exe",
    "@anthropic-ai", "claude-agent-sdk", "claude-code", "claude-code.exe", "claude_code", "claude_code_cli",
}


def bytecode_path(path: PurePosixPath) -> bool:
    return (path.parts[0] == "app" or path.parts[:3] == ("python", "Lib", "site-packages")) and bool(
        re.fullmatch(r".+/__pycache__/[^/]+\.cpython-312\.pyc", path.as_posix())
    )


def check_path(value: str) -> None:
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute() or path.as_posix() != value
            or unicodedata.normalize("NFKC", value) != value
            or any(p in {".", ".."} or any(c in '<>:"|?*' for c in p) or p.endswith((" ", "."))
                   or any(ord(c) < 32 or ord(c) == 127 for c in p)
                   or p.split(".")[0].upper() in {"CON", "CONIN$", "CONOUT$", "PRN", "AUX", "NUL", *(
                       f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10))}
                   for p in path.parts)):
        raise ValueError(f"Unsafe bundle path: {value}")
    for part in path.parts:
        lower = part.lower()
        distribution = re.sub(r"[-_.]+", "-", lower.split(".dist-info")[0])
        if (lower in FORBIDDEN or lower.startswith(("claude_agent_sdk-", "mcp-", ".env"))
                or re.sub(r"\.(?:exe|cmd|bat|ps1|com)$", "", lower) in {"claude", "claude-code", "claude_code"}
                or lower.endswith(".dist-info") and any(distribution == name or distribution.startswith(name + "-")
                    for name in ("claude-agent-sdk", "mcp", "claude-code", "claude-code-cli"))
                or lower.endswith(".pth") or lower.startswith("__editable__")
                or lower.endswith((".key", ".pfx", ".p12", ".pyo"))
                or (lower.endswith(".pyc") and not bytecode_path(path))):
            raise ValueError(f"Forbidden bundle path: {value}")
        if lower.endswith(".pem") and value != "python/Lib/site-packages/certifi/cacert.pem":
            raise ValueError(f"Unexpected key or certificate: {value}")
    if path.parts[0] not in ROOTS and value not in ROOT_FILES:
        raise ValueError(f"Unexpected bundle path: {value}")
    if path.parts[:4] == ("python", "Lib", "site-packages", "bin"):
        raise ValueError(f"Nonportable console launcher: {value}")


def check_origin(value: str, origin: str) -> None:
    """Accept only the source selection, pinned payload trees and declared build artifacts."""
    if origin.startswith("exported path:"):
        source = origin.removeprefix("exported path:")
        if is_source_path(source) and (value == "app/" + source or (
                value == source and (source in {"LICENSE", "NOTICE"} or source.startswith("LICENSES/")))):
            return
    pinned = {
        "pinned input:python": value.startswith("python/") and "/" not in value.removeprefix("python/"),
        "pinned input:pip": bool(re.fullmatch(r"addons/pip-[^/]+-py3-none-any\.whl", value)),
        "pinned input:node": value in {"node/node.exe", "node/LICENSE"},
        "pinned input:ffmpeg": value.startswith("ffmpeg/"),
        "pinned input:requirements-core.txt": value.startswith("python/Lib/site-packages/"),
        "pinned input:motion-core/package-lock.json": value.startswith("app/motion-core/node_modules/"),
    }
    generated = {
        "generated:launcher": value == "Cutgraph Studio.exe",
        "generated:embedded import paths": bool(re.fullmatch(r"python/python\d+\._pth", value)),
        "generated:ui build from tracked source and ui/package-lock.json": value.startswith("app/ui/dist/"),
        "generated:licensing": value in {"THIRD-PARTY-NOTICES.txt", "SBOM.cdx.json"} or value.startswith("licenses/"),
        "generated:bundle runner": value == "run-studio.cmd",
        "generated:unchecked-hash bytecode": bytecode_path(PurePosixPath(value)),
    }
    if pinned.get(origin, False) or generated.get(origin, False):
        return
    raise ValueError(f"Unregistered bundle file: {value}")


def reject_build_paths(path: Path, build_paths: tuple[Path, ...]) -> None:
    """Scan complete files, including binary tails, for native, JSON and UTF-16 paths."""
    needles = set()
    for root in build_paths:
        native = str(root.resolve())
        for value in (native, native.replace("\\", "/"), native.replace("\\", "\\\\")):
            needles.update((value.casefold().encode(), value.casefold().encode("utf-16-le")))
    if not needles:
        return
    overlap = max(map(len, needles)) - 1
    with path.open("rb") as stream:
        previous = b""
        while chunk := stream.read(1024 * 1024):
            data = (previous + chunk).lower()
            if any(needle in data for needle in needles):
                raise ValueError(f"Build output path embedded in bundle file: {path.name}")
            previous = data[-overlap:]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def regular_files(root: Path) -> Iterator[Path]:
    """Visit links before descending, never traversing a junction or symlink."""
    if root.is_symlink() or root.is_junction():
        raise ValueError("Bundle links are forbidden")
    for path in sorted(root.iterdir()):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Bundle links are forbidden: {path.name}")
        if stat.S_ISDIR(info.st_mode):
            yield path
            yield from regular_files(path)
        elif stat.S_ISREG(info.st_mode):
            yield path
        else:
            raise ValueError(f"Nonregular bundle file: {path.name}")


def inventory(root: Path, origins: Mapping[str, str], *, build_paths: tuple[Path, ...] = ()) -> list[dict]:
    """Reject unregistered files, links, private paths and missing expected files."""
    rows = []
    seen: set[str] = set()
    folded: set[str] = set()
    for path in sorted(regular_files(root)):
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST:
            continue
        check_path(relative)
        if path.is_dir():
            if not any(name.startswith(relative + "/") for name in origins):
                raise ValueError(f"Unregistered bundle directory: {relative}")
            continue
        if relative.casefold() in folded:
            raise ValueError(f"Case-colliding bundle path: {relative}")
        folded.add(relative.casefold())
        origin = origins.get(relative, "")
        check_origin(relative, origin)
        if path.name == "METADATA" and path.parent.name.lower().endswith(".dist-info"):
            name = Parser().parsestr(path.read_text(encoding="utf-8"), headersonly=True).get("Name", "")
            if re.sub(r"[-_.]+", "-", name).lower() in {"claude-agent-sdk", "mcp", "claude-code", "claude-code-cli"}:
                raise ValueError(f"Forbidden bundle distribution: {relative}")
        if "__pycache__" in path.parts:
            if origin != "generated:unchecked-hash bytecode" or not bytecode_path(PurePosixPath(relative)):
                raise ValueError(f"Unexpected bytecode: {relative}")
            with path.open("rb") as stream:
                if stream.read(8) != b"\xcb\r\r\n\x01\x00\x00\x00":
                    raise ValueError(f"Bytecode must use CPython 3.12 unchecked hashes: {relative}")
        reject_build_paths(path, build_paths)
        seen.add(relative)
        rows.append({"path": relative, "size": path.stat().st_size, "sha256": digest(path), "origin": origin})
    if missing := set(origins) - seen:
        raise ValueError(f"Missing bundle files: {sorted(missing)}")
    return rows


def write_manifest(root: Path, origins: Mapping[str, str], *, omitted: list[dict] | None = None,
                   source_revision: str | None = None, build_paths: tuple[Path, ...] = ()) -> dict:
    rows = inventory(root, origins, build_paths=build_paths)
    result = {"schemaVersion": 1, "files": rows, "size": sum(row["size"] for row in rows),
              "excludes": [MANIFEST], "omittedWheelMetadata": omitted or []}
    if source_revision is not None:
        result["sourceRevision"] = source_revision
    (root / MANIFEST).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def verify_manifest(root: Path) -> dict:
    """Verify the inventory, raising ValueError for malformed or inconsistent manifests."""
    if root.is_symlink() or root.is_junction():
        raise ValueError("Bundle links are forbidden")
    marker = root / MANIFEST
    if marker.is_symlink() or marker.is_junction():
        raise ValueError("Bundle links are forbidden")
    recorded = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(recorded, dict):
        raise ValueError("Malformed bundle manifest: expected an object")
    if recorded.get("schemaVersion") != 1:
        raise ValueError("Unsupported bundle manifest version")
    if (not isinstance(recorded.get("files"), list) or type(recorded.get("size")) is not int
            or any(not isinstance(row, dict)
                   or not all(isinstance(row.get(key), str) for key in ("path", "origin", "sha256"))
                   or type(row.get("size")) is not int for row in recorded["files"])):
        raise ValueError("Malformed bundle manifest inventory")
    origins = {row["path"]: row["origin"] for row in recorded["files"]}
    rows = inventory(root, origins)
    if rows != recorded["files"] or recorded["size"] != sum(row["size"] for row in rows):
        raise ValueError("Bundle contents differ from the manifest")
    return recorded
