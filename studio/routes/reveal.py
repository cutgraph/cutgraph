from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import names
from studio import helpers

router = APIRouter()
_XDG_OPEN = shutil.which("xdg-open")
if _XDG_OPEN:
    _XDG_OPEN = str(Path(_XDG_OPEN).resolve()) if Path(_XDG_OPEN).is_absolute() else None


# ── auth ───────────────────────────────────────────────────────────────────────
def require_loopback_client(request: Request) -> None:
    """Guard local file-explorer actions against clients outside this computer."""
    host = request.client.host if request.client else None
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="loopback clients only")


@router.post("/api/reveal", dependencies=[Depends(require_loopback_client)])
async def api_reveal(request: Request) -> Response:
    """Select one rendered artifact in the OS file explorer (Export sheet's "Show in folder").

    Body is `{"project": "<slug>"}` (resolved to its compiled render, `_install_render_output`'s own
    naming) or `{"path": "<state-home-relative path>"}` for anything else already under STATE_HOME.
    Both shapes are resolved and re-checked against STATE_HOME so neither an unsafe slug nor a
    traversal in `path` can select a file outside the app's own state (containment, not trust in the
    input). Loopback-only (`require_loopback_client` above) —
    this shells a GUI window, meaningless and unsafe to trigger from another device on the LAN.
    """
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    raw_project = body.get("project")
    raw_path = body.get("path")
    if isinstance(raw_project, str) and raw_project:
        project = helpers._safe(raw_project)
        if not project:
            return JSONResponse({"error": "bad project"}, status_code=400)
        target = config.FINAL_DIR / f"{project}.compiled.mp4"
    elif isinstance(raw_path, str) and raw_path:
        target = config.STATE_HOME / raw_path
    else:
        return JSONResponse({"error": "project or path is required"}, status_code=400)
    try:
        resolved = target.resolve()
        resolved.relative_to(config.STATE_HOME.resolve())
    except ValueError:
        return JSONResponse({"error": "path is outside the app state directory"}, status_code=403)
    names.exact_path(target, config.STATE_HOME, resolved=resolved)
    if not resolved.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    if sys.platform == "win32":
        system_root = Path(os.environ.get("SystemRoot", ""))
        if not system_root.is_absolute():
            return JSONResponse({"error": "file explorer is unavailable on this platform"}, status_code=501)
        subprocess.Popen([str(system_root / "explorer.exe"), f"/select,{resolved}"], shell=False)
    elif sys.platform == "linux" and _XDG_OPEN:
        subprocess.Popen([_XDG_OPEN, str(resolved.parent)], shell=False)
    else:
        return JSONResponse({"error": "file explorer is unavailable on this platform"}, status_code=501)
    return JSONResponse({"ok": True})
