"""Waveform-peaks tests (scripts/make_peaks.py + GET /api/peaks).

Import-light and numpy-free: the reduction is stdlib `array`-based, and the ffmpeg pipe is mocked
with a recorder that returns a known s16le buffer (pattern: test_proxy.py). A real-ffmpeg pass is
guarded behind shutil.which("ffmpeg").
"""
from __future__ import annotations

import array
import asyncio
import json
import shutil
import subprocess
from collections.abc import Awaitable, Iterator
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from scripts import make_peaks
from scripts import studio_app

T = TypeVar("T")
_HAS_FFMPEG = shutil.which("ffmpeg") is not None
_WIN = config.PEAKS_SAMPLE_RATE * config.PEAKS_WINDOW_MS // 1000  # 400 samples per window


def _pcm(samples: list[int]) -> bytes:
    return array.array("h", samples).tobytes()


# ── the pure reduction ───────────────────────────────────────────────────────────
def test_reduce_min_max_per_window() -> None:
    # One full window with a known min/max, then a short trailing window.
    win = [0] * _WIN
    win[10], win[20] = -5000, 9000
    peaks = make_peaks._reduce(_pcm(win + [123, -456]))
    assert peaks[0] == [-5000, 9000]
    assert peaks[1] == [-456, 123]        # the tail window is min/max of the leftover samples


def test_reduce_ignores_stray_trailing_byte() -> None:
    buf = _pcm([100, 200]) + b"\x01"      # 5 bytes: 2 int16 + 1 stray
    assert make_peaks._reduce(buf) == [[100, 200]]


def test_reduce_empty_buffer() -> None:
    assert make_peaks._reduce(b"") == []


# ── make_peaks end to end with a mocked ffmpeg pipe ──────────────────────────────
def test_make_peaks_writes_json_and_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    monkeypatch.setattr(config, "RUN_LOG", log)
    buf = _pcm([1000, -2000] + [0] * (_WIN - 2))

    calls: list[list[str]] = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, buf, b"")

    monkeypatch.setattr(make_peaks.subprocess, "run", _fake_run)
    out = tmp_path / "clip.peaks.json"
    make_peaks.make_peaks(Path("clip.mp4"), out)

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["sampleRate"] == config.PEAKS_SAMPLE_RATE
    assert doc["windowMs"] == config.PEAKS_WINDOW_MS
    assert doc["peaks"] == [[-2000, 1000]]
    # ffmpeg argv: mono s16le pipe, logged to run.log before running.
    from pipeline.runtime_tools import tool_path

    assert calls[0][:1] == [tool_path("ffmpeg")] and "s16le" in calls[0] and calls[0][-1] == "-"
    assert "ffmpeg" in log.read_text(encoding="utf-8")


@pytest.mark.skipif(not _HAS_FFMPEG, reason="needs ffmpeg on PATH")
def test_make_peaks_real_ffmpeg(tmp_path: Path) -> None:
    src = tmp_path / "sine.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src)],
        check=True, capture_output=True, text=True,
    )
    out = make_peaks.make_peaks(src, tmp_path / "sine.peaks.json")
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["peaks"] and all(len(p) == 2 and p[0] <= p[1] for p in doc["peaks"])


# ── GET /api/peaks route ─────────────────────────────────────────────────────────
def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _get(path: str) -> httpx.Response:
    async def _f() -> httpx.Response:
        transport = httpx.ASGITransport(app=studio_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as c:
            return await c.get(path)
    return _run(_f())


@pytest.fixture()
def _peaks_file() -> Iterator[str]:
    slug = "zzpeaks"
    config.TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.TRANSCRIPTS_DIR / f"{slug}.peaks.json"
    path.write_text(json.dumps({"sampleRate": 8000, "windowMs": 50, "peaks": [[-1, 1]]}), encoding="utf-8")
    try:
        yield slug
    finally:
        path.unlink(missing_ok=True)


def test_get_peaks_serves_sidecar(_peaks_file: str) -> None:
    r = _get(f"/api/peaks/{_peaks_file}")
    assert r.status_code == 200
    assert r.json()["peaks"] == [[-1, 1]]


def test_get_peaks_missing_404() -> None:
    r = _get("/api/peaks/zz_absent_peaks")
    assert r.status_code == 404
    assert r.json() == {"error": "no peaks", "code": 'not_found'}


def test_get_peaks_bad_slug_404() -> None:
    r = _get("/api/peaks/bad.slug")
    assert r.status_code == 404
