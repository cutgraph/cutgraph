from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

import config
from pipeline import names

router = APIRouter()


UI_DIST = config.CODE_ROOT / "ui" / "dist"  # built React editor (Vite base "/editor/")


_CTYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".json": "application/json",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png",
}


def _editor_response(rel: str) -> Response:
    """SPA shell + assets for the React editor (Vite base "/editor/").

    /editor and any deep link -> index.html; /editor/assets/* -> the built asset, path-sanitized
    to UI_DIST/assets so a crafted URL can't escape the dist tree.
    """
    if rel.startswith("assets/"):
        target = names.exact_path(UI_DIST / rel, UI_DIST).resolve()
        root = (UI_DIST / "assets").resolve()
        if root not in target.parents or not target.is_file():
            return PlainTextResponse("not found", status_code=404)
        data = target.read_bytes()
        return Response(
            data,
            media_type=_CTYPES.get(target.suffix.lower(), "application/octet-stream"),
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )
    index = UI_DIST / "index.html"
    if not index.is_file():
        return PlainTextResponse("editor not built (run the ui build)", status_code=404)
    # The shell names content-hashed assets. Never let a long-lived Studio tab
    # reuse an old shell after deploy; the referenced assets themselves are
    # immutable and remain aggressively cacheable above.
    return Response(
        index.read_text(encoding="utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/editor")
@router.get("/editor/{rest:path}")
def editor(rest: str = "") -> Response:
    return _editor_response(rest)
