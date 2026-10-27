from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import names
from studio import compute, helpers, state
from studio.errors import public_error

router = APIRouter()


@router.put("/upload/{filename:path}")
async def upload(filename: str, request: Request) -> Response:
    name = _safe_filename(unquote(filename))
    if not name:
        return JSONResponse({"error": "bad filename or unsupported type"}, status_code=400)
    length = helpers.check_body_limit(request, config.MAX_SOURCE_UPLOAD_BYTES)
    if length == 0:
        return JSONResponse({"error": "empty upload"}, status_code=400)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.DATA_DIR / name
    names.check_case_collision(dest, source_stem=True)
    if dest.exists():
        return JSONResponse({"error": f"{name} already exists"}, status_code=409)
    tmp = config.DATA_DIR / f".{name}-{uuid.uuid4().hex}.part"
    try:
        written = 0
        with tmp.open("xb") as fh:
            async for chunk in helpers.limited_stream(request, config.MAX_SOURCE_UPLOAD_BYTES):
                fh.write(chunk)
                written += len(chunk)
        if not written:
            raise helpers.BodyReadError(400, "empty upload")
        if length is not None and written < length:
            raise OSError("client disconnected before the upload finished")
        await asyncio.to_thread(_publish_locked, tmp, dest)
    except helpers.BodyReadError:
        raise
    except FileExistsError as e:
        return JSONResponse({"error": public_error(e, 409)}, status_code=409)
    except Exception as e:  # noqa: BLE001 — clean up the partial file, report the failure
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    finally:
        tmp.unlink(missing_ok=True)
    # Kick the scrub-proxy encode off a fire-and-forget thread so the upload stays responsive.
    compute._spawn_proxy(dest)
    return JSONResponse({"ok": True, "name": name, "slug": dest.stem})


def _safe_filename(raw: str) -> str | None:
    """Sanitize an uploaded name to `<slug><ext>` (slug matches _SAFE); reject non-video types."""
    return names.filename(raw, helpers.VIDEO_EXTS)


def _publish_upload(tmp: Path, dest: Path) -> None:
    try:
        os.link(tmp, dest)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    if os.name == "nt":
        os.rename(tmp, dest)
        return
    # Exclusive creation preserves a competing upload when hard links are unavailable.
    with dest.open("xb") as target:
        try:
            with tmp.open("rb") as source:
                shutil.copyfileobj(source, target)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise


def _publish_locked(tmp: Path, dest: Path) -> None:
    with state._PROJECT_CREATE_LOCK:
        names.check_case_collision(dest, source_stem=True)
        _publish_upload(tmp, dest)
