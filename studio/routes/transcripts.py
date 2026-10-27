from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import names
from studio import helpers
from studio.errors import public_error

router = APIRouter()


@router.get("/api/transcript/{p:path}")
def api_transcript(p: str) -> Response:
    s = helpers._safe(p.strip("/"))
    slug = helpers._safe(helpers._transcript_slug(s)) if s else None
    words_path = names.exact_path(config.TRANSCRIPTS_DIR / f"{slug}.words.json") if slug else None
    if not words_path or not words_path.exists():
        return JSONResponse({"error": "no transcript"}, status_code=404)
    try:
        raw = json.loads(words_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    return JSONResponse(helpers._reshape_transcript(raw))

