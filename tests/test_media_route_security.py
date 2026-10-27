"""Security and exact-filename contracts for retained source and proxy media routes."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

import config
from scripts import studio_app


async def _get(path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test",
    ) as client:
        return await client.get(path, headers=headers)


@pytest.fixture()
def media_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source = tmp_path / "data" / "source"
    proxy = tmp_path / "outputs" / "proxies"
    source.mkdir(parents=True)
    proxy.mkdir(parents=True)
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", source)
    monkeypatch.setattr(config, "PROXIES_DIR", proxy)
    return source, proxy


def test_source_raw_serves_exact_filename_and_range(media_dirs: tuple[Path, Path]) -> None:
    source, _ = media_dirs
    for filename, content in (("a.mp4", b"source"), ("take.mp4", b"mp4"), ("take.mov", b"mov")):
        (source / filename).write_bytes(content)
        response = asyncio.run(_get(f"/media/source-raw/data/source/{filename}"))
        assert response.status_code == 200
        assert response.content == content
    ranged = asyncio.run(_get("/media/source-raw/data/source/take.mp4", {"Range": "bytes=1-2"}))
    assert ranged.status_code == 206
    assert ranged.content == b"p4"
    assert asyncio.run(_get("/media/source-raw/data/source/a.mov")).status_code == 404
    assert asyncio.run(_get("/media/source-raw/data/source/../../a.mp4")).status_code == 404


def test_source_raw_rejects_existing_unsupported_extension(media_dirs: tuple[Path, Path]) -> None:
    source, _ = media_dirs
    (source / "take.txt").write_bytes(b"not video")
    assert asyncio.run(_get("/media/source-raw/data/source/take.txt")).status_code == 404


@pytest.mark.parametrize("reference", [
    "data/source/%2e%2e/%2e%2e/a.mp4",
    "data%5csource%5ca.mp4",
])
def test_source_raw_rejects_unsafe_paths(media_dirs: tuple[Path, Path], reference: str) -> None:
    source, _ = media_dirs
    (source / "a.mp4").write_bytes(b"source")
    (config.STATE_HOME / "a.mp4").write_bytes(b"outside source")
    assert asyncio.run(_get(f"/media/source-raw/{reference}")).status_code == 404


def test_proxy_serves_only_safe_flat_mp4_names(media_dirs: tuple[Path, Path]) -> None:
    _, proxy = media_dirs
    for filename in ("good.mp4", "good.mov", "good.dotted.mp4", "good name.mp4"):
        (proxy / filename).write_bytes(b"proxy")
    response = asyncio.run(_get("/media/proxy/good.mp4"))
    assert response.status_code == 200
    assert response.content == b"proxy"
    for filename in ("good.mov", "good.dotted.mp4", "good%20name.mp4", "nested/good.mp4",
                     "%2e%2e/good.mp4", "nested%5cgood.mp4"):
        assert asyncio.run(_get(f"/media/proxy/{filename}")).status_code == 404


@pytest.mark.parametrize("route, directory", [
    ("/media/source-raw/data/source/escape.mp4", 0),
    ("/media/proxy/escape.mp4", 1),
])
def test_media_routes_reject_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, media_dirs: tuple[Path, Path],
    route: str, directory: int,
) -> None:
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    link = media_dirs[directory] / "escape.mp4"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:
            raise
        # Windows may deny symlink creation; exercise the same resolved-target containment check.
        link.write_bytes(b"link")
        resolve = Path.resolve

        def resolved_target(path: Path, strict: bool = False) -> Path:
            return resolve(outside, strict=strict) if path == link else resolve(path, strict=strict)

        monkeypatch.setattr(Path, "resolve", resolved_target)
    assert asyncio.run(_get(route)).status_code == 404
