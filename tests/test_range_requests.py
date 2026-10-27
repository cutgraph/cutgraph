"""Bounded Range responses through the asset and video media routes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import perf_counter

import httpx
import pytest

import config
from pipeline import library
from scripts import studio_app


async def _get(path: str, range_header: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test",
    ) as client:
        return await client.get(path, headers={"Range": range_header})


@pytest.fixture()
def media_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, str], bytes]:
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "PROXIES_DIR", tmp_path / "proxies")
    workspace_id = library.create_workspace("Range probe")["id"]
    asset_id = "0123456789abcdef"
    asset_dir = config.WORKSPACES_DIR / workspace_id / "library"
    thumbs_dir = asset_dir / "thumbs"
    thumbs_dir.mkdir(parents=True)
    config.PROXIES_DIR.mkdir()
    payload = bytes(range(256)) * 800
    filename = f"{asset_id}.mp4"
    (asset_dir / filename).write_bytes(payload)
    (thumbs_dir / f"{asset_id}.jpg").write_bytes(payload)
    (config.PROXIES_DIR / "range-probe.mp4").write_bytes(payload)
    (asset_dir / "assets.json").write_text(
        json.dumps({"assets": [{"id": asset_id, "file": filename}]}), encoding="utf-8",
    )
    base = f"/api/workspaces/{workspace_id}/assets/{asset_id}"
    return {
        "asset-file": f"{base}/file",
        "asset-thumb": f"{base}/thumb",
        "proxy": "/media/proxy/range-probe.mp4",
    }, payload


@pytest.mark.parametrize("route", ["asset-file", "asset-thumb", "proxy"])
@pytest.mark.parametrize("range_header", [
    pytest.param("bytes=0-1", id="valid"),
    pytest.param("bytes=" + ",".join(["0-1"] * 2000), id="overlapping"),
    pytest.param("bytes=0-" + "9" * 128, id="huge-end"),
    pytest.param("bytes=" + "9" * 128 + "-", id="huge-start"),
    pytest.param("bytes=invalid", id="malformed"),
    pytest.param("bytes=10-5", id="reversed"),
])
def test_range_response_is_bounded(
    media_routes: tuple[dict[str, str], bytes], route: str, range_header: str,
) -> None:
    routes, payload = media_routes
    started = perf_counter()
    response = asyncio.run(_get(routes[route], range_header))
    elapsed = perf_counter() - started

    assert response.status_code in {200, 206, 400, 416}
    assert elapsed < 2.0
    assert len(response.content) <= len(payload)
    if range_header == "bytes=0-1":
        assert response.status_code == 206
        assert response.content == payload[:2]
        assert response.headers["Content-Range"] == f"bytes 0-1/{len(payload)}"
    elif range_header.startswith("bytes=0-") and range_header.count(",") == 0:
        assert response.status_code == 206
        assert response.content == payload


@pytest.mark.parametrize("route", ["asset-file", "asset-thumb", "proxy"])
def test_many_non_overlapping_ranges_are_bounded(
    media_routes: tuple[dict[str, str], bytes], route: str,
) -> None:
    routes, payload = media_routes
    assert len(payload) >= 200_000
    range_header = "bytes=" + ",".join(f"{offset}-{offset}" for offset in range(0, 20_000, 2))
    started = perf_counter()
    response = asyncio.run(_get(routes[route], range_header))
    elapsed = perf_counter() - started

    assert response.status_code in {200, 206, 400, 416}
    assert len(response.content) <= len(payload) * 11 // 10
    assert elapsed < 1.0
    if route == "proxy":
        assert response.status_code == 416
        assert response.content == b""
        assert response.headers["Content-Range"] == f"bytes */{len(payload)}"
