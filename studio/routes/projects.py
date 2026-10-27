from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import names
from pipeline import project as project_mod
from studio import compute, helpers, state
from studio.errors import public_error
from pipeline.errors import ValidationError

router = APIRouter()


@router.get("/api/projects")
def api_projects() -> Response:
    return JSONResponse(_list_projects())


@router.post("/api/projects")
async def api_create_project(request: Request) -> Response:
    """Materialize a full-length editor project from an uploaded source clip."""
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    project = names.safe_name(str(body.get("project") or ""))
    source_slug = helpers._safe(str(body.get("source") or ""))
    if not project or not source_slug:
        return JSONResponse({"error": "project and source are required"}, status_code=400)
    title = body.get("title")
    if title is not None:
        if not isinstance(title, str) or not title.strip() or len(title) > project_mod.SCENE_TITLE_MAX:
            return JSONResponse({"error": "title must be 1-120 characters"}, status_code=400)
        title = title.strip()
    source = helpers._source(source_slug)
    if not source.exists():
        return JSONResponse({"error": "source not found"}, status_code=404)
    try:
        proj = await asyncio.to_thread(_create_project_from_source, project, source, title)
    except names.NameConflict:
        raise
    except FileExistsError:
        return JSONResponse({"error": "project already exists"}, status_code=409)
    except (OSError, subprocess.CalledProcessError, ValueError, ValidationError) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    # Best-effort: the scrub proxy may already be encoding from /upload; this call just
    # registers the new project as a waiter so it hears the SSE `job` event when it lands
    # (a no-op re-encode is only started if one hasn't already been kicked off).
    compute._spawn_proxy(source, project=project)
    # The durable job and SSE channel follow the chosen Studio project. The
    # uploaded source remains a bounded slug in private job metadata.
    return JSONResponse({
        "project": project_mod.to_dict(proj),
        "revision": proj.revision,
        "transcription": compute._enqueue_transcribe(source_slug, project=project),
    }, status_code=201)


def _create_project_from_source(project: str, source: Path, title: str | None = None) -> project_mod.Project:
    """Off-event-loop body of `api_create_project`: the ffprobe call, validation, and the atomic
    write all shell out or touch disk, so this whole sequence runs in a worker thread
    (`asyncio.to_thread`), never on the event loop — a slow ffprobe must not stall every other
    request (SSE included) for its duration.

    Avoids a two-tab check-then-write race that could otherwise overwrite the first project by
    keeping `_PROJECT_CREATE_LOCK` held for the same span, now inside the thread instead of on the
    loop.
    """
    with state._PROJECT_CREATE_LOCK:
        path = helpers._project_path(project)
        names.check_case_collision(path)
        if path.exists():
            raise FileExistsError(project)
        proj = helpers._project_from_source(project, source, title)
        project_mod.validate(proj)
        with project_mod.write_lock():
            project_mod.write_project(path, proj)
    return proj


@router.get("/api/project/{p:path}/transcription")
def api_project_transcription(p: str) -> Response:
    """Reconcile one editor project's transcript lifecycle from durable state."""
    project = helpers._safe(p.strip("/"))
    if not project or not helpers._project_path(project).exists():
        return JSONResponse({"error": "unknown project"}, status_code=404)
    return JSONResponse(compute._transcript_lifecycle(project))


@router.get("/api/project/{p:path}/proxy")
def api_project_proxy(p: str) -> Response:
    """Reconcile one editor project's scrub-proxy readiness.
    Polling complements the SSE `job` event from `_spawn_proxy`."""
    project = helpers._safe(p.strip("/"))
    if not project or not helpers._project_path(project).exists():
        return JSONResponse({"error": "unknown project"}, status_code=404)
    return JSONResponse(helpers._proxy_lifecycle(project))


@router.get("/api/project/{p:path}")
def api_get_project(p: str) -> Response:
    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=404)
    try:
        doc, on_disk = helpers._read_project_doc(project)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    if doc is None:
        # No project ever wrote this slug: 404, not a skeleton. Every creation path
        # (api.createProject, script-video) writes the doc first, so a real "new project" never hits
        # this branch; PUT/PATCH still bootstrap a fresh slug themselves via their own _skeleton fallback.
        return JSONResponse({"error": "unknown project"}, status_code=404)
    # Serve through the model so a v0 doc is migrated on read (parity with PUT/PATCH); the reported
    # revision is the authority from the same single parse, never a second divergent read.
    try:
        proj = project_mod.from_dict(doc)
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    return JSONResponse({"project": project_mod.to_dict(proj), "revision": on_disk})


def _list_projects() -> list[dict]:
    out = []
    if config.PROJECTS_DIR.exists():
        for p in sorted(config.PROJECTS_DIR.glob("*.json")):
            # Field extraction stays INSIDE the guard: a valid-JSON-but-non-dict doc (doc.get →
            # AttributeError) or a non-int-coercible revision (int(None) → TypeError, int("x") →
            # ValueError) must skip that one file, not 500 the whole listing (matches the narrow-route
            # pattern at _read_project_doc's callers). A corrupt project doc shouldn't break the picker.
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(doc, dict):
                    continue
                out.append({
                    "project": p.stem,
                    "name": doc.get("name", p.stem),
                    "revision": int(doc.get("revision", 0)),
                    "mtime": p.stat().st_mtime,
                })
            except (json.JSONDecodeError, OSError, TypeError, ValueError, AttributeError):
                continue
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out
