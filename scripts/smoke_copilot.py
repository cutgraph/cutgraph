r"""Live co-editor smoke test — one real chat turn against a running studio, asserting the SDK
authenticates and replies. It shows that a running Studio can use its configured API key;
missing or rejected credentials surface here as an `agent_error` instead of a `chat_text`.

Uses the local `.venv` HTTP client with environment proxies disabled. Drives the same async
contract the UI uses (POST enqueues, the reply lands in the durable chat log): POST one turn tagged
with a nonce, then poll `GET /api/chat/<slug>/log` for the FIRST agent entry after our tagged user
line — `chat_text` (pass) or `agent_error` (fail). Prints a one-line JSON verdict and exits 0/1.

    python scripts/smoke_copilot.py --url http://127.0.0.1:8765 \
        --project __smoke__ --timeout 90
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.parse import urlsplit

import httpx

# A plain-text reply that needs no project doc and no tools — keeps the smoke about auth + liveness,
# not editorial behaviour. The nonce lets us find OUR turn in a shared, truncated (last-200) log tail.
_PROMPT = "[smoke test {nonce}] Automated connection check. Reply with exactly the single word READY and use no tools."


def _headers() -> dict[str, str]:
    return {"Content-Type": "application/json"}


def _request(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> httpx.Response:
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    with httpx.Client(trust_env=False, timeout=15) as client:
        response = client.request(method, url, content=body, headers={**headers, "Origin": origin})
        response.raise_for_status()
        return response


def _post_turn(base: str, project: str, headers: dict[str, str], text: str) -> "str | None":
    """Enqueue one turn. Returns an error string on failure (503 host disabled, 401 auth, …) or None."""
    url = f"{base}/api/chat/{project}"
    body = json.dumps({"text": text}).encode()
    try:
        resp = _request("POST", url, headers, body)
        data = json.loads(resp.content or b"{}")
        return None if data.get("ok") else f"POST returned {data}"
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:200]
        return f"POST {exc.response.status_code}: {detail}"
    except httpx.RequestError as exc:
        return f"POST unreachable: {exc}"


def _poll_reply(base: str, project: str, headers: dict[str, str], nonce: str,
                timeout: float, interval: float) -> dict:
    """Poll the durable chat log for the first agent entry after our nonce-tagged user line."""
    url = f"{base}/api/chat/{project}/log"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = _request("GET", url, headers)
            entries = json.loads(resp.content or b"{}").get("entries", [])
        except (httpx.HTTPError, json.JSONDecodeError):
            entries = []
        seen_user = False
        for entry in entries:
            if not seen_user:
                if entry.get("role") == "user" and nonce in str(entry.get("text", "")):
                    seen_user = True
                continue
            if entry.get("role") != "agent":
                continue
            if entry.get("type") == "chat_text":
                return {"ok": True, "reply": str(entry.get("text", "")).strip()}
            if entry.get("type") == "agent_error":
                return {"ok": False, "error": entry.get("message", "agent_error"),
                        "kind": entry.get("kind", "runtime")}
        time.sleep(interval)
    return {"ok": False, "error": f"no agent reply within {int(timeout)}s", "kind": "timeout"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Live co-editor smoke test against a running Studio.")
    ap.add_argument("--url", required=True, help="studio base URL, e.g. http://127.0.0.1:8765")
    ap.add_argument("--project", required=True, help="throwaway project slug for the turn")
    ap.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for the reply")
    ap.add_argument("--interval", type=float, default=2.0, help="log poll interval in seconds")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    headers = _headers()
    nonce = f"{int(time.time())}-{time.monotonic_ns() % 100000}"
    prompt = _PROMPT.format(nonce=nonce)

    post_err = _post_turn(base, args.project, headers, prompt)
    if post_err is not None:
        print(json.dumps({"ok": False, "error": post_err, "kind": "post"}))
        return 1

    verdict = _poll_reply(base, args.project, headers, nonce, args.timeout, args.interval)
    print(json.dumps(verdict))
    return 0 if verdict.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
