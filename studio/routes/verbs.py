from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import edits as edits_mod
from pipeline import project as project_mod
from pipeline import scene_render, timebase
from pipeline import names
from studio import helpers
from studio.errors import public_error
from pipeline.errors import ValidationError

router = APIRouter()


def _flat_words(slug: str) -> list[dict] | None:
    """Flat raw words for a source slug (segments→words order), or None if no words file. The order
    mirrors the transcript reshape so a `wordIdx` anchor lines up with them."""
    return scene_render.flat_words(names.exact_path(config.TRANSCRIPTS_DIR / f"{slug}.words.json", config.TRANSCRIPTS_DIR))


def _flat_word_starts(slug: str) -> list[float] | None:
    """Flat word start-seconds for a source slug, or None if no words file."""
    words = _flat_words(slug)
    return None if words is None else [w.get("start", 0.0) for w in words]


def _word_frames_for(base: project_mod.Project) -> dict[str, list[int]]:
    """Source→word-source-frames for every distinct speech clip, feeding ripple_cut's word-anchor
    recompute. A source whose words file is missing/unsafe is omitted → its anchors fall back to
    time-pinned (edits.ripple_cut's documented behavior)."""
    out: dict[str, list[int]] = {}
    for track in base.tracks:
        if track.kind != "speech":
            continue
        for clip in track.clips:
            src = clip.media.source
            if src in out:
                continue
            slug = helpers._safe(Path(src).stem)
            starts = _flat_word_starts(slug) if slug else None
            if starts is not None:
                out[src] = [timebase.sec_to_frame(s, base.fps) for s in starts]
    return out


@router.post("/api/verbs/ripple_cut/{p:path}")
async def api_ripple_cut(p: str, request: Request) -> Response:
    """Stateless compute verb: apply ripple cuts to the POSTED project doc and
    return {project: nextDoc, warnings}. It does NOT commit — no revision bump, no SSE. The client
    persists nextDoc through the revision-gated saveProject PUT, so one accept = one revision."""
    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=404)
    try:
        body = await helpers.read_json(request, config.MAX_PROJECT_BODY_BYTES)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    doc = body.get("project")
    raw_cuts = body.get("cuts")
    if not isinstance(doc, dict) or not isinstance(raw_cuts, list):
        return JSONResponse({"error": "missing project/cuts"}, status_code=400)
    try:
        base = project_mod.from_dict(doc)
        ranges = [(int(c["startF"]), int(c["endF"])) for c in raw_cuts]
        proj2, warnings = edits_mod.apply_cuts(base, ranges, word_frames=_word_frames_for(base))
    except (ValidationError, KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    return JSONResponse({"project": project_mod.to_dict(proj2), "warnings": warnings})


def _resolved_word_frames_for(base: project_mod.Project) -> dict[str, list[int]]:
    """Like `_word_frames_for`, but unaligned words take their timed neighbour's frame (the Story
    model's rule), so a scene that starts on one still gets the boundary the UI shows."""
    out: dict[str, list[int]] = {}
    for track in base.tracks:
        if track.kind != "speech":
            continue
        for clip in track.clips:
            src = clip.media.source
            slug = helpers._safe(Path(src).stem)
            if src in out or not slug:
                continue
            words = _flat_words(slug)
            if words is not None:
                out[src] = edits_mod.resolve_word_frames(
                    [(w.get("start"), w.get("end")) for w in words], base.fps)
    return out


@router.post("/api/verbs/reorder_scenes/{p:path}")
async def api_reorder_scenes(p: str, request: Request) -> Response:
    """Stateless compute verb, the ripple_cut contract: put the POSTED project's scenes in `order` and
    re-sequence its speech track to match, returning {project: nextDoc, warnings} without committing.
    The client persists nextDoc through the revision-gated PUT; `baseRevision` must already be the
    on-disk revision (409 otherwise) so a reorder is never computed from a stale document."""
    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=404)
    try:
        body = await helpers.read_json(request, config.MAX_PROJECT_BODY_BYTES)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    doc, order, base_revision = body.get("project"), body.get("order"), body.get("baseRevision")
    if (not isinstance(doc, dict) or not isinstance(order, list) or not all(isinstance(i, str) for i in order)
            or not isinstance(base_revision, int) or isinstance(base_revision, bool)):
        return JSONResponse({"error": "missing project/order/baseRevision"}, status_code=400)
    try:
        _on_disk_doc, on_disk = helpers._read_project_doc(project)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    if base_revision != on_disk:
        return JSONResponse({"currentRevision": on_disk}, status_code=409)
    try:
        base = project_mod.from_dict(doc)
        proj2, warnings = edits_mod.reorder_scenes(base, order, word_frames=_resolved_word_frames_for(base))
    except (ValidationError, KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    return JSONResponse({"project": project_mod.to_dict(proj2), "warnings": warnings})


@router.post("/api/verbs/ripple_restore/{p:path}")
async def api_ripple_restore(p: str, request: Request) -> Response:
    """Stateless compute verb, the inverse of ripple_cut: put cut SOURCE ranges back on the speech
    track and return {project: nextDoc, warnings}. Like ripple_cut it does NOT commit; the client
    persists nextDoc through the revision-gated saveProject PUT."""
    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=404)
    try:
        body = await helpers.read_json(request, config.MAX_PROJECT_BODY_BYTES)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    doc = body.get("project")
    raw_ranges = body.get("ranges")
    if not isinstance(doc, dict) or not isinstance(raw_ranges, list):
        return JSONResponse({"error": "missing project/ranges"}, status_code=400)
    try:
        proj = project_mod.from_dict(doc)
        word_frames = _word_frames_for(proj)
        warnings: list[dict] = []
        for r in raw_ranges:
            proj, w = edits_mod.ripple_restore(proj, str(r["media"]), int(r["srcStartF"]), int(r["srcEndF"]),
                                               word_frames=word_frames)
            warnings.extend(w)
    except (ValidationError, KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    return JSONResponse({"project": project_mod.to_dict(proj), "warnings": warnings})
