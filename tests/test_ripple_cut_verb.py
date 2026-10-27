"""POST /api/verbs/ripple_cut/<p> stateless compute-verb tests (scripts/studio_app.py).

In-process via httpx.ASGITransport (no bind, no GPU). The verb applies edits.apply_cuts to the POSTED
project doc and returns {project: nextDoc, warnings} WITHOUT committing (no revision bump, no SSE, no
project file written) — the client persists nextDoc through the revision-gated saveProject PUT.
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

_PREFIX = "zzrc"
_SRC = f"data/source/{_PREFIX}src.mp4"       # speech source; its words file drives word-anchor recompute
_SLUG = f"{_PREFIX}src"          # words-file stem
_PROJ = f"{_PREFIX}proj"         # URL slug (the verb reads the POSTED doc, not a project file)

# Flat words at frames [0, 30, 60, 90] (fps=30): start-seconds 0.0/1.0/2.0/3.0.
_WORDS_DOC = {
    "language": "en", "duration": 4.0,
    "segments": [
        {"start": 0.0, "end": 2.5, "text": "a b c", "words": [
            {"word": " a", "start": 0.0, "end": 0.4, "score": 0.9},
            {"word": " b", "start": 1.0, "end": 1.4, "score": 0.9},
            {"word": " c", "start": 2.0, "end": 2.4, "score": 0.9},
        ]},
        {"start": 2.5, "end": 4.0, "text": "d", "words": [
            {"word": " d", "start": 3.0, "end": 3.4, "score": 0.9},
        ]},
    ],
}


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _do(fn: Callable[[], Awaitable[T]]) -> T:
    return _run(fn())


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=studio_app.app)
    return httpx.AsyncClient(transport=transport, base_url="http://studio.test")


def _post(path: str, body: object) -> httpx.Response:
    async def _f() -> httpx.Response:
        async with _client() as c:
            return await c.post(path, json=body)
    return _do(_f)


def _cut(cuts: list[dict], doc: dict, slug: str = _PROJ) -> httpx.Response:
    return _post(f"/api/verbs/ripple_cut/{slug}", {"project": doc, "cuts": cuts})


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
    (config.TRANSCRIPTS_DIR / f"{_SLUG}.words.json").write_text(
        json.dumps(_WORDS_DOC), encoding="utf-8")
    yield
    _wipe()


def _base_doc(tracks: list[dict]) -> dict:
    return {
        "schemaVersion": 1, "name": "ignored", "fps": 30, "width": 1920, "height": 1080,
        "revision": 1, "transcript": None, "tracks": tracks,
    }


def _speech_clip(cid: str, src_in: int, src_out: int, tl: int, source: str = _SRC) -> dict:
    return {"id": cid, "media": {"source": source, "proxy": None},
            "srcInF": src_in, "srcOutF": src_out, "tlStartF": tl, "gainDb": 0.0}


def _broll(cid: str, word_idx: int, tl: int) -> dict:
    """A video-track b-roll clip anchored to a transcript word of the speech source."""
    return {"id": cid, "media": {"source": f"data/source/{_PREFIX}broll.mp4", "proxy": None},
            "srcInF": 0, "srcOutF": 30, "tlStartF": tl, "gainDb": 0.0,
            "anchor": {"mode": "word", "ref": {"media": _SRC, "wordIdx": word_idx}, "offsetF": 0}}


def _speech(clip: dict) -> list[dict]:
    return _base_doc([{"id": "s", "kind": "speech", "clips": [clip]}])["tracks"]


def _clips(body: dict, track_kind: str) -> list[dict]:
    track = next(t for t in body["project"]["tracks"] if t["kind"] == track_kind)
    return track["clips"]


def test_single_cut_removes_range_and_shifts_later_clips() -> None:
    doc = _base_doc([{"id": "s", "kind": "speech", "clips": [
        _speech_clip("c1", 0, 120, 0),
        _speech_clip("c2", 0, 30, 120, source=f"data/source/{_PREFIX}tail.mp4"),
    ]}])
    r = _cut([{"startF": 30, "endF": 60}], doc)
    assert r.status_code == 200
    body = r.json()
    clips = _clips(body, "speech")
    # c1 splits at the join; the right piece starts at the cut and advances past the removed span.
    right = next(c for c in clips if c["id"] == "c1-r30")
    assert (right["srcInF"], right["srcOutF"], right["tlStartF"]) == (60, 120, 30)
    # The trailing clip ripples left by the 30-frame removal.
    assert next(c for c in clips if c["id"] == "c2")["tlStartF"] == 90
    assert body["warnings"] == []
    # Statelessness: no revision bump, no project file written.
    assert body["project"]["revision"] == 1
    assert not (config.PROJECTS_DIR / f"{_PROJ}.json").exists()


def test_word_anchored_broll_survives_and_moves() -> None:
    doc = _base_doc([
        {"id": "s", "kind": "speech", "clips": [_speech_clip("c1", 0, 120, 0)]},
        {"id": "v", "kind": "video", "clips": [_broll("b1", word_idx=2, tl=999)]},
    ])
    r = _cut([{"startF": 0, "endF": 30}], doc)  # trims the head; word 2 (frame 60) survives
    assert r.status_code == 200
    body = r.json()
    assert body["warnings"] == []
    # word 2 now sits at timeline frame 30 (clip srcInF advanced 0→30, tlStart 0 + (60-30)).
    assert _clips(body, "video")[0]["tlStartF"] == 30


def test_cut_over_anchor_word_orphans_it() -> None:
    doc = _base_doc([
        {"id": "s", "kind": "speech", "clips": [_speech_clip("c1", 0, 120, 0)]},
        {"id": "v", "kind": "video", "clips": [_broll("b1", word_idx=0, tl=500)]},
    ])
    r = _cut([{"startF": 0, "endF": 30}], doc)  # removes frame 0 = word 0's source frame
    assert r.status_code == 200
    body = r.json()
    assert {"type": "orphaned-anchor", "itemId": "b1"} in body["warnings"]
    assert _clips(body, "video")[0]["tlStartF"] == 500  # left time-pinned


def test_batch_equals_sequential_right_to_left() -> None:
    project = _base_doc(_speech(_speech_clip("c1", 0, 120, 0)))
    a, b = {"startF": 10, "endF": 20}, {"startF": 50, "endF": 60}
    batch = _cut([a, b], project).json()["project"]
    # apply_cuts folds right-to-left: the higher-startF range first, then the lower.
    step1 = _cut([b], project).json()["project"]
    step2 = _cut([a], step1).json()["project"]
    assert batch == step2


def test_bad_slug_404() -> None:
    doc = _base_doc(_speech(_speech_clip("c1", 0, 120, 0)))
    r = _cut([{"startF": 0, "endF": 30}], doc, slug=f"{_PREFIX}.bad")
    assert r.status_code == 404


def test_missing_cuts_400() -> None:
    doc = _base_doc(_speech(_speech_clip("c1", 0, 120, 0)))
    r = _post(f"/api/verbs/ripple_cut/{_PROJ}", {"project": doc})
    assert r.status_code == 400


def test_missing_project_400() -> None:
    r = _post(f"/api/verbs/ripple_cut/{_PROJ}", {"cuts": [{"startF": 0, "endF": 30}]})
    assert r.status_code == 400


def _restore(ranges: list[dict], doc: dict) -> httpx.Response:
    return _post(f"/api/verbs/ripple_restore/{_PROJ}", {"project": doc, "ranges": ranges})


def test_restore_is_the_inverse_of_a_cut_and_brings_anchors_back() -> None:
    doc = _base_doc([
        {"id": "s", "kind": "speech", "clips": [_speech_clip("c1", 0, 120, 0)]},
        {"id": "v", "kind": "video", "clips": [_broll("b1", word_idx=2, tl=60)]},
    ])
    cut = _cut([{"startF": 30, "endF": 90}], doc).json()
    assert {"type": "orphaned-anchor", "itemId": "b1"} in cut["warnings"]
    r = _restore([{"media": _SRC, "srcStartF": 30, "srcEndF": 90}], cut["project"])
    assert r.status_code == 200
    body = r.json()
    assert _clips(body, "speech") == _clips({"project": _base_doc(doc["tracks"])}, "speech")
    assert _clips(body, "video")[0]["tlStartF"] == 60
    assert body["warnings"] == []
    assert not (config.PROJECTS_DIR / f"{_PROJ}.json").exists()


def test_restore_missing_ranges_400() -> None:
    doc = _base_doc(_speech(_speech_clip("c1", 0, 120, 0)))
    assert _post(f"/api/verbs/ripple_restore/{_PROJ}", {"project": doc}).status_code == 400
    assert _restore([{"media": _SRC, "srcStartF": 30, "srcEndF": 30}], doc).status_code == 400
