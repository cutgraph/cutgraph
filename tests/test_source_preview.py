"""A video's first open: `/media/source/<slug>` never serves a half-written proxy.

Until the source's `<stem>_540.mp4` scrub proxy exists, the route starts its one encode in the
background and redirects to the original file; `GET /api/proxy/<slug>` reports the build. The encode
writes beside the proxy and renames it into place, so the proxy is only ever absent or complete.
"""
from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from pipeline import encoder_ladder, proxy
from scripts import studio_app
from studio import compute
from studio import state as studio_state

T = TypeVar("T")

ORIGINAL = b"original source bytes " * 64
FIRST_HALF, SECOND_HALF = b"proxy-head " * 32, b"proxy-tail " * 32


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


@pytest.fixture()
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(encoder_ladder, "probe_encoders", lambda _: frozenset({"h264_nvenc", "libx264"}))
    data = tmp_path / "data" / "source"
    data.mkdir(parents=True)
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PROXIES_DIR", tmp_path / "outputs" / "proxies")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(studio_state, "_PROXY_WAITERS", {})
    monkeypatch.setattr(studio_state, "_PROXY_FAILED", set())
    monkeypatch.setattr(studio_state, "_BACKGROUND_THREADS", set())
    monkeypatch.setattr(studio_state, "_BACKGROUND_STOP", threading.Event())
    monkeypatch.setattr(proxy, "_probe_stream", lambda _src: {
        "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "pix_fmt": "yuv420p", "color_transfer": "bt709"})
    clip = data / "clip.mp4"
    clip.write_bytes(ORIGINAL)
    return clip


class _SlowEncode:
    """Fake ffmpeg that writes half its output, waits to be released, then writes the rest."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.encodes: list[list[str]] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, cmd, **_kwargs):  # noqa: ANN001, ANN204 - subprocess.run double
        self.encodes.append(list(cmd))
        out = Path(cmd[-1])
        out.write_bytes(FIRST_HALF)
        self.started.set()
        assert self.release.wait(5)
        if self.fail:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="Conversion failed!")
        with out.open("ab") as fh:
            fh.write(SECOND_HALF)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _settle() -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with studio_state._BACKGROUND_LOCK:
            if not studio_state._PROXY_WAITERS and not studio_state._BACKGROUND_THREADS:
                return
        time.sleep(0.01)
    raise AssertionError("the proxy encode never finished")


def test_concurrent_first_requests_never_see_a_partial_proxy_and_encode_once(
    source: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    encode = _SlowEncode()
    monkeypatch.setattr(proxy.subprocess, "run", encode)

    async def first_open() -> list[httpx.Response]:
        async with _client() as client:
            return list(await asyncio.gather(*(client.get("/media/source/clip") for _ in range(12))))

    responses = _run(first_open())
    assert encode.started.wait(5)

    async def during() -> tuple[list[httpx.Response], httpx.Response, httpx.Response, httpx.Response]:
        async with _client() as client:
            again = list(await asyncio.gather(*(client.get("/media/source/clip") for _ in range(6))))
            state = await client.get("/api/proxy/clip")
            partial = await client.get("/media/proxy/clip_540.mp4")
            original = await client.get("/media/source/clip", follow_redirects=True)
            return again, state, partial, original

    again, state, partial, original = _run(during())
    for response in responses + again:
        assert response.status_code == 307
        assert response.headers["location"] == "/media/source-raw/data/source/clip.mp4"
        assert response.headers["cache-control"] == "no-store"
    assert state.json() == {"state": "building"}
    assert partial.status_code == 404
    assert original.status_code == 200 and original.content == ORIGINAL
    assert len(encode.encodes) == 1

    encode.release.set()
    _settle()

    async def after() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with _client() as client:
            return (
                await client.get("/media/source/clip"),
                await client.get("/media/source/clip", headers={"Range": "bytes=0-9"}),
                await client.get("/api/proxy/clip"),
            )

    served, ranged, state = _run(after())
    assert served.status_code == 200 and served.content == FIRST_HALF + SECOND_HALF
    assert ranged.status_code == 206 and ranged.content == FIRST_HALF[:10]
    assert state.json() == {"state": "ready"}
    assert len(encode.encodes) == 1
    assert sorted(p.name for p in config.PROXIES_DIR.iterdir()) == ["clip_540.mp4"]
    assert " ".join(encode.encodes[0]) + "\n" in config.RUN_LOG.read_text(encoding="utf-8")


def test_an_existing_proxy_is_reused_without_encoding(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config.PROXIES_DIR.mkdir(parents=True)
    (config.PROXIES_DIR / "clip_540.mp4").write_bytes(b"made at upload")
    monkeypatch.setattr(compute.proxy_stage, "make_proxy", lambda *_a, **_k: pytest.fail("re-encoded a finished proxy"))

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            return await client.get("/media/source/clip"), await client.get("/api/proxy/clip")

    served, state = _run(scenario())
    assert served.status_code == 200 and served.content == b"made at upload"
    assert state.json() == {"state": "ready"}


def test_a_failed_encode_leaves_no_file_and_is_not_retried(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    encode = _SlowEncode(fail=True)
    encode.release.set()
    monkeypatch.setattr(proxy.subprocess, "run", encode)

    async def first() -> httpx.Response:
        async with _client() as client:
            return await client.get("/media/source/clip")

    assert _run(first()).status_code == 307
    _settle()

    async def later() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            return await client.get("/media/source/clip"), await client.get("/api/proxy/clip")

    served, state = _run(later())
    assert served.status_code == 307
    assert state.json() == {"state": "failed"}
    assert len(encode.encodes) == 1
    assert list(config.PROXIES_DIR.iterdir()) == []


def test_an_unknown_source_is_not_found(source: Path) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            return await client.get("/media/source/nope"), await client.get("/api/proxy/nope")

    media, state = _run(scenario())
    assert media.status_code == 404
    assert state.status_code == 404
