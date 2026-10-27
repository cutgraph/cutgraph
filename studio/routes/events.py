from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse
from sse_starlette.sse import EventSourceResponse

from studio import helpers, state

router = APIRouter()


@router.get("/api/events")
async def api_events(request: Request, project: str) -> Response:
    """SSE stream of accepted-write notifications for one project (broadcast).

    Emits a revision-only `{"type":"project","revision":N}` on every accepted write; the client
    re-GETs the full project on receipt (the single "adopt newer state" code path).
    """
    proj = project if project == state.ADDON_EVENTS_CHANNEL else helpers._safe(project)
    if not proj:
        return JSONResponse({"error": "bad project"}, status_code=404)
    q = state.hub.subscribe(proj)  # subscribe before returning so no write between here and stream is lost

    async def _events() -> AsyncIterator[dict]:
        try:
            while True:
                msg = await q.get()
                yield {"data": json.dumps(msg)}
        finally:
            state.hub.unsubscribe(proj, q)

    return EventSourceResponse(_events())

