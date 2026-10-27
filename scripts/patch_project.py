r"""Apply an RFC-6902 patch to a project.json through the studio's PATCH route (the third caller).

    .venv\Scripts\python scripts\patch_project.py <project> --ops '<json-array>' \
        [--host 127.0.0.1] [--port 8765]

Goes through HTTP (not a direct file write) so the CLI's change flows through the same revision gate
as the UI and fires the same in-process SSE broadcast — making this a true third caller of the write
verb. Flow: GET /api/project/<p> to read the current revision, then PATCH with {ops, baseRevision};
on a 409 (someone wrote first) re-GET the revision once and retry.
"""
from __future__ import annotations

import argparse
import json

import httpx


def main() -> int:
    ap = argparse.ArgumentParser(description="Patch a project.json via the studio PATCH route.")
    ap.add_argument("project", help="project slug")
    ap.add_argument("--ops", required=True, help="RFC-6902 ops as a JSON array")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    try:
        ops = json.loads(args.ops)
    except json.JSONDecodeError as e:
        print(f"bad --ops JSON: {e}")
        return 1
    if not isinstance(ops, list):
        print("--ops must be a JSON array of RFC-6902 operations")
        return 1

    origin = f"http://{args.host}:{args.port}"
    url = f"{origin}/api/project/{args.project}"
    with httpx.Client(headers={"Origin": origin}, trust_env=False) as client:
        r = client.get(url)
        if r.status_code != 200:
            print(f"error: GET {url} -> {r.status_code} {r.text}")
            return 1
        revision = r.json()["revision"]
        resp = client.patch(url, json={"ops": ops, "baseRevision": revision})
        if resp.status_code == 409:
            revision = client.get(url).json()["revision"]  # someone wrote first — refresh and retry once
            resp = client.patch(url, json={"ops": ops, "baseRevision": revision})

    if resp.status_code == 200:
        print(f"ok: revision {resp.json()['revision']}")
        return 0
    print(f"error: PATCH -> {resp.status_code} {resp.text}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
