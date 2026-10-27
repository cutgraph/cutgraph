"""Workspaces & a shared per-workspace asset library.

A workspace groups many videos and owns a shared media-bin asset library so
B-roll/photos/audio are imported once and reused. Layout mirrors the rest of the STATE_HOME
namespaces (`pipeline.jobs`, `pipeline.notes`): a durable JSON mirror per workspace, atomic
temp-sibling + os.replace writes, and an id validated against a closed charset before it ever
touches a path (path-traversal is rejected by the regex itself, not by best-effort escaping).

    STATE_HOME/workspaces/<id>/workspace.json   {id, name, createdAt, updatedAt, brand?: {name?, accent?}, archived?: true}
    STATE_HOME/workspaces/<id>/library/assets.json          {"assets": [...]}
    STATE_HOME/workspaces/<id>/library/<assetId><ext>       the imported file
    STATE_HOME/workspaces/<id>/library/thumbs/<assetId>.jpg generated for video/image only

Assets are content-addressed: `id` is the first 16 hex characters of the file's sha256, so
importing identical bytes twice is a no-op dedupe rather than a second copy. The `brand` workspace
is built-in and always resolvable; `unsorted` is a virtual grouping of videos with no workspaceId
and is never persisted here (composed by the caller from project.json documents instead — this
module owns the workspace/library namespace only, not the per-video project documents).

A workspace's `brand` is the eyebrow its videos' animated headlines carry: a name and an accent
colour. Only what the user set is stored; `brand_of` fills in missing values from config.

An archived client workspace is hidden from Home and the switcher with everything in it kept; only
an empty one can be deleted. The built-ins can be neither.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from pipeline import names, runtime_tools

BUILTIN_WORKSPACE_ID = "brand"
UNSORTED_WORKSPACE_ID = "unsorted"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
_ASSET_ID_RE = re.compile(r"^[a-f0-9]{16}$")
_MEDIA_FILE_RE = re.compile(r"^[a-f0-9]{16}\.[a-z0-9]{2,5}$")
_ASSET_KINDS = {"video", "image", "audio"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
_KIND_BY_EXT = {
    **{ext: "video" for ext in _VIDEO_EXTS},
    **{ext: "image" for ext in _IMAGE_EXTS},
    **{ext: "audio" for ext in _AUDIO_EXTS},
}
_THUMB_WIDTH = 480
BRAND_EYEBROW = config.BRAND_EYEBROW
BRAND_ACCENT = config.BRAND_ACCENT
BRAND_NAME_MAX = 40
_ACCENT_RE = re.compile(r"^#[0-9a-f]{6}$")
_MAX_TAGS = 32
_MAX_TAG_LEN = 64
_MAX_NAME_LEN = 200
_REPLACE_RETRY_DELAYS_S = (0.005, 0.01, 0.02, 0.05, 0.1)
_BUILT_IN_NAMES = {BUILTIN_WORKSPACE_ID: "Brand", UNSORTED_WORKSPACE_ID: "Unsorted"}

# Every write to a workspace.json or asset manifest holds its workspace's lock, so a read under the lock
# never meets a replace in flight (on Windows that read fails and looks like a missing file). Re-entrant:
# a brand write reads the brand through ensure_brand_workspace, which takes the same lock to create it.
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


class LibraryError(ValueError):
    """A caller-facing 400: bad id, bad name, unsupported file type, malformed tags."""


class DeleteRefused(Exception):
    """Why a workspace can't be deleted, for a 409: `error` in brief, `reason` for a person."""

    def __init__(self, error: str, reason: str) -> None:
        super().__init__(reason)
        self.error = error
        self.reason = reason


def _lock_for(workspace_id: str) -> threading.RLock:
    with _locks_guard:
        lock = _locks.get(workspace_id)
        if lock is None:
            lock = threading.RLock()
            _locks[workspace_id] = lock
        return lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_id(workspace_id: str, label: str = "workspace id") -> str:
    if names.existing_name(workspace_id) is None or not _ID_RE.fullmatch(workspace_id):
        raise LibraryError(f"invalid {label}: {workspace_id!r}")
    return workspace_id


def _validate_asset_id(asset_id: str) -> str:
    if names.safe_name(asset_id) is None or not _ASSET_ID_RE.fullmatch(asset_id):
        raise LibraryError(f"invalid asset id: {asset_id!r}")
    return asset_id


def _workspace_dir(workspace_id: str) -> Path:
    """The id charset forbids '.' and separators, and `exact_path` proves the resolved folder lies inside
    WORKSPACES_DIR."""
    _validate_id(workspace_id)
    return names.exact_path(config.WORKSPACES_DIR / workspace_id)


def _workspace_path(workspace_id: str) -> Path:
    return _workspace_dir(workspace_id) / "workspace.json"


def _library_dir(workspace_id: str) -> Path:
    return _workspace_dir(workspace_id) / "library"


def _assets_manifest_path(workspace_id: str) -> Path:
    return _library_dir(workspace_id) / "assets.json"


def _thumbs_dir(workspace_id: str) -> Path:
    return _library_dir(workspace_id) / "thumbs"


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write through a temp file of this write's own, then os.replace it over `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2) + "\n")
        # Windows refuses to replace a file another thread holds open for reading; wait it out.
        for delay in _REPLACE_RETRY_DELAYS_S:
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(delay)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _parse_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except PermissionError:
        raise
    except (OSError, json.JSONDecodeError):
        return None


def _read_json(path: Path) -> object | None:
    """The parsed file, or None when it is missing or malformed.

    Windows refuses a read while a replace or a scanner holds the file. That file exists, and reporting it
    missing would let a caller recreate it over the user's data, so wait it out like the write does.
    """
    for delay in _REPLACE_RETRY_DELAYS_S:
        try:
            return _parse_json(path)
        except PermissionError:
            time.sleep(delay)
    return _parse_json(path)


def _read_workspace_doc(workspace_id: str) -> dict | None:
    doc = _read_json(_workspace_path(workspace_id))
    return doc if isinstance(doc, dict) else None


def _write_workspace_doc(workspace_id: str, doc: dict) -> None:
    _atomic_write_json(_workspace_path(workspace_id), doc)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:40] or "workspace"


def ensure_brand_workspace() -> dict:
    """The built-in brand workspace always resolves; create it on first access."""
    doc = _read_workspace_doc(BUILTIN_WORKSPACE_ID)
    if doc is not None:
        return doc
    with _lock_for(BUILTIN_WORKSPACE_ID):
        doc = _read_workspace_doc(BUILTIN_WORKSPACE_ID)
        if doc is None:
            now = _now()
            doc = {"id": BUILTIN_WORKSPACE_ID, "name": "Brand", "createdAt": now, "updatedAt": now}
            _write_workspace_doc(BUILTIN_WORKSPACE_ID, doc)
    return doc


def get_workspace(workspace_id: str) -> dict | None:
    """Raises LibraryError for a malformed id; returns None for a well-formed but unknown one."""
    _validate_id(workspace_id)
    if workspace_id == BUILTIN_WORKSPACE_ID:
        return ensure_brand_workspace()
    return _read_workspace_doc(workspace_id)


def create_workspace(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise LibraryError("workspace name must not be empty")
    if len(name) > _MAX_NAME_LEN:
        raise LibraryError("workspace name is too long")
    with _lock_for("workspace-names"):
        _check_workspace_name(name)
        slug = _slugify(name)
        for _ in range(50):
            candidate = f"{slug}-{secrets.token_hex(3)}"
            names.validate_name(candidate)
            names.check_case_collision(config.WORKSPACES_DIR / candidate)
            if candidate in (BUILTIN_WORKSPACE_ID, UNSORTED_WORKSPACE_ID):
                continue
            with _lock_for(candidate):
                if _workspace_dir(candidate).exists():
                    continue
                now = _now()
                doc = {"id": candidate, "name": name, "createdAt": now, "updatedAt": now}
                _write_workspace_doc(candidate, doc)
                return doc
        raise LibraryError("could not allocate a workspace id")  # pragma: no cover - 2**24 suffixes



def rename_workspace(workspace_id: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise LibraryError("workspace name must not be empty")
    if len(name) > _MAX_NAME_LEN:
        raise LibraryError("workspace name is too long")
    with _lock_for("workspace-names"):
        _check_workspace_name(name, exclude=workspace_id)
        with _lock_for(_validate_id(workspace_id)):
            doc = get_workspace(workspace_id)
            if doc is None:
                raise FileNotFoundError(f"unknown workspace: {workspace_id}")
            doc = {**doc, "name": name, "updatedAt": _now()}
            _write_workspace_doc(workspace_id, doc)
        return doc



def _check_workspace_name(name: str, *, exclude: str | None = None) -> None:
    if not config.WORKSPACES_DIR.is_dir():
        return
    existing = []
    for directory in config.WORKSPACES_DIR.iterdir():
        if directory.is_dir() and directory.name != exclude:
            doc = _read_json(directory / "workspace.json")
            if isinstance(doc, dict) and isinstance(doc.get("name"), str):
                existing.append(doc["name"])
    names.check_display_collision(name, existing)


def brand_of(doc: dict) -> dict:
    """A workspace's explicit brand, with config defaults for missing values."""
    stored = doc.get("brand")
    name, accent = (stored.get("name"), stored.get("accent")) if isinstance(stored, dict) else (None, None)
    if not (isinstance(name, str) and name.strip()):
        name = BRAND_EYEBROW
    return {"name": name, "accent": accent if isinstance(accent, str) and _ACCENT_RE.fullmatch(accent) else BRAND_ACCENT}


def workspace_brand(workspace_id: str | None) -> dict:
    """The brand a video in `workspace_id` carries: its workspace's, or the brand kit's for an unsorted
    video or an unknown workspace. Reading writes nothing."""
    doc = None
    if isinstance(workspace_id, str) and workspace_id != UNSORTED_WORKSPACE_ID and _ID_RE.fullmatch(workspace_id):
        doc = _read_workspace_doc(workspace_id)
    return brand_of(doc or _read_workspace_doc(BUILTIN_WORKSPACE_ID) or {"id": BUILTIN_WORKSPACE_ID})


def set_brand(workspace_id: str, patch: dict) -> dict:
    """Set or reset a workspace's brand: `patch` names a `name` (at most BRAND_NAME_MAX characters)
    and/or an `accent` (#rrggbb); null or an empty string restores that field's default. Returns the
    workspace document; FileNotFoundError for an unknown workspace."""
    unknown = set(patch) - {"name", "accent"}
    if unknown:
        raise LibraryError(f"brand has unknown fields: {', '.join(sorted(unknown))}")
    with _lock_for(_validate_id(workspace_id)):
        doc = get_workspace(workspace_id)
        if doc is None:
            raise FileNotFoundError(f"unknown workspace: {workspace_id}")
        brand = dict(doc["brand"]) if isinstance(doc.get("brand"), dict) else {}
        for key, value in patch.items():
            if value is None or value == "":
                brand.pop(key, None)
            elif not isinstance(value, str):
                raise LibraryError(f"brand {key} must be a string")
            elif key == "name":
                clean = " ".join(value.split())
                if len(clean) > BRAND_NAME_MAX:
                    raise LibraryError(f"brand name is longer than {BRAND_NAME_MAX} characters")
                if clean:
                    brand[key] = clean
                else:
                    brand.pop(key, None)
            elif not _ACCENT_RE.fullmatch(value.lower()):
                raise LibraryError(f"brand accent must be a #rrggbb colour: {value!r}")
            else:
                brand[key] = value.lower()
        doc = {**{k: v for k, v in doc.items() if k != "brand"}, **({"brand": brand} if brand else {}), "updatedAt": _now()}
        _write_workspace_doc(workspace_id, doc)
    return doc


def set_archived(workspace_id: str, archived: bool) -> dict:
    """Archive a client workspace, or restore it with False. Only `archived: true` is stored. It is
    not an edit, so `updatedAt` (and with it Recently used) stays put. LibraryError for a built-in,
    FileNotFoundError for an unknown workspace."""
    if workspace_id in _BUILT_IN_NAMES:
        raise LibraryError(f"{_BUILT_IN_NAMES[workspace_id]} is built in and can't be archived")
    with _lock_for(_validate_id(workspace_id)):
        doc = get_workspace(workspace_id)
        if doc is None:
            raise FileNotFoundError(f"unknown workspace: {workspace_id}")
        doc = {**{k: v for k, v in doc.items() if k != "archived"}, **({"archived": True} if archived else {})}
        _write_workspace_doc(workspace_id, doc)
    return doc


def _counted(counts: dict[str, int]) -> str:
    parts = [f"{n} {word}{'' if n == 1 else 's'}" for word, n in counts.items() if n]
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def delete_workspace(workspace_id: str, *, videos: int, characters: int) -> None:
    """Delete an empty client workspace and its folder. Its videos and characters are counted by
    their owners (the video documents, pipeline.cast), so the caller passes those in; its library assets
    are counted here. DeleteRefused for a built-in or a workspace with anything in it, FileNotFoundError
    for an unknown one."""
    if workspace_id in _BUILT_IN_NAMES:
        raise DeleteRefused("built-in workspace", f"{_BUILT_IN_NAMES[workspace_id]} is built in and can't be deleted.")
    with _lock_for(_validate_id(workspace_id)):
        doc = get_workspace(workspace_id)
        if doc is None:
            raise FileNotFoundError(f"unknown workspace: {workspace_id}")
        contents = {"video": videos, "asset": len(_read_manifest(workspace_id)), "character": characters}
        if any(contents.values()):
            raise DeleteRefused(
                "workspace is not empty",
                f"“{doc.get('name') or workspace_id}” still has {_counted(contents)}. Move or delete them first, "
                "or archive the workspace instead.",
            )
        shutil.rmtree(_workspace_dir(workspace_id))


def _parsed_time(stamp: object) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def workspace_row(doc: dict, video_times: list[str]) -> dict:
    """A workspace as the workspaces list shows it: its document with `videoCount`, `archived` and
    `lastUsedAt`. `video_times` are its videos' last saves (the caller reads the video documents).
    `lastUsedAt` is the latest of those, the workspace's own edits and its library's last change: Home's
    Recently used order. Archiving writes no `updatedAt`, so it moves nothing there."""
    times = [t for t in map(_parsed_time, [doc.get("updatedAt"), *video_times]) if t is not None]
    if doc.get("id") != UNSORTED_WORKSPACE_ID:
        try:
            times.append(datetime.fromtimestamp(_assets_manifest_path(doc["id"]).stat().st_mtime, tz=timezone.utc))
        except (KeyError, LibraryError, OSError):
            pass
    last = max(times, default=None)
    return {
        **doc, "videoCount": len(video_times), "archived": doc.get("archived") is True,
        "lastUsedAt": None if last is None else last.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }


def _cover_asset_id(assets: list[dict]) -> str | None:
    return next((a["id"] for a in assets if a.get("kind") in ("video", "image")), None)


def list_workspaces() -> list[dict]:
    """Every persisted workspace (ensures brand exists) with its library asset counts.

    videoCount is per-video-document state the caller (studio_app) owns, and the virtual
    `unsorted` grouping isn't a real workspace, so both are merged in by the caller.
    """
    ensure_brand_workspace()
    out: list[dict] = []
    if config.WORKSPACES_DIR.exists():
        for entry in sorted(config.WORKSPACES_DIR.iterdir()):
            if not entry.is_dir():
                continue
            try:
                doc = get_workspace(entry.name)
            except LibraryError:
                continue
            if doc is None:
                continue
            assets = _read_manifest(entry.name)
            out.append({**doc, "assetCount": len(assets), "coverAssetId": _cover_asset_id(assets)})
    return out


# ── asset library ───────────────────────────────────────────────────────────────

def _read_manifest(workspace_id: str) -> list[dict]:
    doc = _read_json(_assets_manifest_path(workspace_id))
    assets = doc.get("assets") if isinstance(doc, dict) else None
    return assets if isinstance(assets, list) else []


def _write_manifest(workspace_id: str, assets: list[dict]) -> None:
    _atomic_write_json(_assets_manifest_path(workspace_id), {"assets": assets})


def _require_workspace(workspace_id: str) -> None:
    if get_workspace(workspace_id) is None:
        raise FileNotFoundError(f"unknown workspace: {workspace_id}")


def list_assets(workspace_id: str, *, kind: str | None = None) -> list[dict]:
    _require_workspace(workspace_id)
    if kind is not None and kind not in _ASSET_KINDS:
        raise LibraryError(f"invalid asset kind: {kind!r}")
    assets = _read_manifest(workspace_id)
    return [a for a in assets if kind is None or a.get("kind") == kind]


def get_asset(workspace_id: str, asset_id: str) -> dict | None:
    _require_workspace(workspace_id)
    _validate_asset_id(asset_id)
    return next((a for a in _read_manifest(workspace_id) if a.get("id") == asset_id), None)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_display_name(raw: str) -> str:
    name = Path(raw).name.strip()
    name = re.sub(r"[\r\n\t]+", " ", name)
    return name[:_MAX_NAME_LEN]


def _clean_tags(tags: object) -> list[str]:
    if not isinstance(tags, list):
        raise LibraryError("tags must be a list of strings")
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise LibraryError("tags must be a list of strings")
        value = tag.strip()[:_MAX_TAG_LEN]
        if value and value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned[:_MAX_TAGS]


def _log_ffmpeg(cmd: list[str]) -> None:
    with open(config.RUN_LOG, "a", encoding="utf-8") as fh:
        fh.write(" ".join(cmd) + "\n")


def _probe_media(path: Path, ffprobe: str) -> dict[str, object]:
    """One ffprobe call for duration + first video-stream dimensions; images resolve dimensions
    the same way (the image2 demuxer exposes a one-frame video stream), matching the technique
    scripts.studio_app already uses for project-asset metadata."""
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=width,height",
             "-of", "json", str(path)], check=True, capture_output=True, text=True,
        )
        raw = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {}
    out: dict[str, object] = {}
    fmt = raw.get("format") if isinstance(raw.get("format"), dict) else {}
    duration_raw = fmt.get("duration")
    if isinstance(duration_raw, (str, int, float)):
        try:
            duration = float(duration_raw)
        except ValueError:
            duration = -1.0
        if duration >= 0:
            out["durationS"] = round(duration, 3)
    streams = raw.get("streams")
    for stream in streams if isinstance(streams, list) else []:
        if isinstance(stream, dict) and stream.get("width") and stream.get("height"):
            out["width"] = int(stream["width"])
            out["height"] = int(stream["height"])
            break
    return out


def _kind_probe_fields(probe: dict[str, object], kind: str) -> dict[str, object]:
    out = dict(probe)
    if kind not in ("audio", "video"):
        out.pop("durationS", None)
    if kind not in ("video", "image"):
        out.pop("width", None)
        out.pop("height", None)
    return out


def _make_asset_thumb(workspace_id: str, source: Path, asset_id: str, kind: str, ffmpeg: str) -> None:
    """Best-effort JPEG thumb (first frame for video, a downscale for image); audio has none.
    A bad/corrupt source must not fail the import, only leave the thumb endpoint 404ing."""
    if kind == "audio":
        return
    dest = _thumbs_dir(workspace_id) / f"{asset_id}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if kind == "video":
        cmd += ["-ss", "0", "-i", str(source), "-frames:v", "1", "-vf", f"scale={_THUMB_WIDTH}:-2"]
    else:
        cmd += ["-i", str(source), "-frames:v", "1", "-vf", f"scale={_THUMB_WIDTH}:-2"]
    cmd.append(str(dest))
    _log_ffmpeg(cmd)
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        dest.unlink(missing_ok=True)


def import_asset(workspace_id: str, source: Path, *, display_name: str,
                  attribution: dict | None = None) -> tuple[dict, bool]:
    """Content-address `source` into the workspace library. Returns (asset, deduped).

    `source` is consumed: on success (new or duplicate) it no longer exists at its original path -
    either moved into the library or removed as a redundant copy. `attribution` (provider, author,
    authorUrl, sourceUrl, license) is stored on the record as-is when the asset is newly created; a
    dedupe hit keeps whatever attribution (if any) the first import recorded.
    """
    _require_workspace(workspace_id)
    source = Path(source)
    ext = Path(display_name).suffix.lower()
    kind = _KIND_BY_EXT.get(ext)
    if kind is None:
        source.unlink(missing_ok=True)
        raise LibraryError(f"unsupported asset type: {ext or '(none)'}")
    digest = _sha256_file(source)
    asset_id = digest[:16]
    with _lock_for(workspace_id):
        assets = _read_manifest(workspace_id)
        names.check_display_collision(display_name, [a["name"] for a in assets if isinstance(a.get("name"), str)])
        existing = next((a for a in assets if a.get("id") == asset_id), None)
        if existing is not None:
            source.unlink(missing_ok=True)
            return existing, True
        dest = _library_dir(workspace_id) / f"{asset_id}{ext}"
        names.check_case_collision(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, dest)
        ffmpeg = runtime_tools.tool_path("ffmpeg")
        ffprobe = runtime_tools.tool_path("ffprobe")
        probe = _kind_probe_fields(_probe_media(dest, ffprobe), kind)
        _make_asset_thumb(workspace_id, dest, asset_id, kind, ffmpeg)
        record = {
            "id": asset_id, "sha256": digest, "name": _safe_display_name(display_name) or asset_id,
            "kind": kind, "file": dest.name, "bytes": dest.stat().st_size, "tags": [],
            "createdAt": _now(), **probe,
        }
        if attribution is not None:
            record["attribution"] = attribution
        assets.append(record)
        _write_manifest(workspace_id, assets)
    return record, False


def update_asset(workspace_id: str, asset_id: str, *, name: str | None = None,
                  tags: list[str] | None = None) -> dict:
    _require_workspace(workspace_id)
    _validate_asset_id(asset_id)
    with _lock_for(workspace_id):
        assets = _read_manifest(workspace_id)
        for i, asset in enumerate(assets):
            if asset.get("id") != asset_id:
                continue
            updated = dict(asset)
            if name is not None:
                clean = _safe_display_name(name)
                if not clean:
                    raise LibraryError("asset name must not be empty")
                names.check_display_collision(clean, [a["name"] for a in assets
                                                       if a.get("id") != asset_id and isinstance(a.get("name"), str)])
                updated["name"] = clean
            if tags is not None:
                updated["tags"] = _clean_tags(tags)
            assets[i] = updated
            _write_manifest(workspace_id, assets)
            return updated
    raise FileNotFoundError(f"unknown asset: {asset_id}")


def delete_asset(workspace_id: str, asset_id: str) -> bool:
    _require_workspace(workspace_id)
    _validate_asset_id(asset_id)
    with _lock_for(workspace_id):
        assets = _read_manifest(workspace_id)
        remaining = [a for a in assets if a.get("id") != asset_id]
        if len(remaining) == len(assets):
            return False
        removed = next(a for a in assets if a.get("id") == asset_id)
        _write_manifest(workspace_id, remaining)
    file_name = removed.get("file")
    if isinstance(file_name, str):
        (_library_dir(workspace_id) / file_name).unlink(missing_ok=True)
    (_thumbs_dir(workspace_id) / f"{asset_id}.jpg").unlink(missing_ok=True)
    return True


def asset_file_path(workspace_id: str, asset_id: str) -> Path | None:
    asset = get_asset(workspace_id, asset_id)
    if asset is None or not isinstance(asset.get("file"), str):
        return None
    path = _library_dir(workspace_id) / asset["file"]
    return path if path.is_file() else None


def asset_thumb_path(workspace_id: str, asset_id: str) -> Path | None:
    _require_workspace(workspace_id)
    _validate_asset_id(asset_id)
    path = _thumbs_dir(workspace_id) / f"{asset_id}.jpg"
    return path if path.is_file() else None


def media_file(workspace_id: str, filename: str) -> Path | None:
    """The library file a project media ref or `/media/workspace/<id>/<file>` URL names, or None.

    Only a content-addressed `<assetId><ext>` sitting directly in the workspace's library dir
    resolves — never the manifest, a thumb, or anything a crafted name could reach."""
    _validate_id(workspace_id)
    if not _MEDIA_FILE_RE.fullmatch(filename) or Path(filename).suffix not in _KIND_BY_EXT:
        return None
    path = names.exact_path(_library_dir(workspace_id) / filename)
    return path if path.is_file() else None


def parse_media_ref(ref: str) -> tuple[str, str] | None:
    """Split a STATE_HOME-relative `workspaces/<id>/library/<file>` media.source into (id, file)."""
    parts = ref.split("/")
    if (len(parts) != 4 or parts[0] != "workspaces" or parts[2] != "library"
            or not _ID_RE.fullmatch(parts[1]) or not _MEDIA_FILE_RE.fullmatch(parts[3])):
        return None
    return parts[1], parts[3]
