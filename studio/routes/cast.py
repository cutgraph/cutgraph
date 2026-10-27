from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import cast as cast_mod
from studio import helpers
from studio.errors import public_error

router = APIRouter()


def _cast_error(e: ValueError) -> Response:
    return JSONResponse({"error": public_error(e, 409 if isinstance(e, cast_mod.CastConflict) else 400)}, status_code=409 if isinstance(e, cast_mod.CastConflict) else 400)


def _video_workspace(video: str) -> str | None:
    project = helpers._safe(video.strip("/"))
    try:
        doc = helpers._read_project_doc(project)[0] if project else None
    except (OSError, json.JSONDecodeError):
        doc = None
    workspace = doc.get("workspaceId") if isinstance(doc, dict) else None
    return workspace if isinstance(workspace, str) else None


@router.get("/api/cast")
async def api_cast(workspace: str | None = None, video: str | None = None) -> Response:
    """What a video can cast: the brand's characters, plus its client workspace's (by `workspace`, or
    the workspace of `video`), with the default character."""
    if video:
        workspace = await asyncio.to_thread(_video_workspace, video)
    return JSONResponse(await asyncio.to_thread(cast_mod.video_cast, workspace))


@router.post("/api/cast/characters")
async def api_create_character(request: Request) -> Response:
    body = await helpers._json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        entry = await asyncio.to_thread(
            cast_mod.create_character, body.get("workspace"), body.get("name"), body.get("rig", cast_mod.DEFAULT_RIG),
            art=body.get("art"), palette=body.get("palette"))
    except ValueError as e:
        return _cast_error(e)
    return JSONResponse(entry, status_code=201)


@router.patch("/api/cast/characters/{character_id}")
async def api_update_character(character_id: str, request: Request) -> Response:
    body = await helpers._json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        entry = await asyncio.to_thread(
            cast_mod.update_character, character_id, name=body.get("name"), art=body.get("art"), palette=body.get("palette"),
            rig=body.get("rig"), approved=body.get("approved"))
    except FileNotFoundError:
        return JSONResponse({"error": "unknown character"}, status_code=404)
    except ValueError as e:
        return _cast_error(e)
    return JSONResponse(entry)


def _project_docs() -> Iterator[dict]:
    for path in sorted(config.PROJECTS_DIR.glob("*.json")) if config.PROJECTS_DIR.exists() else []:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            yield doc


@router.get("/api/cast/characters/{character_id}/usage")
async def api_character_usage(character_id: str) -> Response:
    """How many scenes cast a character (lead or co-star): what deleting it would change."""
    try:
        usage = await asyncio.to_thread(lambda: cast_mod.character_usage(character_id, _project_docs()))
    except FileNotFoundError:
        return JSONResponse({"error": "unknown character"}, status_code=404)
    return JSONResponse(usage)


@router.delete("/api/cast/characters/{character_id}")
async def api_delete_character(character_id: str) -> Response:
    try:
        deleted = await asyncio.to_thread(cast_mod.delete_character, character_id)
    except ValueError as e:
        return _cast_error(e)
    if not deleted:
        return JSONResponse({"error": "unknown character"}, status_code=404)
    return JSONResponse({"ok": True})


@router.put("/api/cast/default")
async def api_set_default_character(request: Request) -> Response:
    body = await helpers._json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        return JSONResponse(await asyncio.to_thread(cast_mod.set_default, body.get("workspace"), body.get("character")))
    except ValueError as e:
        return _cast_error(e)


@router.post("/api/cast/expressions")
async def api_create_cast_expression(request: Request) -> Response:
    body = await helpers._json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    character = body.get("character", cast_mod.DEFAULT_CHARACTER_ID)
    if not isinstance(character, str):
        return JSONResponse({"error": "character must be a character id"}, status_code=400)
    try:
        entry = await asyncio.to_thread(
            cast_mod.create_expression, character, body.get("name"), body.get("spec"), body.get("draft", False))
    except FileNotFoundError:
        return JSONResponse({"error": "unknown character"}, status_code=404)
    except ValueError as e:
        return _cast_error(e)
    return JSONResponse(entry, status_code=201)


@router.patch("/api/cast/expressions/{expression_id}")
async def api_update_cast_expression(
    expression_id: str, request: Request, character: str = cast_mod.DEFAULT_CHARACTER_ID,
) -> Response:
    body = await helpers._json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        entry = await asyncio.to_thread(
            cast_mod.update_expression, character, expression_id,
            name=body.get("name"), spec=body.get("spec"), approved=body.get("approved"))
    except FileNotFoundError as e:
        return JSONResponse({"error": public_error(e, 404)}, status_code=404)
    except ValueError as e:
        return _cast_error(e)
    return JSONResponse(entry)


@router.delete("/api/cast/expressions/{expression_id}")
async def api_delete_cast_expression(expression_id: str, character: str = cast_mod.DEFAULT_CHARACTER_ID) -> Response:
    if not await asyncio.to_thread(cast_mod.delete_expression, character, expression_id):
        return JSONResponse({"error": "unknown expression"}, status_code=404)
    return JSONResponse({"ok": True})


@router.post("/api/cast/describe")
async def api_describe_cast_expression(request: Request) -> Response:
    body = await helpers._json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    from scripts import (
        agent_host,  # lazy: agent_host itself imports the SDK lazily
    )

    try:
        rig = cast_mod.describe_rig(body.get("rig"), body.get("character"))
        proposal = await agent_host.describe_expression(body.get("text"), rig)
    except ValueError as e:
        return _cast_error(e)
    return JSONResponse(proposal)

