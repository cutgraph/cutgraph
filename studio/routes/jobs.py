from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import cast as cast_mod
from pipeline import compile as compile_stage
from pipeline import render_inputs
from pipeline import jobs as jobs_mod
from pipeline import addons
from pipeline import proxy as proxy_stage
from pipeline import silence as silence_mod
from studio import compute, helpers
from studio.errors import public_error
from studio.routes.proxy import proxy_result

router = APIRouter()


# ── Compute jobs — the studio owns the ONE registry + ONE GPU serializer ──
# `scripts/mcp_gpu.py` bridges every compute verb here over loopback HTTP. render_final is detached +
# human-gated; render_preview_segment runs inline (fast window); reframe/source_broll are stubs.
_UNAVAILABLE_JOB = {"available": False, "reason": "not available"}


def _validate_preview_window(params: dict) -> "tuple[int, int] | Response":
    """Parse + CAP a preview window: reject a missing/empty/over-cap window with a helpful 400
    so an unbounded agent request can't launch a multi-minute encode."""
    start_raw, end_raw = params.get("start_f"), params.get("end_f")
    if start_raw is None or end_raw is None:
        return JSONResponse({"error": "start_f/end_f required"}, status_code=400)
    try:
        start_f, end_f = int(start_raw), int(end_raw)
    except (TypeError, ValueError):
        return JSONResponse({"error": "start_f/end_f required"}, status_code=400)
    if end_f <= start_f:
        return JSONResponse({"error": f"empty preview window: [{start_f}, {end_f})"}, status_code=400)
    if end_f - start_f > config.PREVIEW_MAX_FRAMES:
        return JSONResponse(
            {"error": f"preview window too large: {end_f - start_f} frames exceeds the "
                      f"{config.PREVIEW_MAX_FRAMES}-frame cap (~{config.PREVIEW_MAX_FRAMES // 30}s) — "
                      f"request a shorter window."},
            status_code=400)
    return start_f, end_f


def _render_preview_to(project: str, pid: str, params: dict) -> dict:
    """Windowed-preview encode into PREVIEW_DIR/<pid>.mp4. MUST run under the single GPU slot (called
    via _gpu_run from the inline path and the detached worker). Returns {output} or {error}; prunes on
    success."""
    doc, _rev = helpers._read_project_doc(project)
    if doc is None:
        return {"error": f"unknown project: {project}"}
    dest = config.PREVIEW_DIR / f"{pid}.mp4"
    r = compile_stage.compile_preview_segment(doc, int(params["start_f"]), int(params["end_f"]), dest)
    if r.status != "ok":
        return {"error": r.reason or "preview failed"}
    compute._prune_preview_dir()
    return {"output": f"/media/preview/{pid}.mp4"}


def _render_final_dedupe_key(project: str, params: dict) -> str:
    """Bind repeated final-render requests to the exact project state/output. That includes the look
    each scene's character resolves to (its rig, palette and custom faces), which lives in the video's
    cast rather than its document."""
    doc, revision = helpers._read_project_doc(project)
    if doc is None:
        raise jobs_mod.JobError("MISSING_ARTIFACT", "project document is unavailable")
    digest = render_inputs.inputs_digest(doc, config.STATE_HOME, {"kind": "final"})
    encoded_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
    workspace = doc.get("workspaceId")
    cast = cast_mod.video_cast(workspace if isinstance(workspace, str) else None)
    looks = [cast_mod.resolve_shot(scene["shot"], cast) for scene in doc.get("scenes") or []
             if isinstance(scene, dict) and isinstance(scene.get("shot"), dict)]
    encoded_looks = json.dumps(looks, sort_keys=True, separators=(",", ":"))
    identity = f"{project}:{revision}:{digest}:{encoded_params}:{encoded_looks}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


async def _run_preview_segment(project: str, params: dict) -> Response:
    """Judge/choose preview. A SMALL window (≤ PREVIEW_SYNC_MAX_FRAMES) renders INLINE and returns
    {output}; a larger (but ≤ cap) window DETACHES as a single-slot GPU job returning {jobId,status} so
    the mcp_gpu bridge never blocks on a multi-minute encode inside its HTTP timeout."""
    doc, _rev = helpers._read_project_doc(project)
    if doc is None:
        return JSONResponse({"error": f"unknown project: {project}"}, status_code=404)
    win = _validate_preview_window(params)
    if isinstance(win, Response):
        return win
    start_f, end_f = win
    try:
        compute._render_facts(doc, preview=end_f - start_f > config.PREVIEW_SYNC_MAX_FRAMES).validate()
    except jobs_mod.JobError as exc:
        return JSONResponse(compute._public_job_error(exc), status_code=409)
    job_id = jobs_mod.new_job("render_preview_segment", project, {"start_f": start_f, "end_f": end_f})
    if end_f - start_f <= config.PREVIEW_SYNC_MAX_FRAMES:
        # It remains a fast response, but it is also a durable job so the
        # sole public replacement is protected by the publication CAS.
        compute._capture_job_event_loop()
        compute._publish_job_update(project, job_id)
        await asyncio.to_thread(compute._run_detached_job, job_id, jobs_mod.get(job_id) or {})
        completed = jobs_mod.get(job_id) or {}
        if completed.get("status") == "done":
            return JSONResponse({"output": completed.get("output")})
        return JSONResponse(
            {"error": completed.get("error", "preview failed"), "status": completed.get("status")},
            status_code=500,
        )
    compute._dispatch_queued_job(project, job_id)
    return JSONResponse({"jobId": job_id, "status": "queued"})


async def _run_inline_verb(kind: str, project: str, params: dict) -> Response:
    src = helpers._source(project)
    if not src.exists():
        return JSONResponse({"error": "no source"}, status_code=404)
    if kind == "make_proxy":
        return JSONResponse(proxy_result(await asyncio.to_thread(compute._gpu_run, proxy_stage.make_proxy, src)))
    if kind == "detect_silence":
        doc, _rev = helpers._read_project_doc(project)
        fps = int((doc or {}).get("fps", config.DEFAULT_FPS))
        return JSONResponse(await asyncio.to_thread(compute._gpu_run, silence_mod.detect, src, fps))
    # caption_burn is folded into render_final (compile draws text via _drawtext) — no standalone job.
    return JSONResponse({"error": f"{kind} is not a standalone job (folded into render_final)"}, status_code=400)


@router.post("/api/jobs")
async def api_create_job(request: Request) -> Response:
    """Create/dispatch a compute job. Detached kinds (render_final, larger previews) return `{jobId}`; a
    small render_preview_segment returns `{output}` inline; unavailable stages return
    `{available:false}`. Captures the running loop so the single-slot worker thread can marshal
    job-transition SSE events back onto it."""
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    kind = body.get("kind")
    if not isinstance(kind, str) or not kind:
        return JSONResponse({"error": "missing kind"}, status_code=400)
    if kind == "source_broll":
        return JSONResponse(dict(_UNAVAILABLE_JOB))

    project = helpers._safe(str(body.get("project") or ""))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=400)
    params = body.get("params") or {}
    if kind == "render_preview_segment":
        return await _run_preview_segment(project, params)
    if kind == "render_scene":
        # Pre-render one animated scene (or every one) into the scene cache; render_final renders any
        # still missing itself, so a client never has to.
        scene_id = params.get("sceneId")
        if scene_id is not None and not isinstance(scene_id, str):
            return JSONResponse({"error": "sceneId must be a string"}, status_code=400)
        doc, _revision = helpers._read_project_doc(project)
        if doc is None:
            return JSONResponse({"error": f"unknown project: {project}"}, status_code=404)
        job_id = jobs_mod.new_job("render_scene", project, {"sceneId": scene_id} if scene_id else {})
        compute._dispatch_queued_job(project, job_id)
        return JSONResponse({"jobId": job_id, "status": "queued"})
    if kind == "render_final":
        # render_final is ALWAYS created human-gated. There is no agent-settable `approved`
        # flag — the ONLY transition to queued is POST /api/jobs/{id}/approve (from the
        # UI). This closes the prompt-injection self-approval hole.
        try:
            doc, _revision = helpers._read_project_doc(project)
            if doc is None:
                return JSONResponse({"error": f"unknown project: {project}"}, status_code=404)
            compute._render_facts(doc, preview=False).validate()
        except jobs_mod.JobError as exc:
            return JSONResponse(compute._public_job_error(exc), status_code=409)
        try:
            dedupe_key = _render_final_dedupe_key(project, params)
        except jobs_mod.JobError as exc:
            return JSONResponse(compute._public_job_error(exc), status_code=409)
        try:
            operation = jobs_mod.create_render_final_job(
                project, dedupe_key=dedupe_key, metadata=params,
            )
        except jobs_mod.JobStateError as exc:
            return JSONResponse({"error": public_error(exc, 400)}, status_code=400)
        job_id, job, created = operation.job_id, operation.job, operation.created
        if not created:
            return JSONResponse({"jobId": job_id, "status": job["status"]})
        compute._publish_job_update(project, job_id)
        return JSONResponse({"jobId": job_id, "status": "needs-approval"})
    if kind == "transcribe":
        outcome = compute._enqueue_compute_action("transcribe", project)
        status = 409 if outcome.get("code") else (404 if outcome.get("error") == "no source" else 200)
        return JSONResponse(outcome, status_code=status)
    if kind == "reframe":
        outcome = compute._enqueue_compute_action("reframe", project)
        status = 409 if outcome.get("code") else (404 if outcome.get("error") == "no source" else 200)
        return JSONResponse(outcome, status_code=status)
    if kind in ("make_proxy", "detect_silence", "caption_burn"):
        return await _run_inline_verb(kind, project, params)
    return JSONResponse({"error": f"unknown kind: {kind}"}, status_code=400)


@router.post("/api/jobs/{job_id}/approve")
async def api_approve_job(job_id: str) -> Response:
    """Release a human-gated render_final onto the single-slot worker (expensive-render gate)."""
    jid = job_id.strip("/")
    try:
        job = jobs_mod.get(jid)
    except ValueError:
        return JSONResponse({"error": "bad job id"}, status_code=400)
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    if job.get("status") != "needs-approval":
        return JSONResponse(
            {"error": f"job not awaiting approval ({job.get('status')})", "status": job.get("status")},
            status_code=409,
        )
    try:
        updated, approved = jobs_mod.approve_job(jid)
    except jobs_mod.JobStateError:
        return JSONResponse({"error": "bad job id"}, status_code=400)
    if not approved:
        return JSONResponse(
            {"error": f"job not awaiting approval ({(updated or {}).get('status')})",
             "status": (updated or {}).get("status")},
            status_code=409,
        )
    # The queued presentation is a durable client-visible transition.  It
    # must be published before this ID becomes observable to the fast worker,
    # otherwise that worker can advance it to running and make SSE skip queued.
    compute._dispatch_queued_job(str(job.get("project") or ""), jid)
    return JSONResponse(compute._job_presentation(updated or job))


@router.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: str) -> Response:
    """Cancel queued work locally and propagate active worker cancellation by job ID only."""
    jid = job_id.strip("/")
    try:
        job = jobs_mod.get(jid)
    except ValueError:
        return JSONResponse({"error": "bad job id"}, status_code=400)
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    try:
        updated, cancelled = jobs_mod.cancel_job(jid)
    except jobs_mod.JobStateError:
        return JSONResponse({"error": "bad job id"}, status_code=400)
    if not cancelled:
        return JSONResponse(
            {"error": "job is already terminal", "status": (updated or job).get("status")},
            status_code=409,
        )
    project = str(job.get("project") or "")
    compute._emit_job_update_threadsafe(project, jid)
    return JSONResponse(compute._job_presentation(updated or job))


def _retry_job(
    job: dict,
) -> dict:
    """Create a new auditable attempt and link its durable state to the original record."""
    kind = str(job.get("kind") or "")
    project = str(job.get("project") or "")
    raw_meta = job.get("meta")
    meta: dict[str, object] = raw_meta if isinstance(raw_meta, dict) else {}
    if kind in {"transcribe", "reframe"}:
        if kind == "transcribe" and not addons.available("transcription"):
            raise jobs_mod.JobError("TRANSCRIPTION_NOT_INSTALLED", addons.unavailable_message("transcription"))
        source_slug = compute._job_source_slug(job)
        source = helpers._source(source_slug) if source_slug is not None else None
        if source is None or not source.exists():
            raise jobs_mod.JobError("MISSING_ARTIFACT", "source video is unavailable")
        # Retry only the one bounded mechanical-input reference. Do not copy
        # unrelated or legacy path-shaped metadata into a new durable attempt.
        meta = {"sourceSlug": source_slug}
        facts = jobs_mod.JobFacts(kind)
        facts.validate()
        status = "queued"
    elif kind in {"render_final", "render_preview_segment"}:
        doc, _revision = helpers._read_project_doc(project)
        if doc is None:
            raise jobs_mod.JobError(
                "MISSING_ARTIFACT", "project document is unavailable"
            )
        compute._render_facts(doc, preview=kind == "render_preview_segment").validate()
        status = "needs-approval" if kind == "render_final" else "queued"
    else:
        raise jobs_mod.JobError(
            "UNSUPPORTED_ACTION", "This job cannot be retried."
        )
    job_id = jobs_mod.new_job(kind, project, meta, status=status)
    parent_id = str(job.get("id") or "")
    jobs_mod.set(job_id, parentJobId=parent_id, attemptState="retrying")
    if parent_id:
        # Keep the original attempt terminal and auditable while recording the durable child
        # lifecycle that explains why another attempt is now active.
        jobs_mod.set(parent_id, retryState="retrying", retryChildJobId=job_id)
    if status == "queued":
        compute._dispatch_queued_job(project, job_id)
    else:
        compute._publish_job_update(project, job_id)
    return compute._job_presentation(jobs_mod.get(job_id) or {})


@router.post("/api/jobs/{job_id}/retry")
async def api_retry_job(job_id: str) -> Response:
    job = jobs_mod.get(job_id.strip("/"))
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    if job.get("status") not in {
        "error", "failed", "cancelled", "stale", "interrupted", "unknown_execution",
    }:
        return JSONResponse({"error": "job is not retryable"}, status_code=409)
    try:
        return JSONResponse(_retry_job(job))
    except jobs_mod.JobError as exc:
        return JSONResponse(compute._public_job_error(exc), status_code=409)
