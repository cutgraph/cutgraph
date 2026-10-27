from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import (
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from studio.errors import JSONResponse

import config
from pipeline import library as library_mod
from pipeline import thumbnail as thumbnail_mod
from pipeline import names
from studio import compute, helpers
from studio.errors import public_error

router = APIRouter()


@router.get("/media/compiled/{rest:path}")
def media_compiled(rest: str, request: Request) -> Response:
    name = helpers._safe(Path(rest).stem)
    if not name:
        return PlainTextResponse("not found", status_code=404)
    return helpers.range_response(config.FINAL_DIR / f"{name}.compiled.mp4", request)


@router.get("/media/project-asset/{project}/{filename:path}")
def media_project_asset(project: str, filename: str, request: Request) -> Response:
    safe_project = helpers._safe(project)
    safe_name = helpers._safe_asset_filename(unquote(filename))
    if not safe_project or not safe_name:
        return PlainTextResponse("not found", status_code=404)
    return helpers.range_response(_project_asset_dir(safe_project) / safe_name, request)


@router.get("/media/workspace/{workspace_id}/{filename}")
def media_workspace(workspace_id: str, filename: str, request: Request) -> Response:
    """A workspace library file referenced by a project clip (`workspaces/<id>/library/<file>`)."""
    try:
        path = library_mod.media_file(workspace_id, filename)
    except ValueError:
        path = None
    if path is None:
        return PlainTextResponse("not found", status_code=404)
    return helpers.range_response(path, request, mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.get("/media/source/{rest:path}")
def media_source(rest: str, request: Request) -> Response:
    return _serve_source(rest.strip("/"), request)


@router.get("/api/proxy/{slug}")
def api_proxy_state(slug: str) -> Response:
    """Whether `/media/source/<slug>` serves its proxy yet: ready, building or failed. Asking starts
    the encode, as opening the media does; the editor polls this to move its players onto the proxy."""
    source = _source_file(slug)
    if source is None:
        return JSONResponse({"error": "no source"}, status_code=404)
    state, _ = compute._source_proxy(source)
    return JSONResponse({"state": state})


@router.get("/media/source-raw/{rest:path}")
def media_source_raw(rest: str, request: Request) -> Response:
    return _serve_source_raw(rest.strip("/"), request)


@router.get("/media/proxy/{rest:path}")
def media_proxy(rest: str, request: Request) -> Response:
    # Serve only an exact flat .mp4 proxy name.
    raw = unquote(rest.strip("/"))
    path = Path(raw)
    if not raw or "/" in raw or "\\" in raw or path.suffix.lower() != ".mp4" or not (
        helpers._safe(path.stem) == path.stem or names.generated_name(path.stem, "_540")
    ):
        return PlainTextResponse("not found", status_code=404)
    proxy = helpers._existing_under(config.PROXIES_DIR / raw, config.PROXIES_DIR, {".mp4"})
    if proxy is None:
        return PlainTextResponse("not found", status_code=404)
    return helpers.range_response(proxy, request)


@router.get("/media/thumb/{rest:path}")
def media_thumb(rest: str, request: Request) -> Response:
    return _serve_thumb(rest.strip("/"), request)


@router.get("/media/preview/{rest:path}")
def media_preview(rest: str, request: Request) -> Response:
    name = helpers._safe(Path(rest).stem) if "/" not in rest and "\\" not in rest else None
    if not name:
        return PlainTextResponse("not found", status_code=404)
    return helpers.range_response(config.PREVIEW_DIR / f"{name}.mp4", request)


_THUMBNAIL_FILE_RE = re.compile(r"^[0-9a-f]{64}\.png$")


@router.get("/media/thumbnail/{name}")
def media_thumbnail(name: str, request: Request) -> Response:
    """A rendered thumbnail PNG, by the content-addressed name its render route returned."""
    if not _THUMBNAIL_FILE_RE.match(name):
        return PlainTextResponse("not found", status_code=404)
    return helpers.range_response(thumbnail_mod.cache_dir() / name, request, "image/png")


@router.post("/api/project/{p:path}/thumbnail/render")
async def api_thumbnail_render(p: str, request: Request) -> Response:
    """Render the video's thumbnail from its saved document at `{format}` ("16:9" or "9:16"),
    or take it from the cache, and return where `/media/thumbnail/<file>` serves the PNG."""
    body = await helpers._json_body(request)
    if body is None or body.get("format") not in thumbnail_mod.FORMATS:
        return JSONResponse({"error": f"format must be one of {sorted(thumbnail_mod.FORMATS)}"}, status_code=400)
    project = helpers._safe(p.strip("/"))
    try:
        doc, _on_disk = helpers._read_project_doc(project) if project else (None, 0)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    if doc is None:
        return JSONResponse({"error": "unknown project"}, status_code=404)
    fmt = body["format"]
    try:
        path = await asyncio.to_thread(thumbnail_mod.ensure_thumbnail, doc, fmt)
    except thumbnail_mod.ThumbnailRenderError as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    width, height = thumbnail_mod.FORMATS[fmt]
    return JSONResponse({"format": fmt, "width": width, "height": height, "url": f"/media/thumbnail/{path.name}"})


def _project_asset_dir(project: str) -> Path:
    return config.PROJECT_MEDIA_DIR / project


def _source_file(slug_raw: str) -> Path | None:
    slug = names.source_name(slug_raw)
    source = helpers._source(slug) if slug else None
    return source if source is not None and source.is_file() else None


def _serve_source(slug_raw: str, request: Request) -> Response:
    """A source's browser preview: its finished scrub proxy, else a redirect to the original file
    while the proxy encodes in the background. Chrome keeps reading a <video>'s redirect target, so
    no element sees its bytes change when the proxy lands."""
    source = _source_file(slug_raw)
    if source is None:
        return PlainTextResponse("not found", status_code=404)
    _, proxy = compute._source_proxy(source)
    if proxy is not None:
        return helpers.range_response(proxy, request)
    return RedirectResponse(
        f"/media/source-raw/data/source/{quote(source.name)}", status_code=307, headers={"Cache-Control": "no-store"},
    )


def _serve_source_raw(slug_raw: str, request: Request) -> Response:
    """Serve an already-existing source without ever generating a browser proxy.

    The ordinary `/media/source` route lazily builds a browser proxy.
    """
    raw = unquote(slug_raw.strip("/"))
    # Reject absolute/backslash/
    # traversal paths and unknown extensions; this route never turns a filename into a slug and
    # therefore cannot collide between take.mp4 and take.mov.
    if not raw or "\\" in raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        return PlainTextResponse("not found", status_code=404)
    parts = raw.split("/")
    if any(names.existing_name(part) is None for part in parts[:-1]) or helpers._safe_asset_filename(parts[-1]) != parts[-1]:
        return PlainTextResponse("not found", status_code=404)
    if parts[:2] == ["data", "source"]:
        if len(parts) != 3:
            return PlainTextResponse("not found", status_code=404)
        # DATA_DIR is independently configurable in tests and deployments. Keep
        # this exact source reference filename-preserving while still resolving
        # through the same containment/symlink guard as the state-root route.
        candidate = config.DATA_DIR / parts[2]
        root = config.DATA_DIR
    else:
        candidate = config.STATE_HOME / raw
        root = config.STATE_HOME
    source = helpers._existing_under(candidate, root, helpers.VIDEO_EXTS)
    if source is None:
        return PlainTextResponse("not found", status_code=404)
    return helpers.range_response(source, request)


def _serve_thumb(slug_raw: str, request: Request) -> Response:
    slug = helpers._safe(Path(slug_raw).stem)
    if not slug:
        return PlainTextResponse("not found", status_code=404)
    dest = helpers.THUMBS_DIR / f"{slug}.jpg"
    if not dest.exists():
        try:
            if compute._gpu_run(helpers._make_thumb, slug) is None:
                return PlainTextResponse("no source", status_code=404)
        except names.NameNotFound:
            raise
        except Exception as e:  # noqa: BLE001 — surface a failed thumbnail
            return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    return helpers.range_response(dest, request, "image/jpeg")
