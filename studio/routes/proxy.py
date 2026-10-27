from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from studio.errors import JSONResponse

from pipeline import proxy as proxy_stage
from pipeline.contracts import StageResult
from studio import compute, helpers
from studio.errors import failure_message

router = APIRouter()


# ── routes: POST ───────────────────────────────────────────────────────────────
@router.post("/api/proxy/{slug:path}")
def api_make_proxy(slug: str) -> Response:
    # Manual (re)generation trigger: run the proxy verb synchronously and return its StageResult.
    s = helpers._safe(slug.strip("/"))
    if not s:
        return JSONResponse({"error": "bad slug"}, status_code=400)
    src = helpers._source(s)
    if not src.exists():
        return JSONResponse({"error": "no source"}, status_code=404)
    result = compute._gpu_run(proxy_stage.make_proxy, src)
    return JSONResponse(proxy_result(result), status_code=200 if result.status == "ok" else 500)


def proxy_result(result: StageResult) -> dict:
    return {
        "status": result.status,
        "output": f"/media/proxy/{helpers._safe(result.clip_id)}_540.mp4" if result.status == "ok" else None,
        "error": failure_message(result.details or result.reason) if result.status != "ok" else None,
    }

