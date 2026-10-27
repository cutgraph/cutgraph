"""Read-only render dependency discovery and content identity."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from pipeline import project as project_mod
from pipeline.jobs import CHUNK_SIZE, JobError


@dataclass(frozen=True)
class RenderInput:
    ref: str
    path: Path
    sha256: str
    size: int
    media_type: str


def canonical_project_bytes(doc: dict) -> bytes:
    """Validate and serialize the exact render document."""
    project_mod.validate(project_mod.from_dict(doc))
    return json.dumps(doc, sort_keys=True).encode("utf-8")


def _checked_path(path: Path) -> Path:
    raw = Path(os.path.abspath(os.fspath(path)))
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise JobError("PATH_ESCAPE", "artifact path contains a symlink or Windows reparse point")
    return raw.resolve()


def discover_inputs(doc: dict, state_root: Path) -> tuple[RenderInput, ...]:
    """Find and hash the same files the compiler can read under the state root."""
    root = Path(state_root).resolve()
    project = project_mod.from_dict(doc)
    project_mod.validate(project)
    paths: dict[str, tuple[Path, str]] = {}
    speech_sources: set[str] = set()
    for track in project.tracks:
        for clip in track.clips:
            ref = clip.media.source
            path = project_mod.resolve_media(root, clip.media, prefer_proxy=False)
            paths[ref] = (path, "image/*" if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
                          else "video/mp4")
            if track.kind == "speech":
                speech_sources.add(ref)

    if project.crop and project.crop.get("mode") == "reframe":
        explicit = project.crop.get("metaPath")
        if explicit:
            path = root / explicit
            if not path.is_file():
                raise JobError("MISSING_ARTIFACT", "project reframe metadata is unavailable")
            paths[explicit] = (path, "application/json")
        else:
            for ref in list(paths):
                candidate_ref = f"outputs/reframe/{Path(ref).stem}.reframe.json"
                candidate = root / candidate_ref
                if candidate.is_file():
                    paths[candidate_ref] = (candidate, "application/json")

    if project.captions and project.captions.get("enabled"):
        for ref in speech_sources:
            transcript_ref = f"outputs/transcripts/{Path(ref).stem}.words.json"
            transcript = root / transcript_ref
            if transcript.is_file():
                paths[transcript_ref] = (transcript, "application/json")

    inputs: list[RenderInput] = []
    for ref, (path, media_type) in sorted(paths.items()):
        try:
            checked = _checked_path(path)
        except OSError as exc:
            raise JobError("MISSING_ARTIFACT", "project dependency is unavailable") from exc
        if root != checked and root not in checked.parents or not checked.is_file():
            raise JobError("PATH_ESCAPE", "project dependency escaped the state root")
        if checked.relative_to(root).as_posix() != ref:
            raise JobError("PATH_ESCAPE", "project dependency escaped the state root")
        digest, size = hashlib.sha256(), 0
        with checked.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
        inputs.append(RenderInput(ref, checked, digest.hexdigest(), size, media_type))
    return tuple(inputs)


def inputs_digest(doc: dict, state_root: Path, render_spec: dict) -> str:
    """Bind a render to its document, options and dependency bytes without writes."""
    identity = {
        "project": canonical_project_bytes(doc).decode("utf-8"),
        "renderSpec": render_spec,
        "inputs": [(item.ref, item.sha256, item.size) for item in discover_inputs(doc, state_root)],
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
