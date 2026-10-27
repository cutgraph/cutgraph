from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

from pipeline import addons
from studio import compute, helpers
from studio.errors import JSONResponse

router = APIRouter()


def _notify(job_id: str) -> None:
    from pipeline import jobs

    job = jobs.get(job_id)
    if job:
        compute._emit_job_update_threadsafe(str(job["project"]), job_id)


def _present(name: str, gpus: list[dict]) -> dict:
    job = addons.latest_job(name)
    result = addons.status(name, job=job, read_job=False)
    declared = next(a for a in addons.definitions() if a.name == name)
    result.update(title=declared.title or name, description=declared.description, downloadNote=declared.download_note,
                  removalNote=("Remove frees add-on packages, cached wheels and its owned model caches. Shared caches are kept."
                               if addons.bundle_root() else "Remove frees add-on packages and cached wheels. Source model caches are kept."))
    result["job"] = compute._job_presentation(job) if job else None
    result["tiers"] = [{"tier": addon.tier, "label": addon.tier_label or addon.tier, "note": addon.tier_note,
                        "approximateBytes": addon.approximate_bytes,
                        "declaration": addon.declaration(), "acceptance": addon.acceptance(),
                        "supported": addon.tier != "gpu" or any(gpu["supported"] for gpu in gpus)}
                       for addon in addons.definitions() if addon.name == name and addon.lock_path.is_file()]
    return result


@router.get("/api/addons")
def list_addons() -> dict:
    gpus = addons.detect_gpus()
    return {"addons": [_present(name, gpus) for name in dict.fromkeys(a.name for a in addons.definitions())],
            "gpus": gpus, "recommendedTier": "gpu" if any(gpu["supported"] for gpu in gpus) else "cpu"}


@router.post("/api/addons/{name}/install")
async def install_addon(name: str, request: Request) -> Response:
    try:
        body = await helpers.read_json(request)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Expected a JSON object."}, status_code=400)
        tier, acceptance = body.get("tier"), body.get("acceptance")
        if not isinstance(tier, str) or not isinstance(acceptance, str):
            raise addons.AddonError("Choose a tier and accept its licence declaration before downloading.")
        compute._capture_job_event_loop()
        job_id = await asyncio.to_thread(addons.install, name, tier, acceptance, _notify,
                                         retry=body.get("retry") is True)
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON."}, status_code=400)
    except addons.AddonError as error:
        return JSONResponse({"error": str(error)}, status_code=error.status_code)
    return JSONResponse({"jobId": job_id}, status_code=202)


@router.delete("/api/addons/{name}")
async def remove_addon(name: str) -> Response:
    try:
        await asyncio.to_thread(addons.remove, name)
        return JSONResponse(addons.status(name))
    except addons.AddonError as error:
        return JSONResponse({"error": str(error)}, status_code=error.status_code)
