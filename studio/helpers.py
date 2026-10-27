from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import time
from collections.abc import AsyncIterator, Collection, Iterator
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import (
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from studio.errors import JSONResponse

import config
from pipeline import project as project_mod
from pipeline import names
from studio import compute, state
from pipeline.runtime_tools import tool_path
from studio.errors import public_error
from pipeline.errors import ValidationError

THUMBS_DIR = config.OUTPUTS_DIR / "thumbs"


VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")


ASSET_KINDS = {
    **{ext: "video" for ext in VIDEO_EXTS},
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".aac": "audio", ".flac": "audio", ".ogg": "audio",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image",
}


class BodyReadError(HTTPException):
    pass


def content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        length = int(raw)
        if length < 0:
            raise ValueError
    except ValueError as e:
        raise BodyReadError(400, "invalid content-length") from e
    return length


def check_body_limit(request: Request, limit: int, message: str = "request body exceeds the size limit") -> int | None:
    length = content_length(request)
    if length is not None and length > limit:
        raise BodyReadError(413, message)
    return length


async def limited_stream(
    request: Request, limit: int, message: str = "request body exceeds the size limit",
) -> AsyncIterator[bytes]:
    check_body_limit(request, limit, message)
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise BodyReadError(413, message)
        yield chunk


def cleanup_stale_uploads() -> None:
    cutoff = time.time() - 3600
    for root, pattern in ((config.WORKSPACES_DIR, ".upload-*"), (config.DATA_DIR, ".*.part")):
        for path in root.glob(pattern):
            try:
                if path.stat().st_mtime >= cutoff or path.is_symlink():
                    continue
                if getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                if root == config.WORKSPACES_DIR and path.is_dir():
                    shutil.rmtree(path)
                elif root == config.DATA_DIR and path.is_file():
                    path.unlink()
            except OSError:
                # Another process may still hold an abandoned upload open on Windows.
                continue


async def read_json(request: Request, limit: int | None = None) -> Any:
    body = bytearray()
    async for chunk in limited_stream(request, config.MAX_JSON_BODY_BYTES if limit is None else limit):
        body.extend(chunk)
    try:
        return json.loads(body or b"{}")
    except UnicodeDecodeError as e:
        raise json.JSONDecodeError("invalid JSON encoding", "", 0) from e


def _safe(name: str) -> str | None:
    """A slug/clip_id is only ever used inside a file path — reject anything that isn't one."""
    return names.project_name(name) or names.source_name(name)


def _safe_asset_filename(raw: str) -> str | None:
    """Filename-only asset import: sanitize the display name and allow only supported media types."""
    return names.existing_filename(raw, ASSET_KINDS)


def _source(slug: str) -> Path:
    """The source video for a slug — uploads may be any video type, so resolve the real one."""
    entries = list(config.DATA_DIR.iterdir()) if config.DATA_DIR.is_dir() else []
    for ext in VIDEO_EXTS:
        for entry in entries:
            if entry.stem == slug and entry.suffix.lower() == ext:
                return names.exact_path(entry, config.DATA_DIR)
    if any(entry.stem.casefold() == slug.casefold() and entry.suffix.lower() in VIDEO_EXTS for entry in entries):
        raise names.NameNotFound("The requested item was not found.")
    return config.DATA_DIR / f"{slug}.mp4"


def _source_exact(filename: str) -> Path:
    """Resolve one exact uploaded source filename; never fall through to another extension."""
    return names.exact_path(config.DATA_DIR / filename, config.DATA_DIR)


def _existing_under(path: Path, root: Path, suffixes: Collection[str]) -> Path | None:
    """Return a real file only when its resolved target stays under the serving root."""
    try:
        resolved = path.resolve(strict=True)
        root_resolved = root.resolve()
    except (OSError, RuntimeError):
        return None
    if root_resolved not in resolved.parents or not resolved.is_file() or resolved.suffix.lower() not in suffixes:
        return None
    try:
        names.exact_path(path, root, resolved=resolved)
    except names.NameNotFound:
        return None
    return resolved


def _make_thumb(slug: str) -> Path | None:
    src = _source(slug)
    if not src.exists():
        return None
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMBS_DIR / f"{slug}.jpg"
    if dest.exists():
        return dest
    base = [tool_path("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", str(src), "-frames:v", "1"]
    tonemap = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
               "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale=480:-2")
    try:
        compute._log_ffmpeg(base + ["-vf", tonemap, str(dest)])
        subprocess.run(base + ["-vf", tonemap, str(dest)], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        dest.unlink(missing_ok=True)
        compute._log_ffmpeg(base + ["-vf", "scale=480:-2", str(dest)])
        subprocess.run(base + ["-vf", "scale=480:-2", str(dest)], check=True, capture_output=True, text=True)
    return dest


# ── editor document persistence ───────────────────────────────────────────────


def _timeline_hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()


# ── response helpers ───────────────────────────────────────────────────────────
def range_response(path: Path, request: Request, ctype: str = "video/mp4") -> Response:
    """Range-aware streaming so the browser can seek into long videos without buffering all of it.

    Byte-for-byte the semantics of the old stdlib `_serve_media`: suffix ranges (empty start ⇒ last
    N bytes), open-ended (`start-`), end clamped to size-1, 416 + `Content-Range: bytes */<size>`
    on unsatisfiable, 206 + `Content-Range`/`Content-Length` when ranged else 200, always
    `Accept-Ranges: bytes`, 64 KB chunks, swallowing client-disconnect errors. NOT FileResponse —
    its Range handling differs and would break <video> seek parity.
    """
    if not names.exact_path(path).is_file():
        return PlainTextResponse("not found", status_code=404)
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng and rng.startswith("bytes="):
        try:
            s, _, e = rng[6:].partition("-")
            if s == "":
                start = max(0, size - int(e))
            else:
                start = int(s)
                end = int(e) if e else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                raise ValueError
            status = 206
        except (ValueError, TypeError):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    def _body() -> Iterator[bytes]:
        try:
            with path.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client seeked away / closed mid-stream — normal for <video>

    return StreamingResponse(_body(), status_code=status, media_type=ctype, headers=headers)


def _project_path(project: str) -> Path:
    return config.PROJECTS_DIR / f"{project}.json"


def _project_digest(doc: dict) -> str:
    return hashlib.sha256(
        json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _skeleton(project: str) -> project_mod.Project:
    """A fresh revision-0 project so the UI can start a brand-new slug (GET/PATCH share this shape)."""
    return project_mod.Project(name=project, fps=config.DEFAULT_FPS, revision=0, tracks=[])


def _source_duration_frames(source: Path) -> int:
    """Return a source's complete duration in the editor's fixed frame clock.

    A new project must be immediately editable, not an empty skeleton awaiting a later
    pipeline stage. Keep the probe at the HTTP boundary; the project model stays pure.
    """
    probe = subprocess.run(
        [tool_path("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
        check=True, capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    if duration <= 0:
        raise ValueError("source has no usable duration")
    return max(1, round(duration * config.DEFAULT_FPS))


def _source_display_size(source: Path) -> tuple[int, int]:
    """The source's DISPLAY width/height: its coded stream size, swapped when a ±90°/270° rotation
    means the frame actually plays back on its side.

    Phones commonly code a portrait recording as 1920x1080 with a rotation carried in a Display
    Matrix side_data entry (the modern form) or, on older files, a `rotate` stream tag — the coded
    size alone would silently make a portrait recording a landscape project. Any probe failure (no
    video stream, corrupt file, ffprobe missing) falls back to 1920x1080, matching the previous
    unconditional default.
    """
    try:
        probe = subprocess.run(
            [tool_path("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(source)],
            check=True, capture_output=True, text=True,
        )
        raw = json.loads(probe.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return 1920, 1080
    streams = raw.get("streams") if isinstance(raw, dict) else None
    stream = streams[0] if isinstance(streams, list) and streams else None
    if not isinstance(stream, dict):
        return 1920, 1080
    width, height = stream.get("width"), stream.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or isinstance(width, bool) or width <= 0 or height <= 0:
        return 1920, 1080
    rotation = 0
    for entry in stream.get("side_data_list") or []:
        if isinstance(entry, dict) and isinstance(entry.get("rotation"), (int, float)):
            rotation = int(entry["rotation"])
            break
    if rotation == 0:
        tag = (stream.get("tags") or {}).get("rotate") if isinstance(stream.get("tags"), dict) else None
        try:
            rotation = int(tag) if tag is not None else 0
        except (TypeError, ValueError):
            rotation = 0
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return width, height


def _project_dimensions_for_source(source: Path) -> tuple[int, int]:
    """The new project's canvas, matching the source's display orientation rather than always
    defaulting to landscape: landscape -> 1920x1080, portrait -> 1080x1920 (no crop), square ->
    1080x1080."""
    width, height = _source_display_size(source)
    if width == height:
        return 1080, 1080
    if width > height:
        return 1920, 1080
    return 1080, 1920


def _project_from_source(project: str, source: Path, title: str | None = None) -> project_mod.Project:
    """Create the first editable timeline from a verified source video."""
    duration_f = compute._gpu_run(_source_duration_frames, source)
    width, height = compute._gpu_run(_project_dimensions_for_source, source)
    source_ref = source.relative_to(config.STATE_HOME).as_posix()
    return project_mod.Project(
        name=project,
        title=title,
        fps=config.DEFAULT_FPS,
        width=width,
        height=height,
        revision=1,
        tracks=[project_mod.Track(
            id="speech",
            kind="speech",
            clips=[project_mod.Clip(
                id="source",
                media=project_mod.Media(source=source_ref),
                srcInF=0,
                srcOutF=duration_f,
                tlStartF=0,
            )],
        )],
    )


def _read_project_doc(project: str) -> tuple[dict | None, int]:
    """Single on-disk read per request: return the raw doc plus the revision authority derived
    from that SAME read (`doc["revision"]`, default 0). Missing file ⇒ (None, 0). GET/PUT/PATCH all
    go through this so the gate and the served body can never disagree from two divergent parses."""
    path = names.exact_path(_project_path(project), config.PROJECTS_DIR)
    if not path.exists():
        return None, 0
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc, int(doc.get("revision", 0))


def _commit_project(project: str, proj: project_mod.Project, on_disk: int) -> Response:
    """Write path shared by PUT/PATCH: force revision/name, validate, atomic write, broadcast.

    `name` is forced to the safe slug (it flows into FINAL_DIR/<name>.mp4 — traversal defense) and
    the doc's own `revision` is untrusted (baseRevision is the concurrency token, already checked).
    """
    proj.revision = on_disk + 1
    proj.name = project
    if not _project_path(project).exists():
        if names.project_name(project, creating=True) is None:
            names.validate_name(project)
        names.check_case_collision(_project_path(project))
    try:
        project_mod.validate(proj)
    except ValidationError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    with project_mod.write_lock():
        project_mod.write_project(_project_path(project), proj)
    state.hub.publish(project, proj.revision)
    return JSONResponse({"project": project_mod.to_dict(proj), "revision": proj.revision})


async def _write_prelude(
    p: str, request: Request, field: str, field_type: type, missing_msg: str,
) -> Response | tuple[str, object, dict | None, int]:
    """The gate prelude shared by PUT and PATCH: safe-slug, JSON body, the payload field +
    an int-not-bool baseRevision, the single on-disk read, and the stale-revision 409. Returns an
    early Response on any rejection, else (project, field_value, on_disk_doc, on_disk_revision)."""
    project = _safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=400)
    try:
        body = await read_json(request, config.MAX_PROJECT_BODY_BYTES if field in ("project", "ops") else None)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    value, base_revision = body.get(field), body.get("baseRevision")
    if not isinstance(value, field_type) or not isinstance(base_revision, int) or isinstance(base_revision, bool):
        return JSONResponse({"error": missing_msg}, status_code=400)
    try:
        doc, on_disk = _read_project_doc(project)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    if base_revision != on_disk:
        return JSONResponse({"currentRevision": on_disk}, status_code=409)
    return project, value, doc, on_disk


# ── routes: GET ────────────────────────────────────────────────────────────────
def _speech_source(doc: dict) -> str | None:
    """The source media of a project's first speech clip — the bridge to its `<slug>.words.json`."""
    for track in doc.get("tracks", []):
        if track.get("kind") == "speech":
            for clip in track.get("clips") or []:
                src = (clip.get("media") or {}).get("source")
                if isinstance(src, str):
                    return src
    return None


def _speech_proxy(doc: dict) -> str | None:
    """The proxy media of a project's first speech clip, once the new-video flow has set one."""
    for track in doc.get("tracks", []):
        if track.get("kind") == "speech":
            for clip in track.get("clips") or []:
                proxy = (clip.get("media") or {}).get("proxy")
                if isinstance(proxy, str) and proxy:
                    return proxy
    return None


def _proxy_lifecycle(project: str) -> dict:
    """The durable, project-scoped proxy readiness used by the new-video flow to avoid polling
    blindly: the persisted doc (already set) wins, else an encode `_spawn_proxy` still runs for this
    project is pending (its output file exists long before it is playable), else an on-disk
    `<slug>_540.mp4` (set by a prior run that never got PATCHed back) is ready."""
    doc, _revision = _read_project_doc(project)
    if doc is None:
        return {"state": "none", "proxy": None}
    existing = _speech_proxy(doc)
    if existing:
        return {"state": "ready", "proxy": existing}
    source_slug = compute._project_compute_source_slug(project)
    if not source_slug:
        return {"state": "none", "proxy": None}
    with state._BACKGROUND_LOCK:
        pending = any(project in waiters for waiters in state._PROXY_WAITERS.values())
    if pending:
        return {"state": "pending", "proxy": None}
    dest = names.exact_path(config.PROXIES_DIR / f"{source_slug}_540.mp4", config.PROXIES_DIR)
    if dest.exists():
        return {"state": "ready", "proxy": dest.relative_to(config.STATE_HOME).as_posix()}
    return {"state": "none", "proxy": None}


def _transcript_slug(s: str) -> str:
    """Resolve a route param to the words-file slug: a project ⇒ its speech source stem, else `s`.

    `<p>` is a project slug; a project reaches its transcript through its speech clip media.source
    (the words file is keyed by source stem, shared across projects). Raw-slug callers (no project
    file of that name) fall through to `s` itself for back-compat.
    """
    proj_path = names.exact_path(config.PROJECTS_DIR / f"{s}.json", config.PROJECTS_DIR)
    if proj_path.exists():
        try:
            doc = json.loads(proj_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return s
        source = _speech_source(doc)
        if source:
            return Path(source).stem
    return s


def _reshape_transcript(raw: dict) -> dict:
    """Flatten the nested on-disk TranscriptResult into the panel contract: a flat word list with a
    synthesized `segIdx` (segment enumeration), `word`→`w` with WhisperX's leading space stripped
    (mirrors caption_burn), plus lightweight segments for paragraphing. Times stay float seconds.

    Verbatim fillers ride a SEPARATE `fillers` array `[{w,start,end,segIdx}]` served alongside `words`
  : the flat `words` list is the `wordIdx` contract and must never carry a filler, so the
    panel interleaves fillers by time for DISPLAY (synthetic keys) and derives cut proposals from their
    start/end times — it never mints an index into the word space."""
    words: list[dict] = []
    segments: list[dict] = []
    for seg_idx, seg in enumerate(raw.get("segments", [])):
        segments.append({
            "segIdx": seg_idx,
            "start": seg.get("start"),
            "end": seg.get("end"),
            "text": seg.get("text", ""),
        })
        for w in seg.get("words", []):
            words.append({
                "w": (w.get("word") or "").strip(),
                "start": w.get("start"),
                "end": w.get("end"),
                "segIdx": seg_idx,
            })
    fillers = [
        {"w": (f.get("word") or "").strip(), "start": f.get("start"),
         "end": f.get("end"), "segIdx": f.get("segIdx")}
        for f in raw.get("fillers", [])
    ]
    return {
        "language": raw.get("language", ""),
        "duration": raw.get("duration", 0.0),
        "words": words,
        "segments": segments,
        "fillers": fillers,
    }


# ── workspaces & shared asset library ──────────────────────────


# ── the cast: character profiles and their custom expressions ────────
async def _json_body(request: Request) -> dict | None:
    try:
        body = await read_json(request)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None
