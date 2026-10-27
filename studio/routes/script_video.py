from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import jobs as jobs_mod
from pipeline import library as library_mod
from pipeline import project as project_mod
from pipeline import script_video as script_video_mod
from pipeline import timebase
from pipeline import names
from pipeline import transcribe as transcribe_stage
from studio import compute, helpers, state
from studio.errors import public_error
from pipeline.errors import ValidationError

router = APIRouter()


# ── script-origin videos ─────────────────────────────────────────────
# A script video is an ordinary project over a generated silent source whose words sidecar is the
# script itself (pipeline/script_video.py). A narration take is uploaded and transcribed through the
# normal job path, then aligned onto the script words in one revision-gated write.
TAKE_EXTS = (*helpers.VIDEO_EXTS, ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")


MAX_SCRIPT_CHARS = 100_000


def _source_stem_taken(stem: str) -> bool:
    try:
        names.check_case_collision(config.DATA_DIR / f"{stem}.mp4", source_stem=True)
    except names.NameConflict:
        return True
    return (
        any((config.DATA_DIR / f"{stem}{ext}").exists() for ext in helpers.VIDEO_EXTS)
        or (config.TRANSCRIPTS_DIR / f"{stem}.words.json").exists()
        or (config.TRANSCRIPTS_DIR / f"{stem}.whisperx.json").exists()
    )


def _script_video_slug(title: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_")[:48] or "script_video"
    if names.reserved_name(base):
        base = f"{base}_video"
    names.validate_name(base)
    slug, n = base, 1
    while _script_slug_taken(slug):
        n += 1
        slug = f"{base}_{n}"
    names.validate_name(slug)
    names.check_case_collision(helpers._project_path(slug))
    return slug


def _script_slug_taken(slug: str) -> bool:
    try:
        names.check_case_collision(helpers._project_path(slug))
        names.check_case_collision(config.DATA_DIR / f"{slug}_script.mp4", source_stem=True)
    except names.NameConflict:
        return True
    return helpers._project_path(slug).exists() or _source_stem_taken(f"{slug}_script")


def _create_script_video(
    title: str, scenes: list[script_video_mod.ScriptScene], workspace_id: str | None,
) -> tuple[project_mod.Project, Path]:
    """Off-loop body of `api_create_script_video`. The create lock spans the slug choice and every
    write, so two tabs creating the same title get two videos."""
    with state._PROJECT_CREATE_LOCK:
        slug = _script_video_slug(title)
        source = config.DATA_DIR / f"{slug}_script.mp4"
        transcript = script_video_mod.script_transcript(scenes)
        frames = max(1, timebase.sec_to_frame(transcript.duration, config.DEFAULT_FPS))
        proj = script_video_mod.build_project(
            slug, scenes, source.relative_to(config.STATE_HOME).as_posix(), frames, workspace_id,
            title=title,
        )
        project_mod.validate(proj)
        try:
            script_video_mod.make_silent_source(source, frames, proj.fps, proj.width, proj.height)
            transcribe_stage._write_outputs(transcript, source.stem)  # noqa: SLF001 - the transcription sidecar format
            with project_mod.write_lock():
                project_mod.write_project(helpers._project_path(slug), proj)
        except Exception:
            source.unlink(missing_ok=True)
            for suffix in (".words.json", ".srt", ".txt"):
                (config.TRANSCRIPTS_DIR / f"{source.stem}{suffix}").unlink(missing_ok=True)
            raise
    return proj, source


@router.post("/api/script-video")
async def api_create_script_video(request: Request) -> Response:
    """Create a video from a written script (`{title, script, workspaceId?}`): an animatic that plays
    at estimated timing until a narration take is imported."""
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    title, script, workspace_id = body.get("title"), body.get("script"), body.get("workspaceId")
    if not isinstance(title, str) or not title.strip() or len(title) > project_mod.SCENE_TITLE_MAX:
        return JSONResponse({"error": "a title of 1-120 characters is required"}, status_code=400)
    if not isinstance(script, str) or len(script) > MAX_SCRIPT_CHARS:
        return JSONResponse({"error": "the script must be text of at most 100,000 characters"}, status_code=400)
    scenes = script_video_mod.parse_script(script)
    if not scenes:
        return JSONResponse({"error": "Write at least one line of script."}, status_code=400)
    if workspace_id in (None, "", library_mod.UNSORTED_WORKSPACE_ID):
        workspace_id = None
    else:
        try:
            workspace = library_mod.get_workspace(str(workspace_id))
        except ValueError as e:
            return JSONResponse({"error": public_error(e, 400)}, status_code=400)
        if workspace is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)
        workspace_id = str(workspace_id)
    try:
        proj, source = await asyncio.to_thread(_create_script_video, title.strip(), scenes, workspace_id)
    except names.NameConflict:
        raise
    except ValidationError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    compute._spawn_proxy(source, project=proj.name)
    return JSONResponse(
        {"project": project_mod.to_dict(proj), "revision": proj.revision, "video": proj.name}, status_code=201,
    )


@router.post("/api/script/draft")
async def api_draft_script(request: Request) -> Response:
    """"From an idea" → "✦ Draft it": the co-pilot's one-shot `{title, script, source}` for an
    idea, validated by the same parser `/api/script-video` uses to create the video. Offline-safe: a
    friendly `error` on anything short of a clean draft, never a canned script standing in for one."""
    body = await helpers._json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    from scripts import (
        agent_host,  # lazy: agent_host itself imports the SDK lazily
    )

    try:
        draft = await agent_host.draft_script(body.get("idea"), body.get("format"))
    except agent_host.ScriptDraftError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except agent_host.ScriptDraftUnavailable as e:
        return JSONResponse({"error": public_error(e, 503)}, status_code=503)
    return JSONResponse(draft)


def _script_takes(project: str) -> list[str]:
    """The narration take slugs uploaded for a script video, oldest first."""
    pattern = re.compile(rf"^{re.escape(project)}_take([0-9]+)$")
    found: list[tuple[int, str]] = []
    if config.DATA_DIR.exists():
        for path in config.DATA_DIR.iterdir():
            match = pattern.match(path.stem)
            if match and path.suffix.lower() in helpers.VIDEO_EXTS:
                found.append((int(match.group(1)), path.stem))
    return [stem for _n, stem in sorted(found)]


def _store_take(project: str, upload: Path, ext: str, doc: dict) -> Path:
    """Off-loop: keep a video take as uploaded; turn one with no picture into an MP4 over black. The
    create lock reserves the take slug until its file exists."""
    picture = script_video_mod.take_has_picture(upload)
    with state._PROJECT_CREATE_LOCK:
        n = 1
        while _source_stem_taken(f"{project}_take{n}"):
            n += 1
        take = f"{project}_take{n}"
        if names.source_name(take) is None:
            raise ValidationError("invalid narration take name")
        if picture and ext in helpers.VIDEO_EXTS:
            dest = config.DATA_DIR / f"{take}{ext}"
            os.replace(upload, dest)
            return dest
        dest = config.DATA_DIR / f"{take}.mp4"
        try:
            script_video_mod.make_take_source(
                upload, dest, int(doc.get("fps") or config.DEFAULT_FPS),
                int(doc.get("width") or 1920), int(doc.get("height") or 1080),
            )
        except Exception:
            dest.unlink(missing_ok=True)
            raise
    upload.unlink(missing_ok=True)
    return dest


def _take_status(project: str, take: str, doc: dict) -> dict:
    speech = helpers._speech_source(doc)
    if speech and Path(speech).stem == take:
        return {"take": take, "state": "applied"}
    if any(names.exact_path(config.TRANSCRIPTS_DIR / f"{take}{suffix}", config.TRANSCRIPTS_DIR).exists()
           for suffix in (".whisperx.json", ".words.json")):
        return {"take": take, "state": "ready"}
    jobs = [
        job for job in jobs_mod.all_persisted().values()
        if job.get("project") == project and job.get("kind") == "transcribe" and compute._job_source_slug(job) == take
    ]
    latest = max(jobs, key=lambda job: str(job.get("createdAt") or ""), default=None)
    if latest is None:
        return {"take": take, "state": "not_started"}
    status = str(latest.get("status") or "")
    if status in {"queued", "retrying", "fallback"}:
        return {"take": take, "state": "queued"}
    if status in {"running", "publishing"}:
        return {"take": take, "state": "running"}
    return {"take": take, "state": "failed", "message": compute._job_presentation(latest).get("error", "Transcription didn't finish.")}


def _script_doc(project: str | None) -> dict | None:
    if not project:
        return None
    try:
        doc, _revision = helpers._read_project_doc(project)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("narration") else None


@router.get("/api/script-video/{p}/narration")
def api_script_narration(p: str, take: str = "") -> Response:
    """Where a script video's narration take stands (`?take=`, else its latest): not_started, queued,
    running, failed, ready (transcribed) or applied."""
    project = helpers._safe(p)
    doc = _script_doc(project)
    if not project or doc is None:
        return JSONResponse({"error": "not a script video"}, status_code=404)
    takes = _script_takes(project)
    slug = take or (takes[-1] if takes else "")
    if not slug:
        return JSONResponse({"take": None, "state": "none"})
    if slug not in takes:
        return JSONResponse({"error": "unknown take"}, status_code=404)
    return JSONResponse(_take_status(project, slug, doc))


@router.put("/api/script-video/{p}/narration/{filename}")
async def api_script_narration_upload(p: str, filename: str, request: Request) -> Response:
    """Upload a narration take (audio or video) for a script video and queue its WhisperX transcription."""
    project = helpers._safe(p)
    ext = Path(unquote(filename)).suffix.lower()
    doc = _script_doc(project)
    if not project or doc is None:
        return JSONResponse({"error": "not a script video"}, status_code=404)
    if ext not in TAKE_EXTS:
        return JSONResponse({"error": "choose an audio or video file"}, status_code=400)
    if state._JOBS.get(project, {}).get("status") in ("queued", "running"):
        return JSONResponse({"error": "a narration take is already being transcribed"}, status_code=409)
    message = "the take is larger than 2 GB"
    length = helpers.check_body_limit(request, config.MAX_NARRATION_UPLOAD_BYTES, message)
    if length == 0:
        return JSONResponse({"error": "empty upload"}, status_code=400)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.DATA_DIR / f".{project}_take-{uuid.uuid4().hex}{ext}.part"
    try:
        written = 0
        with tmp.open("xb") as fh:
            async for chunk in helpers.limited_stream(request, config.MAX_NARRATION_UPLOAD_BYTES, message):
                fh.write(chunk)
                written += len(chunk)
        if not written:
            raise helpers.BodyReadError(400, "empty upload")
        if length is not None and written < length:
            raise OSError("client disconnected before the upload finished")
        take_path = await asyncio.to_thread(_store_take, project, tmp, ext, doc)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except (OSError, subprocess.CalledProcessError) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    finally:
        tmp.unlink(missing_ok=True)
    compute._spawn_proxy(take_path, project=project)
    return JSONResponse(
        {"take": take_path.stem, "transcription": compute._enqueue_transcribe(take_path.stem, project=project)},
        status_code=201,
    )


@router.post("/api/script-video/{p}/narration/{take}/apply")
async def api_script_narration_apply(p: str, take: str, request: Request) -> Response:
    """Narrate a script video with its transcribed take (`{baseRevision}`): the script words take the
    take's timings, the speech track moves onto it and the video is marked recorded, in one
    revision-gated write. The take's raw transcript is kept beside it as `<take>.whisperx.json`."""
    prelude = await helpers._write_prelude(p, request, "baseRevision", int, "missing baseRevision")
    if isinstance(prelude, Response):
        return prelude
    project, _base, doc, on_disk = prelude
    take_slug = names.source_name(take) if re.fullmatch(rf"{re.escape(project)}_take[1-9][0-9]*", take) else None
    if doc is None or not doc.get("narration"):
        return JSONResponse({"error": "not a script video"}, status_code=404)
    if not take_slug or take_slug not in _script_takes(project):
        return JSONResponse({"error": "unknown take"}, status_code=404)
    speech = helpers._speech_source(doc)
    if speech and Path(speech).stem == take_slug:
        return JSONResponse({"project": project_mod.to_dict(project_mod.from_dict(doc)), "revision": on_disk,
                             "alignment": None})
    words_dir = config.TRANSCRIPTS_DIR
    raw = names.exact_path(words_dir / f"{take_slug}.whisperx.json", words_dir)
    if not raw.exists():
        raw = names.exact_path(words_dir / f"{take_slug}.words.json", words_dir)
    script_slug = names.source_name(Path(speech or '').stem)
    script_words = names.exact_path(words_dir / f"{script_slug}.words.json", words_dir) if script_slug else None
    if not raw.exists():
        return JSONResponse({"error": "the take hasn't been transcribed yet"}, status_code=409)
    if not speech or script_words is None or not script_words.exists():
        return JSONResponse({"error": "this video has no script words"}, status_code=409)
    take_path = helpers._source(take_slug)
    proxy_path = names.exact_path(config.PROXIES_DIR / f"{take_slug}_540.mp4", config.PROXIES_DIR)
    proxy = proxy_path.relative_to(config.STATE_HOME).as_posix() if proxy_path.exists() else None
    try:
        take_frames = await asyncio.to_thread(helpers._source_duration_frames, take_path)
        narrated, retimed, alignment = script_video_mod.apply_narration(
            project_mod.from_dict(doc), script_video_mod.load_transcript(script_words),
            script_video_mod.load_transcript(raw), take_path.relative_to(config.STATE_HOME).as_posix(),
            take_frames, proxy,
        )
    except script_video_mod.NarrationError as e:
        return JSONResponse({"error": public_error(e, 422)}, status_code=422)
    except (OSError, subprocess.CalledProcessError, KeyError, TypeError, ValueError, ValidationError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    _doc, current = helpers._read_project_doc(project)
    if current != on_disk:
        return JSONResponse({"currentRevision": current}, status_code=409)
    if raw.name.endswith(".words.json"):
        os.replace(raw, words_dir / f"{take_slug}.whisperx.json")
    transcribe_stage._write_outputs(retimed, take_slug)  # noqa: SLF001 - the transcription sidecar format
    if proxy is None:
        compute._spawn_proxy(take_path, project=project)
    response = helpers._commit_project(project, narrated, on_disk)
    if response.status_code != 200:
        return response
    return JSONResponse({**json.loads(bytes(response.body)), "alignment": alignment})
