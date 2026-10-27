r"""Compute MCP server — a stdio FastMCP server the agent host spawns as a child.

Every verb is a THIN loopback-HTTP bridge to the studio's `POST /api/jobs`: the studio owns
the ONE job registry and the ONE single-slot GPU serializer, so this process never spawns ffmpeg or a
second queue — it just forwards the request (internal token from a private file) and returns the studio's reply as a
compact JSON string. Long renders come back as a `job_id` the agent polls via `get_job`; the expensive
`render_final` is always created human-gated (`needs-approval`); only the studio's
POST /api/jobs/<id>/approve (from the UI) transitions it to queued.

Env (set by the agent host when it launches this server):
  CUTGRAPH_STUDIO_URL studio base url (default http://127.0.0.1:8765)
  CUTGRAPH_PROJECT the slug this session edits (jobs are scoped to it)
  CUTGRAPH_INTERNAL_TOKEN_FILE private file containing the internal Studio request token

Unit tests mock the HTTP client and assert the bridged kind/params/token — no real GPU or network.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import addons

import httpx


def _server():
    if __name__ == "__main__":
        addons.activate("assistant")
    from mcp.server.fastmcp import FastMCP

    return FastMCP("cutgraph-gpu")


mcp = _server()

def _compact(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _studio_url() -> str:
    return os.environ.get("CUTGRAPH_STUDIO_URL", "http://127.0.0.1:8765").rstrip("/")


def _project() -> str:
    return os.environ.get("CUTGRAPH_PROJECT", "")


def _request(method: str, path: str, payload: "dict | None" = None) -> str:
    """One loopback request to the studio; the reply is re-serialized compact. A transport failure is
    reported as a compact error string (the agent sees it and can retry / surface it), never raised."""
    url = f"{_studio_url()}{path}"
    try:
        token_file = os.environ.get("CUTGRAPH_INTERNAL_TOKEN_FILE")
        token = Path(token_file).read_text(encoding="utf-8") if token_file else None
        headers = {"X-Cutgraph-Internal": token} if token else {}
        with httpx.Client(timeout=30.0, trust_env=False) as client:
            resp = client.request(method, url, json=payload, headers=headers)
        parsed: object
        try:
            parsed = resp.json()
        except ValueError:
            parsed = {"error": resp.text[:400]}
        out: dict[str, object] = dict(parsed) if isinstance(parsed, dict) else {"result": parsed}
        out.setdefault("_status", resp.status_code)
        return _compact(out)
    except (httpx.HTTPError, OSError, UnicodeError) as e:
        return _compact({"error": f"studio bridge failed: {e}"})


def _post_job(kind: str, params: "dict | None" = None, **extra: object) -> str:
    body = {"kind": kind, "project": _project(), "params": params or {}, **extra}
    return _request("POST", "/api/jobs", body)


@mcp.tool()
def transcribe() -> str:
    """WhisperX transcription of the project's source (single-slot GPU job)."""
    return _post_job("transcribe")


@mcp.tool()
def make_proxy() -> str:
    """(Re)build the 540p scrub proxy for the project's source."""
    return _post_job("make_proxy")


@mcp.tool()
def detect_silence() -> str:
    """VAD silence/pause detection over the project's audio (returns source-frame pause cuts)."""
    return _post_job("detect_silence")


@mcp.tool()
def render_final() -> str:
    """Full 1080p NVENC final render. Detached + HUMAN-GATED: ALWAYS created `needs-approval` — only the
    human's Approve button in the studio UI releases it (you cannot self-approve). Returns a job_id;
    poll it with `get_job` and report progress. Do NOT claim the render is done until get_job says so."""
    return _post_job("render_final")


@mcp.tool()
def render_preview_segment(start_f: int, end_f: int) -> str:
    """Windowed preview of timeline frames [start_f, end_f) for a taste decision. A small window returns
    `{output}` (a /media/preview/<id>.mp4) immediately; a larger window returns `{jobId,status}` to poll
    with `get_job`, whose result carries the `output` path. Windows are capped (~15s) — a too-long
    request is rejected; ask for a shorter window."""
    return _post_job("render_preview_segment", {"start_f": int(start_f), "end_f": int(end_f)})


@mcp.tool()
def get_job(job_id: str) -> str:
    """Poll a detached job's status/output/error by its opaque id."""
    if re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
        return _compact({"error": "invalid job id"})
    return _request("GET", f"/api/jobs/{job_id}")


if __name__ == "__main__":
    mcp.run()  # stdio transport (the SDK spawns this as a child and speaks MCP over the pipe)
