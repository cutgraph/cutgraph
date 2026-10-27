"""Pure payload builders for the in-process agent tools.

SDK-free (stdlib only): the `@tool` handlers in `scripts/agent_host.py` are trivial adapters that call
these and hand the returned string straight back to the model. Every builder returns a COMPACT JSON
string (`json.dumps(x, separators=(",",":"))` under ~2 KB) — an in-process SDK tool has no
`structuredContent` channel, so the payload IS the text block, and the compactness cap is what keeps
the agent's context small.

Bounded, never-crashing tools. No builder asserts/excepts on payload size; each returns valid
compact JSON that fits BY CONSTRUCTION: `project_skeleton` is a summarized overview (counts + per-track
first/last clip) with per-track pagination for full addressability; `read_transcript` paginates with a
`nextCursor`; `notes_tail` returns the newest entries that fit with `{truncated,totalLines}`;
`find_word` is size-guarded. Word-level transcript is NEVER dumped without an explicit bounded range.
"""
from __future__ import annotations

import json
import string
from collections.abc import Callable
from typing import Any

_MAX_CHARS = 2048
_TEXT_CAP = 40  # skeleton/segment text is truncated to this many chars to stay addressable, not verbose


def _dumps(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _fits(payload: object) -> bool:
    return len(_dumps(payload)) < _MAX_CHARS


def _truncate(text: str, cap: int = _TEXT_CAP) -> str:
    text = text or ""
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _pack(base: dict, key: str, seq: list, start: int, render: Callable[[Any, int], dict],
          *, stop: "int | None" = None) -> dict:
    """Greedily fill `base[key]` from `seq[start:stop]` while the compact dump stays < 2 KB, setting
    `nextCursor` (an absolute index into `seq`) when more remains. At least one entry is always
    included (skeleton/segment entries are small by construction), so a huge sequence still returns
    valid, bounded JSON instead of raising."""
    n = len(seq) if stop is None else min(stop, len(seq))
    start = max(0, start)
    out: dict = dict(base)
    chosen: list = []
    i = start
    while i < n:
        chosen.append(render(seq[i], i))
        trial: dict = {**out, key: chosen}
        if i + 1 < n:
            trial["nextCursor"] = i + 1
        if not _fits(trial) and len(chosen) > 1:
            chosen.pop()
            return {**out, key: chosen, "nextCursor": i}
        i += 1
    return {**out, key: chosen}


def _fit(base: dict, key: str, rendered: list) -> dict:
    """Trim an already-capped list (hits/labels) from the end until it fits the budget."""
    chosen = list(rendered)
    while len(chosen) > 1 and not _fits({**base, key: chosen}):
        chosen.pop()
    return {**base, key: chosen}


# ── get_project: a bounded, addressable skeleton ──────────────────────────────────
def _clip_skeleton(c: dict) -> dict:
    d: dict = {
        "id": c.get("id"),
        "srcInF": c.get("srcInF"),
        "srcOutF": c.get("srcOutF"),
        "tlStartF": c.get("tlStartF"),
    }
    anchor = c.get("anchor")
    if isinstance(anchor, dict):
        d["anchor"] = anchor.get("mode")
    return d


def _item_skeleton(i: dict) -> dict:
    d: dict = {
        "id": i.get("id"),
        "text": _truncate(i.get("text", "")),
        "tlStartF": i.get("tlStartF"),
        "durF": i.get("durF"),
    }
    anchor = i.get("anchor")
    if isinstance(anchor, dict):
        d["anchor"] = anchor.get("mode")
    return d


def _track_seq(t: dict) -> "tuple[str, list, Callable[[dict], dict]]":
    if t.get("kind") == "text":
        return "items", t.get("items", []), _item_skeleton
    return "clips", t.get("clips", []), _clip_skeleton


def _timeline_len(tracks: list) -> int:
    """Timeline length in frames = the last frame any clip/item occupies (for orientation)."""
    end = 0
    for t in tracks:
        _key, seq, _render = _track_seq(t)
        for e in seq:
            start = e.get("tlStartF") or 0
            span = (e.get("durF") if e.get("durF") is not None
                    else (e.get("srcOutF") or 0) - (e.get("srcInF") or 0))
            end = max(end, int(start) + int(span or 0))
    return end


def project_skeleton(doc: dict, revision: int, *, track: "str | None" = None, cursor: int = 0) -> str:
    """The addressable skeleton the agent patches — bounded by construction.

    Default (no `track`): a compact OVERVIEW — revision/name/fps/dims/duration + per-track
    `{id,kind,count,first,last}`. Tracks and clips keep DOC ORDER so a JSON-Pointer index is the array
    position (track index = position in `tracks`; clip index = `cursor` + position on a track page).
    With `track=<id>`: a paginated page of that track's full clip/item list `{...,clips|items,cursor,
    nextCursor?}` so every middle element stays addressable without ever exceeding the budget.
    """
    tracks = doc.get("tracks", [])
    if track is not None:
        return _dumps(_track_page(revision, tracks, track, int(cursor or 0)))
    return _dumps(_overview(doc, revision, tracks))


def _overview(doc: dict, revision: int, tracks: list) -> dict:
    summary: list[dict] = []
    for t in tracks:
        _key, seq, render = _track_seq(t)
        entry: dict = {"id": t.get("id"), "kind": t.get("kind"), "count": len(seq)}
        if seq:
            entry["first"] = render(seq[0])
            if len(seq) > 1:
                entry["last"] = render(seq[-1])
        summary.append(entry)
    out = {
        "revision": revision,
        "name": doc.get("name"),
        "fps": doc.get("fps"),
        "width": doc.get("width"),
        "height": doc.get("height"),
        "durationF": _timeline_len(tracks),
        "tracks": summary,
    }
    if _fits(out):
        return out
    # Pathological (very many tracks): drop the sample clips; the counts-only overview is tiny and the
    # full clip lists remain reachable per-track via pagination.
    for entry in out["tracks"]:
        entry.pop("first", None)
        entry.pop("last", None)
    return out


def _track_page(revision: int, tracks: list, track_id: str, cursor: int) -> dict:
    t = next((t for t in tracks if t.get("id") == track_id), None)
    if t is None:
        return {"revision": revision, "error": f"no such track: {track_id}"}
    key, seq, render = _track_seq(t)
    base = {"revision": revision, "track": track_id, "kind": t.get("kind"),
            "count": len(seq), "cursor": max(0, cursor)}
    return _pack(base, key, seq, cursor, lambda e, _i: render(e))


# ── read_transcript ──────────────────────────────────────────────────────────────
def read_transcript(
    tdoc: dict, granularity: str = "segments", range_: "tuple[int, int] | None" = None,
    cursor: int = 0,
) -> str:
    """Compact transcript view. `tdoc` is the reshaped panel contract (flat `words` + `segments`).

    - "segments" (default): paginated `{segments:[{i,start,end,text}],nextCursor?}` sized to the budget.
    - "words": ONLY within a bounded `range_=(startWordIdx, endWordIdx)`; without a range this refuses.
      The window is itself paginated so an over-wide range still returns bounded JSON (never crashes).
    """
    if granularity == "words":
        if range_ is None:
            return _dumps({"error": "words granularity requires a bounded range (startIdx,endIdx)"})
        start, end = int(range_[0]), int(range_[1])
        words = tdoc.get("words", [])
        start = max(0, start)
        end = min(end, len(words))
        base = {"granularity": "words", "range": [start, end]}
        page = _pack(base, "words", words, start,
                     lambda w, i: {"i": i, "w": w.get("w", ""), "start": w.get("start")}, stop=end)
        return _dumps(page)
    segments = tdoc.get("segments", [])
    base = {"granularity": "segments"}
    page = _pack(base, "segments", segments, int(cursor or 0),
                 lambda s, _i: {"i": s.get("segIdx"), "start": s.get("start"), "end": s.get("end"),
                                "text": _truncate(s.get("text", ""), 80)})
    return _dumps(page)


# ── find_word (normalized, word-boundary matching) ────────────────────────────────
_STRIP = string.punctuation


def _norm(token: str) -> str:
    """Casefold + strip surrounding punctuation so a WhisperX token like 'you,' or '"Hello"' matches
    the plain phrase word. Interior apostrophes/hyphens survive ('don't', 'well-made')."""
    return (token or "").casefold().strip(_STRIP)


def find_word(tdoc: dict, phrase: str, fps: int, cap: int = 20) -> str:
    """Locate a phrase in the transcript: up to `cap` hits `{wordIdx,endWordIdx,tSec,frame,context}`,
    matched on NORMALIZED word boundaries (casefold + strip punctuation, whole-token equality — never
    substring), so 'cut' no longer hits 'haircut'/'executed' and 'thank you' matches ['thank','you,'].
    Size-guarded: the hit list is trimmed to stay within the budget."""
    words = tdoc.get("words", [])
    tokens = [n for t in (phrase or "").split() if (n := _norm(t))]
    if not tokens:
        return _dumps({"phrase": phrase, "hits": []})
    normwords = [_norm(w.get("w") or "") for w in words]
    span = len(tokens)
    hits: list[dict] = []
    for i in range(len(words) - span + 1):
        if normwords[i:i + span] != tokens:
            continue
        w = words[i]
        t_sec = w.get("start")
        hits.append({
            "wordIdx": i,
            "endWordIdx": i + span - 1,
            "tSec": t_sec,
            "frame": int(round(t_sec * fps)) if isinstance(t_sec, (int, float)) else None,
            "context": " ".join((words[j].get("w") or "")
                                 for j in range(max(0, i - 2), min(len(words), i + span + 2))),
        })
        if len(hits) >= cap:
            break
    return _dumps(_fit({"phrase": phrase}, "hits", hits))


def preference_summary(payload: dict) -> str:
    """Compact local editorial outcomes; deliberately excludes media paths, frames, and transcript text."""
    events = payload.get("events") if isinstance(payload, dict) else []
    return _dumps(_fit({"scope": payload.get("scope"), "project": payload.get("project"),
                        "channel": payload.get("channel")}, "events", events if isinstance(events, list) else []))


# ── channel profile ───────────────────────────────────────────────────
def channel_profile(channels_doc: dict, channel: str | None) -> str:
    """Compact channel profile the planner matches its voice to — voice/audience/accentColor only
    (NOT the full captionStyle, which the caption engine resolves). `{available:false}` when the
    project has no channel or channels.json lacks it, so the planner degrades cleanly. Under budget."""
    channels = channels_doc.get("channels", {}) if isinstance(channels_doc, dict) else {}
    prof = channels.get(channel) if channel else None
    if not isinstance(prof, dict):
        return _dumps({"available": False, "reason": "no channel profile (set project.channel)"})
    return _dumps({
        "available": True,
        "channel": channel,
        "name": prof.get("name"),
        "voice": prof.get("voice"),
        "audience": prof.get("audience"),
        "accentColor": prof.get("accentColor"),
    })


# ── echoes ──────────────────────────────────────────────────────────────────────────


def options_labels(kind: str, options: list) -> str:
    """Compact echo of a judge/choose gallery: the option LABELS only. The full option
    payload — descriptions and `previewRef` video links — rides the `options` SSE event out to the UI
    gallery and is NEVER fed back into the agent's context; the agent only needs to know which labels it
    offered so it can await the human's pick as its next turn (it never one-shots the aesthetic)."""
    labels = [str(o.get("label", "")) for o in (options or []) if isinstance(o, dict)]
    return _dumps(_fit({"kind": kind}, "labels", labels))


def notes_tail(text: str) -> str:
    """The NOTES.md the agent reads back as long-term memory — the NEWEST entries that fit the budget
    (notes append newest-LAST) with `{truncated,totalLines}` so it knows older decisions exist.
    Returns valid compact JSON; NEVER a mid-value slice of a JSON string (the old `dumps(...)[:2000]`
    bug produced invalid JSON AND dropped the newest decisions)."""
    lines = (text or "").splitlines()
    total = len(lines)
    kept: list[str] = []
    for line in reversed(lines):
        trial = [line, *kept]
        payload = {"notes": "\n".join(trial), "truncated": len(trial) < total, "totalLines": total}
        if not _fits(payload) and kept:
            break
        kept = trial
    notes_str = "\n".join(kept)
    payload = {"notes": notes_str, "truncated": len(kept) < total, "totalLines": total}
    if not _fits(payload):
        # A single newest entry alone overflows: cap the STRING VALUE (still valid JSON, still the tail).
        head = len(_dumps({"notes": "", "truncated": True, "totalLines": total}))
        budget = max(0, _MAX_CHARS - head - 8)
        payload = {"notes": notes_str[-budget:], "truncated": True, "totalLines": total}
    return _dumps(payload)
