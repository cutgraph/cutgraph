from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request
from fastapi.responses import Response

from pipeline.errors import ValidationError
from pipeline.secret_store import SecretStoreError
from studio import helpers, state
from studio.errors import JSONResponse

if TYPE_CHECKING:
    from scripts.agent_host import AgentHost

router = APIRouter()


async def _connection_body(request: Request) -> dict:
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError as error:
        raise ValidationError("invalid JSON") from error
    if not isinstance(body, dict):
        raise ValidationError("JSON body must be an object.")
    return body


@router.get("/api/agent/connection")
def api_agent_connection(verify: bool = True) -> Response:
    return JSONResponse(state._claude_connection.status(verify=verify))


@router.post("/api/agent/connection")
async def api_connect_agent(request: Request) -> Response:
    try:
        body = await _connection_body(request)
        status = await asyncio.to_thread(state._claude_connection.connect, body.get("key"))
    except ValidationError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except SecretStoreError:
        return JSONResponse({"error": "Could not store Claude credentials."}, status_code=503)
    return JSONResponse(status)


@router.post("/api/agent/connection/verify")
async def api_verify_agent(request: Request) -> Response:
    try:
        await _connection_body(request)
        status = await asyncio.to_thread(state._claude_connection.verify)
    except ValidationError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except SecretStoreError:
        return JSONResponse({"error": "Could not read Claude credentials."}, status_code=503)
    return JSONResponse(status)


@router.delete("/api/agent/connection")
async def api_disconnect_agent(request: Request) -> Response:
    try:
        await _connection_body(request)
        status = await asyncio.to_thread(state._claude_connection.disconnect)
    except ValidationError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except SecretStoreError:
        return JSONResponse({"error": "Could not remove Claude credentials."}, status_code=503)
    from studio.routes.chat import _existing_chat_host
    host = _existing_chat_host()
    if host is not None:
        await cast("AgentHost", host).aclose_all()
    return JSONResponse(status)
