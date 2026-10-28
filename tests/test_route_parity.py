"""Route-parity tests for the FastAPI studio port (scripts/studio_app.py).

In-process via httpx.ASGITransport — no real bind, no GPU, no ffmpeg. Proves the ported routes
respond with the same shapes/status the stdlib http.server studio did: JSON list endpoints,
hand-rolled Range/206/416 streaming and anonymous local access.

Async requests are driven with asyncio.run so no pytest-asyncio dependency is needed.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from starlette.routing import Route

from studio.app import routes as app_routes
from scripts import studio_app
from studio.routes import editor

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=studio_app.app)
    return httpx.AsyncClient(transport=transport, base_url="http://studio.test")


async def _get(path: str, **kw) -> httpx.Response:
    async with _client() as client:
        return await client.get(path, **kw)


def _do(fn: Callable[[], Awaitable[T]]) -> T:
    return _run(fn())


# ── JSON list endpoints ────────────────────────────────────────────────────────
def test_job_controls_remain() -> None:
    registered = {
        (method, route.path)
        for route in app_routes()
        if isinstance(route, Route)
        for method in route.methods or set()
    }
    assert {
        ("GET", "/api/jobs/{job_id}"),
        ("POST", "/api/jobs/{job_id}/cancel"),
        ("POST", "/api/jobs"),
        ("GET", "/media/compiled/{rest:path}"),
    } <= registered


def test_api_projects_returns_list() -> None:
    r = _do(lambda: _get("/api/projects"))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_jobs_returns_dict() -> None:
    r = _do(lambda: _get("/api/jobs"))
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_editor_shell_reloads_but_hashed_assets_are_immutable(monkeypatch, tmp_path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text("<html>current shell</html>", encoding="utf-8")
    (assets / "index-current.js").write_text("export {}", encoding="utf-8")
    monkeypatch.setattr(editor, "UI_DIST", tmp_path)

    shell = _do(lambda: _get("/editor"))
    asset = _do(lambda: _get("/editor/assets/index-current.js"))

    assert shell.status_code == 200
    assert shell.headers["cache-control"] == "no-store"
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


# ── Range / 206 / 416 streaming ────────────────────────────────────────────────
@pytest.fixture()
def probe_media() -> Iterator[Path]:
    import config

    config.PROXIES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PROXIES_DIR / "zz_parity_probe.mp4"
    path.write_bytes(bytes(range(100)))  # 100 deterministic bytes
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_range_partial_206(probe_media) -> None:
    r = _do(lambda: _get("/media/proxy/zz_parity_probe.mp4", headers={"Range": "bytes=0-1"}))
    assert r.status_code == 206
    assert r.headers["Content-Range"] == "bytes 0-1/100"
    assert r.headers["Content-Length"] == "2"
    assert r.headers["Accept-Ranges"] == "bytes"
    assert r.content == bytes([0, 1])


def test_range_suffix_206(probe_media) -> None:
    r = _do(lambda: _get("/media/proxy/zz_parity_probe.mp4", headers={"Range": "bytes=-5"}))
    assert r.status_code == 206
    assert r.headers["Content-Range"] == "bytes 95-99/100"
    assert r.content == bytes(range(95, 100))


def test_range_unsatisfiable_416(probe_media) -> None:
    r = _do(lambda: _get("/media/proxy/zz_parity_probe.mp4", headers={"Range": "bytes=500-600"}))
    assert r.status_code == 416
    assert r.headers["Content-Range"] == "bytes */100"


def test_full_body_200(probe_media) -> None:
    r = _do(lambda: _get("/media/proxy/zz_parity_probe.mp4"))
    assert r.status_code == 200
    assert r.headers["Accept-Ranges"] == "bytes"
    assert r.headers["Content-Length"] == "100"
    assert r.content == bytes(range(100))


def test_missing_media_404() -> None:
    r = _do(lambda: _get("/media/proxy/zz_definitely_absent.mp4"))
    assert r.status_code == 404


def test_local_routes_allow_anonymous() -> None:
    r = _do(lambda: _get("/api/projects"))
    assert r.status_code == 200


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_docs_endpoints_disabled(path: str) -> None:
    r = _do(lambda: _get(path))
    assert r.status_code == 404
