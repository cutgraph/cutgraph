from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from pipeline import addons, channels, runtime_tools, version
from studio import state

router = APIRouter()


@router.get("/api/distribution/{document}")
def distribution_document(document: Literal["notices", "sbom"]) -> FileResponse:
    root = runtime_tools.bundle_root()
    filename = {"notices": "THIRD-PARTY-NOTICES.txt", "sbom": "SBOM.cdx.json"}[document]
    if root is None or not (root / filename).is_file():
        raise HTTPException(404, "This document is included with installed distributions.")
    return FileResponse(root / filename, media_type="text/plain" if document == "notices" else "application/json",
                        filename=filename)


@router.get("/api/channels")
def get_channels() -> dict:
    return channels.load()


@router.get("/api/version")
def get_version() -> dict[str, str]:
    return {"version": version.__version__}


@router.get("/api/health")
def health() -> dict:
    """Path-free local runtime and capability probe for launchers and the UI."""
    ffmpeg = bool(runtime_tools.tool_path("ffmpeg"))
    ffprobe = bool(runtime_tools.tool_path("ffprobe"))
    connection = state._claude_connection.snapshot()
    assistant = addons.status("assistant")
    assistant.update(keySource={None: "none", "settings": "saved", "environment": "environment"}[connection["source"]],
                     verification=connection["state"])
    result = {
        "status": "ok",
        "runtime": {
            "application": "standalone",
        },
        "capabilities": {
            "schemaVersion": 1,
            "application": "standalone",
            "packaged": runtime_tools.bundle_root() is not None,
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "cpuRender": ffmpeg and ffprobe,
            "localInference": addons.available("transcription"),
            "transcription": addons.status("transcription"),
            "assistant": assistant,
        },
    }
    return result
