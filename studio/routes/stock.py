from __future__ import annotations

import asyncio
import json
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

from pipeline import library as library_mod
from pipeline import stock as stock_mod
from pipeline.secret_store import SecretStoreError
from studio import compute, helpers
from studio.errors import public_error

router = APIRouter()


# ── stock search: Pexels + Pixabay ──────────────────────
@router.get("/api/stock/providers")
def api_stock_providers() -> Response:
    return JSONResponse(stock_mod.provider_status())


@router.post("/api/stock/keys")
async def api_set_stock_keys(request: Request) -> Response:
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        status = await asyncio.to_thread(stock_mod.set_keys, body)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except SecretStoreError:
        return JSONResponse({"error": "Could not store stock credentials."}, status_code=503)
    return JSONResponse(status)


@router.get("/api/stock/search")
async def api_stock_search(q: str, kind: str = "all", page: int = 1) -> Response:
    if kind not in ("all", "video", "photo"):
        return JSONResponse({"error": "invalid search kind"}, status_code=400)
    try:
        result = await asyncio.to_thread(stock_mod.search, q, kind=cast(stock_mod.SearchKind, kind), page=page)
    except stock_mod.StockError as e:
        return JSONResponse({"error": public_error(e, e.status_code), "code": e.code}, status_code=e.status_code)
    return JSONResponse(result)


@router.post("/api/workspaces/{workspace_id}/stock/import")
async def api_stock_import(workspace_id: str, request: Request) -> Response:
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        workspace = library_mod.get_workspace(workspace_id)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    if workspace is None:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    try:
        asset, deduped = await asyncio.to_thread(compute._gpu_run, stock_mod.import_stock_asset, workspace_id, body)
    except stock_mod.StockError as e:
        return JSONResponse({"error": public_error(e, e.status_code), "code": e.code}, status_code=e.status_code)
    except library_mod.LibraryError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    return JSONResponse({"asset": asset, "deduped": deduped}, status_code=201)
