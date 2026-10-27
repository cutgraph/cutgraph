"""GET /api/transcript/<p> reshape tests (scripts/studio_app.py).

In-process via httpx.ASGITransport (no bind, no GPU). Proves the endpoint flattens the nested
on-disk TranscriptResult into the panel contract {language, duration, words[], segments[]}, resolves
`<p>` as a project (→ speech source stem) OR a raw slug, strips WhisperX's leading space, and 404s a
missing transcript / bad slug.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from typing import TypeVar

import httpx
import pytest

import config
from scripts import studio_app

T = TypeVar("T")

_PREFIX = "zztr"


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _do(fn: Callable[[], Awaitable[T]]) -> T:
    return _run(fn())


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=studio_app.app)
    return httpx.AsyncClient(transport=transport, base_url="http://studio.test")


def _get(path: str) -> httpx.Response:
    async def _f() -> httpx.Response:
        async with _client() as c:
            return await c.get(path)
    return _do(_f)


# A nested TranscriptResult exactly as transcribe.py writes it: `word` carries a WhisperX
# leading space; times are float seconds; two segments so segIdx must increment.
_WORDS_DOC = {
    "language": "en",
    "duration": 3.0,
    "segments": [
        {"start": 0.0, "end": 1.5, "text": "Hello world", "words": [
            {"word": " Hello", "start": 0.1, "end": 0.4, "score": 0.9},
            {"word": " world", "start": 0.5, "end": 0.9, "score": 0.9},
        ]},
        {"start": 1.5, "end": 3.0, "text": "Bye now", "words": [
            {"word": " Bye", "start": 1.6, "end": 1.9, "score": 0.8},
            {"word": " now", "start": 2.0, "end": 2.4, "score": 0.8},
        ]},
    ],
}


def _project_doc(source: str) -> dict:
    return {
        "schemaVersion": 1, "name": "ignored", "fps": 30, "width": 1920, "height": 1080,
        "revision": 0, "transcript": None,
        "tracks": [{"id": "t1", "kind": "speech", "clips": [
            {"id": "c1", "media": {"source": source, "proxy": None},
             "srcInF": 0, "srcOutF": 30, "tlStartF": 0, "gainDb": 0.0},
        ]}],
    }


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def _wipe() -> None:
        for d in (config.TRANSCRIPTS_DIR, config.PROJECTS_DIR):
            if d.exists():
                for f in d.glob(f"{_PREFIX}*"):
                    f.unlink(missing_ok=True)
    config.TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    _wipe()
    yield
    _wipe()


def _write_words(slug: str) -> None:
    (config.TRANSCRIPTS_DIR / f"{slug}.words.json").write_text(
        json.dumps(_WORDS_DOC), encoding="utf-8")


def test_get_by_raw_slug_reshapes() -> None:
    _write_words(f"{_PREFIX}raw")
    r = _get(f"/api/transcript/{_PREFIX}raw")
    assert r.status_code == 200
    body = r.json()
    assert body["language"] == "en"
    assert body["duration"] == 3.0
    assert [w["w"] for w in body["words"]] == ["Hello", "world", "Bye", "now"]
    # No leading space survives the strip.
    assert all(not w["w"].startswith(" ") for w in body["words"])
    # segIdx increments across segments.
    assert [w["segIdx"] for w in body["words"]] == [0, 0, 1, 1]
    # Times preserved as floats.
    assert body["words"][0]["start"] == 0.1
    assert body["words"][0]["end"] == 0.4
    assert isinstance(body["words"][0]["start"], float)
    # Segments carry segIdx + text for paragraphing.
    assert body["segments"][1] == {"segIdx": 1, "start": 1.5, "end": 3.0, "text": "Bye now"}


def test_get_by_project_resolves_speech_source() -> None:
    # Project file present ⇒ resolve to its speech clip's source stem.
    (config.PROJECTS_DIR / f"{_PREFIX}proj.json").write_text(
        json.dumps(_project_doc(f"{_PREFIX}src.mp4")), encoding="utf-8")
    _write_words(f"{_PREFIX}src")
    r = _get(f"/api/transcript/{_PREFIX}proj")
    assert r.status_code == 200
    body = r.json()
    assert [w["w"] for w in body["words"]] == ["Hello", "world", "Bye", "now"]


def test_compound_slug_short_resolves_parent_across_route_stack() -> None:
    """a materialized short lives at a FLAT compound slug `<parent>__<slug>.json` whose
    speech clip keeps the PARENT media verbatim. Every existing route must open it with zero changes —
    `__` survives `_safe`, `/api/project` loads the doc, `/api/transcript` resolves the compound project
    to the parent's source stem (the shared words file), and `/api/peaks` resolves by the parent media
    basename."""
    compound = f"{_PREFIX}par__insight"
    (config.PROJECTS_DIR / f"{compound}.json").write_text(
        json.dumps(_project_doc(f"{_PREFIX}src.mp4")), encoding="utf-8")
    _write_words(f"{_PREFIX}src")
    (config.TRANSCRIPTS_DIR / f"{_PREFIX}src.peaks.json").write_text(
        json.dumps({"peaks": [0, 1, 2], "sampleRate": 100}), encoding="utf-8")

    rp = _get(f"/api/project/{compound}")
    assert rp.status_code == 200
    assert rp.json()["project"]["name"] == "ignored"          # the on-disk doc loaded (not a skeleton)

    rt = _get(f"/api/transcript/{compound}")
    assert rt.status_code == 200
    assert [w["w"] for w in rt.json()["words"]] == ["Hello", "world", "Bye", "now"]

    rk = _get(f"/api/peaks/{_PREFIX}src")                     # peaks key = the parent media basename
    assert rk.status_code == 200
    assert rk.json()["sampleRate"] == 100


def test_missing_transcript_404() -> None:
    r = _get(f"/api/transcript/{_PREFIX}absent")
    assert r.status_code == 404
    assert r.json() == {"error": "no transcript", "code": 'not_found'}


def test_bad_slug_404() -> None:
    # A dot is outside the _safe charset (^[A-Za-z0-9_-]+$) → rejected before any path touch.
    r = _get(f"/api/transcript/{_PREFIX}.bad")
    assert r.status_code == 404
    assert r.json() == {"error": "no transcript", "code": 'not_found'}
