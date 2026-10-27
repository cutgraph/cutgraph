from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import project as project_mod
from pipeline import names
from studio import helpers
from studio.errors import public_error
from pipeline.errors import ValidationError

router = APIRouter()


@router.get("/api/peaks/{slug:path}")
def api_peaks(slug: str) -> Response:
    """Serve the waveform-peaks sidecar verbatim; 404 when it hasn't been built."""
    s = helpers._safe(slug.strip("/"))
    path = names.exact_path(config.TRANSCRIPTS_DIR / f"{s}.peaks.json", config.TRANSCRIPTS_DIR) if s else None
    if not path or not path.exists():
        return JSONResponse({"error": "no peaks"}, status_code=404)
    return Response(path.read_text(encoding="utf-8"), media_type="application/json")


# ── routes: PUT ────────────────────────────────────────────────────────────────
@router.put("/api/project/{p:path}")
async def put_project(p: str, request: Request) -> Response:
    prelude = await helpers._write_prelude(p, request, "project", dict, "missing project/baseRevision")
    if isinstance(prelude, Response):
        return prelude
    project, doc, _on_disk_doc, on_disk = prelude
    try:
        proj = project_mod.from_dict(doc)  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    return helpers._commit_project(project, proj, on_disk)


@router.patch("/api/project/{p:path}")
async def patch_project(p: str, request: Request) -> Response:
    import jsonpatch
    import jsonpointer

    prelude = await helpers._write_prelude(p, request, "ops", list, "missing ops/baseRevision")
    if isinstance(prelude, Response):
        return prelude
    project, ops, on_disk_doc, on_disk = prelude
    # Deliberately scoped like PUT's (never a 500): every bad-input path apply_ops can take — a bad
    # patch/pointer, a malformed op dict, or a validate() failure on the result — is a 400; an
    # unexpected error still surfaces as a 500 rather than being masked as client error.
    try:
        base = project_mod.from_dict(on_disk_doc) if on_disk_doc is not None else helpers._skeleton(project)
        proj = project_mod.apply_ops(base, ops)  # type: ignore[arg-type]
    except (ValidationError, KeyError, TypeError, ValueError,
            jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    return helpers._commit_project(project, proj, on_disk)


# ── scenes: automatic topical grouping ──────────────────────────────────────
def _propose_scenes(project: str) -> list[dict] | None:
    """Group the transcript into scenes; return None when it has no words.json."""
    from pipeline import scenes as scenes_mod

    slug = helpers._safe(helpers._transcript_slug(project))
    words_path = names.exact_path(config.TRANSCRIPTS_DIR / f"{slug}.words.json", config.TRANSCRIPTS_DIR) if slug else None
    if not words_path or not words_path.exists():
        return None
    return scenes_mod.group(json.loads(words_path.read_text(encoding="utf-8")))


@router.post("/api/project/{p:path}/scenes/auto")
async def api_scenes_auto(p: str, request: Request, apply: bool = False) -> Response:
    """Propose scenes from the transcript as `{scenes}`. With `?apply=1` (body `{baseRevision}`) the
    proposal replaces `/scenes` as one patch through the same revision-gated commit as PATCH."""
    from pipeline import scenes as scenes_mod

    project = helpers._safe(p.strip("/"))
    if not project or not helpers._project_path(project).exists():
        return JSONResponse({"error": "unknown project"}, status_code=404)
    try:
        scenes = await asyncio.to_thread(_propose_scenes, project)
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    if scenes is None:
        return JSONResponse({"error": "no transcript"}, status_code=404)
    if not apply:
        return JSONResponse({"scenes": scenes})
    prelude = await helpers._write_prelude(p, request, "baseRevision", int, "missing baseRevision")
    if isinstance(prelude, Response):
        return prelude
    project, _base, doc, on_disk = prelude
    try:
        proj = project_mod.apply_ops(project_mod.from_dict(doc or {}), scenes_mod.set_scenes_ops(scenes))
    except (ValidationError, KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    return helpers._commit_project(project, proj, on_disk)

