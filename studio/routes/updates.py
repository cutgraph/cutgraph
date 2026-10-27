"""Local update status and explicit download controls."""
import json
from fastapi import APIRouter, Request
from fastapi.responses import Response

from pipeline import updates
from studio import helpers
from studio.errors import JSONResponse

router = APIRouter()


@router.get("/api/updates")
def status() -> dict:
    return updates.service().snapshot()


@router.post("/api/updates/check")
def check() -> dict:
    return updates.service().check()


@router.post("/api/updates/install")
def install() -> Response:
    try:
        return JSONResponse(updates.service().install(), status_code=202)
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=409)


@router.put("/api/updates/preferences")
async def preferences(request: Request) -> Response:
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "Expected an automatic boolean."}, status_code=400)
    if not isinstance(body, dict) or set(body) != {"automatic"} or type(body["automatic"]) is not bool:
        return JSONResponse({"error": "Expected an automatic boolean."}, status_code=400)
    return JSONResponse(updates.service().set_automatic(body["automatic"]))
