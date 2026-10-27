from __future__ import annotations


from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response
from studio.errors import JSONResponse
from starlette.routing import BaseRoute

import config
from pipeline import jobs as jobs_mod
from pipeline import bridge_token, web_security
from pipeline import runtime_tools, state_lock
from pipeline import addons as addons_mod
from pipeline import updates as updates_mod
from pipeline.errors import ValidationError
from pipeline.names import NameConflict, NameNotFound
from pipeline.secret_store import SecretStoreError
from studio import compute, helpers, state
from studio.errors import ErrorResponses, error_body, logger, public_error
from studio.routes import (
    addons,
    agent_connection,
    cast,
    chat,
    editor,
    events,
    job_status,
    jobs,
    media,
    project_edits,
    projects,
    proxy,
    reveal,
    script_video,
    stock,
    system,
    transcripts,
    uploads,
    updates,
    verbs,
    workspaces,
)

@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Own and release every application-scoped worker, proxy, and agent child."""

    with state_lock.startup(config.STATE_HOME):
        async with _owned_lifespan():
            yield


@asynccontextmanager
async def _owned_lifespan() -> AsyncIterator[None]:

    addons_mod.activate()
    runtime_tools.configure_process_environment()
    addons_mod.startup()
    helpers.cleanup_stale_uploads()
    jobs_mod.recover_jobs()
    compute._prepare_runtime_start()
    updates_mod.service().start()
    try:
        bridge_token.create()
    except (OSError, SecretStoreError):
        # Editing never needs the bridge; the assistant creates the file again when it starts.
        logger.warning("could not prepare the compute bridge token file", exc_info=True)
    try:
        yield
    finally:
        updates_mod.shutdown()
        addons_mod.shutdown()
        try:
            compute._stop_fifo_worker()
        finally:
            try:
                compute._stop_background_work()
            finally:
                host = chat._existing_chat_host()
                try:
                    if host is not None:
                        closer = getattr(host, "aclose_all", None)
                        if closer is not None:
                            await closer()
                finally:
                    if host is state._agent_host:
                        state._agent_host = None
                    bridge_token.cleanup()


# The local app does not expose interactive API documentation.
app = FastAPI(
    title="Cutgraph Studio",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_lifespan,
    telemetry={"auto_configure": False, "tracing": False, "metrics": False, "logs": False},
)


app.add_middleware(web_security.WebSecurityMiddleware)
app.add_middleware(ErrorResponses)


@app.exception_handler(helpers.BodyReadError)
async def _body_error(_request: Request, error: helpers.BodyReadError) -> JSONResponse:
    return JSONResponse({"error": error.detail}, status_code=error.status_code)


@app.exception_handler(NameConflict)
async def _name_conflict(_request: Request, error: NameConflict) -> JSONResponse:
    return JSONResponse({"error": public_error(error, 409), "code": "name_conflict"}, status_code=409)


@app.exception_handler(ValidationError)
async def _domain_validation_error(_request: Request, error: ValidationError) -> JSONResponse:
    return JSONResponse({"error": public_error(error, 400)}, status_code=400)


@app.exception_handler(NameNotFound)
async def _missing_item(_request: Request, error: NameNotFound) -> JSONResponse:
    return JSONResponse({"error": public_error(error, 404)}, status_code=404)


@app.exception_handler(HTTPException)
async def _http_error(_request: Request, error: HTTPException) -> JSONResponse:
    logger.warning("HTTP request rejected: %s", error.detail)
    return JSONResponse(error_body({}, error.status_code), status_code=error.status_code, headers=error.headers)


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    logger.warning("Request validation failed: %s", error)
    return JSONResponse(error_body({}, 422), status_code=422)


@app.exception_handler(Exception)
async def _server_error(_request: Request, error: Exception) -> Response:
    from starlette.responses import JSONResponse as ServerJSONResponse

    # Starlette's outer error handler sends these responses outside user middleware.
    return ServerJSONResponse(error_body({"error": public_error(error, 500)}, 500),
                        status_code=500, headers=web_security.SECURITY_HEADERS)


ROUTERS = (
    updates.router,
    addons.router,
    agent_connection.router,
    system.router, transcripts.router, job_status.router, projects.router,
    events.router, chat.router, editor.router, media.router, reveal.router,
    proxy.router, jobs.router, verbs.router, project_edits.router, uploads.router,
    workspaces.router, cast.router, stock.router, script_video.router,
)
for _router in ROUTERS:
    app.include_router(_router)
del _router


def routes() -> list[BaseRoute]:
    """Every route the app serves, in match order (included routers keep their declaration order)."""
    return [route for router in ROUTERS for route in router.routes]
