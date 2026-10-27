from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from studio.errors import JSONResponse
from studio.errors import failure_message

from pipeline import jobs as jobs_mod
from studio import compute, helpers, state

router = APIRouter()


@router.get("/api/jobs")
def api_jobs() -> Response:
    # Legacy whole-dict route (slug-keyed transcribe status the web dashboard/clip pages poll). The
    # generalized detached jobs live in the `jobs` registry, addressed by id below.
    return JSONResponse({slug: {
        **record,
        **({"error": failure_message(record.get("error"))}
           if record.get("status") in {"error", "failed"} else {}),
    } for slug, record in state._JOBS.items()})


@router.get("/api/compute/jobs")
def api_compute_jobs(project: str) -> Response:
    selected = helpers._safe(project)
    if not selected:
        return JSONResponse({"error": "bad project"}, status_code=400)
    jobs = [
        compute._job_presentation(job)
        for job in jobs_mod.all_persisted().values()
        if job.get("project") == selected
    ]
    jobs.sort(key=lambda job: (
        str(job.get("createdAt") or ""),
        int(job.get("createdOrdinal", -1)) if isinstance(job.get("createdOrdinal"), int) else -1,
        int(job.get("eventSequence", -1)) if isinstance(job.get("eventSequence"), int) else -1,
        str(job.get("id") or ""),
    ), reverse=True)
    return JSONResponse({"jobs": jobs})


@router.get("/api/compute/jobs/{job_id}")
def api_compute_job(job_id: str) -> Response:
    job = jobs_mod.get(job_id.strip("/"))
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    return JSONResponse(compute._job_presentation(job))


@router.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> Response:
    """A single detached job by opaque id (poll status/output/error)."""
    jid = job_id.strip("/")
    try:
        job = jobs_mod.get(jid)
    except ValueError:
        return JSONResponse({"error": "bad job id"}, status_code=400)
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    return JSONResponse(compute._job_presentation(job))

