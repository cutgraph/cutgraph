"""Revision-gate / PATCH / SSE tests for the FastAPI studio (scripts/studio_app.py).

In-process via httpx.ASGITransport — no real bind, no GPU. Proves the concurrency spine: the
whole-project revision gate (stale baseRevision -> 409), forced revision bump + name traversal
defense, RFC-6902 PATCH with re-validation, serialized concurrent writes, and the in-process SSE
broadcast delivering a revision-only event after an accepted write.

Async requests are driven with asyncio.run so no pytest-asyncio dependency is needed.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

import httpx
import pytest

import config
from pipeline import project as project_mod
from scripts import studio_app
from studio import helpers

T = TypeVar("T")

_PREFIX = "zzrev"


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _do(fn: Callable[[], Awaitable[T]]) -> T:
    return _run(fn())


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=studio_app.app)
    return httpx.AsyncClient(transport=transport, base_url="http://studio.test")


def _doc(name: str = "ignored", revision: int = 0) -> dict:
    """A minimal valid project.json v1 doc (one speech clip, no anchor)."""
    return {
        "schemaVersion": 1,
        "name": name,
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "revision": revision,
        "transcript": None,
        "tracks": [
            {"id": "t1", "kind": "speech", "clips": [
                {"id": "c1", "media": {"source": "data/source/a.mp4", "proxy": None},
                 "srcInF": 0, "srcOutF": 30, "tlStartF": 0, "gainDb": 0.0},
            ]},
        ],
    }


@pytest.fixture(autouse=True)
def _clean_projects() -> Iterator[None]:
    def _wipe() -> None:
        if config.PROJECTS_DIR.exists():
            for f in config.PROJECTS_DIR.glob(f"{_PREFIX}*"):
                f.unlink(missing_ok=True)
    _wipe()
    yield
    _wipe()


def test_project_commit_waits_for_shared_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    name = f"{_PREFIX}_locked"
    path = config.PROJECTS_DIR / f"{name}.json"
    validated = threading.Event()
    validate = project_mod.validate

    def signal_validation(proj: project_mod.Project) -> None:
        validate(proj)
        validated.set()

    monkeypatch.setattr(project_mod, "validate", signal_validation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with project_mod.write_lock():
            with project_mod.write_lock():
                pending = executor.submit(
                    helpers._commit_project, name, project_mod.from_dict(_doc()), 0,
                )
                assert validated.wait(timeout=5)
                with pytest.raises(TimeoutError):
                    pending.result(timeout=0.1)
                assert not pending.done()
                assert not path.exists()
        response = pending.result(timeout=5)
    assert response.status_code == 200
    assert json.loads(path.read_text(encoding="utf-8"))["revision"] == 1


def test_project_write_lock_releases_after_failure() -> None:
    with pytest.raises(ValueError, match="write failed"):
        with project_mod.write_lock():
            raise ValueError("write failed")
    with ThreadPoolExecutor(max_workers=1) as executor:
        def acquire() -> bool:
            with project_mod.write_lock():
                return True

        assert executor.submit(acquire).result(timeout=5)


# ── GET missing ──────────────────────────────────────────────────────────────────
def test_get_missing_project_is_404() -> None:
    """404: an unknown slug 404s instead of a 200 skeleton, so the Story view's
    "Couldn't open this video" state is reachable. PUT/PATCH still bootstrap a fresh slug themselves
    (their own _skeleton fallback), so no creation path ever needed the GET skeleton."""
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get(f"/api/project/{_PREFIX}_skel")

    r = _do(scenario)
    assert r.status_code == 404
    assert r.json() == {"error": "unknown project", "code": 'not_found'}


def test_get_migrates_v0_doc_on_read() -> None:
    """A pre-schemaVersion (v0) doc on disk — bare-string media, no schemaVersion/revision — must be
    served MIGRATED (parity with PUT/PATCH, which load through from_dict). The envelope revision comes
    from the same single parse: raw doc.get('revision', 0) == 0 for a v0 doc."""
    name = f"{_PREFIX}_v0"
    v0 = {
        "name": "ignored",
        "tracks": [
            {"id": "t1", "kind": "speech", "clips": [
                {"id": "c1", "media": "data/source/a.mp4", "srcInF": 0, "srcOutF": 30, "tlStartF": 0, "gainDb": 0.0},
            ]},
        ],
    }
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.PROJECTS_DIR / f"{name}.json").write_text(json.dumps(v0), encoding="utf-8")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get(f"/api/project/{name}")

    r = _do(scenario)
    assert r.status_code == 200
    body = r.json()
    assert body["revision"] == 0
    proj = body["project"]
    assert proj["schemaVersion"] == project_mod.CURRENT_SCHEMA_VERSION
    assert proj["tracks"][0]["clips"][0]["media"] == {"source": "data/source/a.mp4", "proxy": None}


# ── PUT accept + revision bump + name forcing ─────────────────────────────────────
def test_put_accepts_bumps_revision_and_forces_name() -> None:
    name = f"{_PREFIX}_put"

    async def scenario() -> httpx.Response:
        async with _client() as client:
            # doc carries a hostile name + untrusted revision; both must be overridden by the server.
            return await client.put(
                f"/api/project/{name}",
                json={"project": _doc(name="../evil", revision=999), "baseRevision": 0},
            )

    r = _do(scenario)
    assert r.status_code == 200
    body = r.json()
    assert body["revision"] == 1
    assert body["project"]["name"] == name
    assert body["project"]["revision"] == 1


# ── stale baseRevision -> 409 on PUT and PATCH ────────────────────────────────────
def test_put_stale_base_revision_conflicts() -> None:
    name = f"{_PREFIX}_putstale"

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            first = await client.put(
                f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            stale = await client.put(
                f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            return first, stale

    first, stale = _do(scenario)
    assert first.status_code == 200
    assert stale.status_code == 409
    assert stale.json() == {"currentRevision": 1, "error": "The request conflicts with the current state.", "code": "conflict"}


def test_patch_stale_base_revision_conflicts() -> None:
    name = f"{_PREFIX}_patchstale"

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            stale = await client.patch(
                f"/api/project/{name}",
                json={"ops": [{"op": "replace", "path": "/tracks/0/clips/0/srcOutF", "value": 60}],
                      "baseRevision": 0},
            )
            return stale, stale

    stale, _ = _do(scenario)
    assert stale.status_code == 409
    assert stale.json() == {"currentRevision": 1, "error": "The request conflicts with the current state.", "code": "conflict"}


# ── PATCH applies RFC-6902 ops and re-validates ───────────────────────────────────
def test_patch_applies_ops_and_bumps_revision() -> None:
    name = f"{_PREFIX}_patchok"

    async def scenario() -> httpx.Response:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            return await client.patch(
                f"/api/project/{name}",
                json={"ops": [{"op": "replace", "path": "/tracks/0/clips/0/srcOutF", "value": 60}],
                      "baseRevision": 1},
            )

    r = _do(scenario)
    assert r.status_code == 200
    body = r.json()
    assert body["revision"] == 2
    assert body["project"]["tracks"][0]["clips"][0]["srcOutF"] == 60
    assert body["project"]["name"] == name


def test_patch_invalid_result_is_400() -> None:
    name = f"{_PREFIX}_patchbad"

    async def scenario() -> httpx.Response:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            # srcOutF == srcInF violates srcOutF > srcInF -> validate() fails -> 400.
            return await client.patch(
                f"/api/project/{name}",
                json={"ops": [{"op": "replace", "path": "/tracks/0/clips/0/srcOutF", "value": 0}],
                      "baseRevision": 1},
            )

    r = _do(scenario)
    assert r.status_code == 400


def test_patch_bad_pointer_is_400() -> None:
    """A pointer at a missing path raises jsonpointer.JsonPointerException inside apply_ops. The
    scoped PATCH handler must map that (and any bad op) to 400, never 500."""
    name = f"{_PREFIX}_patchptr"

    async def scenario() -> httpx.Response:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            return await client.patch(
                f"/api/project/{name}",
                json={"ops": [{"op": "replace", "path": "/tracks/9/clips/0/srcOutF", "value": 60}],
                      "baseRevision": 1},
            )

    r = _do(scenario)
    assert r.status_code == 400


# ── serialized concurrent writes ─────────────────────────────────────────────────
def test_concurrent_writes_serialize() -> None:
    name = f"{_PREFIX}_serial"

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            accepted = await client.put(
                f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            # a second writer still holding the pre-write baseRevision loses the race.
            loser = await client.patch(
                f"/api/project/{name}",
                json={"ops": [{"op": "replace", "path": "/tracks/0/clips/0/tlStartF", "value": 5}],
                      "baseRevision": 0},
            )
            return accepted, loser

    accepted, loser = _do(scenario)
    assert accepted.status_code == 200
    assert accepted.json()["revision"] == 1
    assert loser.status_code == 409
    assert loser.json() == {"currentRevision": 1, "error": "The request conflicts with the current state.", "code": "conflict"}


# ── SSE broadcast after an accepted write ─────────────────────────────────────────
# httpx.ASGITransport runs the ASGI app to completion before returning a response, so it cannot
# stream an infinite SSE body (it would hang). The event stream is therefore driven by invoking the
# ASGI app directly with a controlled receive/send — still fully in-process, no bind, no GPU — while
# the write that triggers the broadcast goes through the normal ASGITransport client.
async def _sse_capture(name: str, do_after_subscribe: Callable[[], Awaitable[None]]) -> dict:
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": "/api/events", "raw_path": b"/api/events",
        "query_string": f"project={name}".encode(), "headers": [(b"host", b"127.0.0.1:8765")],
        "client": ("127.0.0.1", 123), "server": ("studio.test", 80), "scheme": "http",
    }
    sent: asyncio.Queue[MutableMapping[str, Any]] = asyncio.Queue()
    disconnect = asyncio.Event()
    req_sent = False

    async def receive() -> MutableMapping[str, Any]:
        nonlocal req_sent
        if not req_sent:
            req_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(msg: MutableMapping[str, Any]) -> None:
        await sent.put(msg)

    app_task = asyncio.create_task(studio_app.app(scope, receive, send))
    while True:  # drain to response.start — the handler's hub.subscribe() has run by then
        msg = await asyncio.wait_for(sent.get(), timeout=5)
        if msg["type"] == "http.response.start":
            assert msg["status"] == 200, msg
            break
    await do_after_subscribe()
    event: dict | None = None
    while event is None:
        msg = await asyncio.wait_for(sent.get(), timeout=5)
        if msg["type"] == "http.response.body":
            for part in msg.get("body", b"").decode().split("\n"):
                if part.startswith("data:"):
                    event = json.loads(part[len("data:"):].strip())
    disconnect.set()
    app_task.cancel()
    try:
        await app_task
    except asyncio.CancelledError:
        pass
    return event


def test_sse_emits_event_after_accepted_write() -> None:
    name = f"{_PREFIX}_sse"
    put_status: dict = {}

    async def scenario() -> dict:
        async with _client() as client:
            async def _write() -> None:
                r = await client.put(
                    f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
                put_status["code"] = r.status_code
            return await _sse_capture(name, _write)

    event = _do(scenario)
    assert put_status["code"] == 200
    assert event == {"type": "project", "revision": 1}


# ── apply_ops pure unit (bad op raises) ───────────────────────────────────────────
def test_apply_ops_bad_pointer_raises() -> None:
    proj = project_mod.from_dict(_doc(name="unit"))
    with pytest.raises(Exception):  # noqa: B017 — jsonpatch pointer error on a missing path
        project_mod.apply_ops(proj, [{"op": "replace", "path": "/tracks/9/clips/0/srcOutF", "value": 1}])


def test_apply_ops_valid_op_revalidates() -> None:
    proj = project_mod.from_dict(_doc(name="unit"))
    out = project_mod.apply_ops(proj, [{"op": "replace", "path": "/width", "value": 1280}])
    assert out.width == 1280
