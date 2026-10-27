"""The Story drawer's word-anchored insert, server side.

A mirror of `ui/src/v3/story/drawer/insert.ts` (`insertAsset`) and `drawer/library.ts` (`assetTab`)
so a co-pilot B-roll proposal lands exactly where a drag from the drawer would: a video or image on
a video track, audio on an audio track (music ducked), anchored to the word so a later transcript
cut reflows it. Plain project dicts in and out, never mutated; `tests/fixtures/story_insert_cases.json`
is asserted by both this module's test and the UI's, so the two cannot drift.
"""
from __future__ import annotations

import math
import re

from pipeline import timebase

BROLL_DEFAULT_F = 150
MUSIC_DUCK_DB = -12
SFX_MAX_S = 10
_SFX_TAG = re.compile(r"^(sfx|sounds?|sound effects?)$", re.IGNORECASE)
_MUSIC_TAG = re.compile(r"^music$", re.IGNORECASE)


def asset_tab(asset: dict) -> str:
    """broll | music | sfx. Audio without a music/sfx tag is sorted by length: a sting is short."""
    if asset.get("kind") != "audio":
        return "broll"
    tags = asset.get("tags") or []
    if any(_SFX_TAG.match(t) for t in tags):
        return "sfx"
    if any(_MUSIC_TAG.match(t) for t in tags):
        return "music"
    duration = asset.get("durationS")
    return "sfx" if isinstance(duration, (int, float)) and duration < SFX_MAX_S else "music"


def workspace_media_ref(workspace_id: str, file: str) -> str:
    return f"workspaces/{workspace_id}/library/{file}"


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def is_timed(word: dict) -> bool:
    start, end = word.get("start"), word.get("end")
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end))


def resolve_timed(words: list[dict]) -> list[int]:
    """cutmap.resolveTimed: each word's own index when timed, else its nearest timed neighbour
    (previous preferred, then next); -1 only when no word is timed."""
    timed = [is_timed(w) for w in words]
    out = [-1] * len(words)
    prev = -1
    for i, ok in enumerate(timed):
        if ok:
            prev = i
        out[i] = i if ok else prev
    nxt = -1
    for i in range(len(words) - 1, -1, -1):
        if timed[i]:
            nxt = i
        elif out[i] == -1:
            out[i] = nxt
    return out


def speech_clips(doc: dict) -> list[dict]:
    return next((t.get("clips") or [] for t in doc.get("tracks", []) if t.get("kind") == "speech"), [])


def src_to_timeline(doc: dict, media: str, src_f: int) -> int | None:
    for c in speech_clips(doc):
        if c["media"]["source"] == media and c["srcInF"] <= src_f < c["srcOutF"]:
            return c["tlStartF"] + (src_f - c["srcInF"])
    return None


def word_anchor(doc: dict, words: list[dict], media: str, word_idx: int) -> tuple[dict, int] | None:
    """(anchor, tlStartF) for "starts on word `word_idx`", or None when the word is cut/unknown."""
    eff = resolve_timed(words)
    if not 0 <= word_idx < len(eff) or eff[word_idx] < 0:
        return None
    timed = eff[word_idx]
    tl = src_to_timeline(doc, media, timebase.sec_to_frame(words[timed]["start"], doc["fps"]))
    if tl is None:
        return None
    return {"mode": "word", "ref": {"media": media, "wordIdx": timed}, "offsetF": 0}, tl


def _speech_end_f(doc: dict) -> int:
    return max((c["tlStartF"] + c["srcOutF"] - c["srcInF"] for c in speech_clips(doc)), default=0)


def _element_ids(doc: dict) -> set[str]:
    ids: set[str] = set()
    for t in doc.get("tracks", []):
        ids.add(t["id"])
        ids.update(c["id"] for c in t.get("clips") or [])
        ids.update(i["id"] for i in t.get("items") or [])
    return ids


def _unique_id(ids: set[str], base: str, sep: str) -> str:
    stem = base[:56] or "item"
    candidate, n = stem, 2
    while candidate in ids:
        candidate = f"{stem}{sep}{n}"
        n += 1
    return candidate


def _slug(name: str) -> str:
    s = re.sub(r"\.[^.]+$", "", name)
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")[:56] or "item"


def _dur(element: dict) -> int:
    return element["durF"] if "durF" in element else element["srcOutF"] - element["srcInF"]


def _span_open(track: dict, start: int, dur: int) -> bool:
    end = start + dur
    return all(end <= e["tlStartF"] or start >= e["tlStartF"] + _dur(e)
               for e in [*(track.get("clips") or []), *(track.get("items") or [])])


def _place(doc: dict, kind: str, clip: dict, dur_f: int) -> dict:
    """First `kind` track whose span is free, else a new layer: new footage goes above the other
    footage but under every title and graphic; audio goes last."""
    tracks = doc.get("tracks", [])
    start = clip["tlStartF"]
    open_track = next((t for t in tracks if t.get("kind") == kind and start >= 0 and dur_f > 0
                       and _span_open(t, start, dur_f)), None)

    def add(t: dict) -> dict:
        clips: list[dict] = [*(t.get("clips") or []), clip]
        return {**t, "clips": sorted(clips, key=lambda c: c["tlStartF"])}

    if open_track is not None:
        return {**doc, "tracks": [add(t) if t is open_track else t for t in tracks]}
    layer = add({"id": _unique_id(_element_ids(doc), kind, "_"), "kind": kind})
    last_footage = max((i for i, t in enumerate(tracks) if t.get("kind") in ("speech", "video")), default=-1)
    at = last_footage + 1 if kind == "video" else len(tracks)
    return {**doc, "tracks": [*tracks[:at], layer, *tracks[at:]]}


def _natural_f(asset: dict, fps: int) -> int:
    seconds = asset.get("durationS")
    if asset.get("kind") != "image" and isinstance(seconds, (int, float)) and seconds > 0:
        return max(1, _js_round(seconds * fps))
    return BROLL_DEFAULT_F


def insert_asset(doc: dict, words: list[dict], media: str, word_idx: int, workspace_id: str,
                 asset: dict) -> tuple[dict, str, int]:
    """insert.ts `insertAsset`: (new doc, clip id, tlStartF). Raises ValueError when the word is cut."""
    at = word_anchor(doc, words, media, word_idx)
    if at is None:
        raise ValueError("That word is cut, so nothing can start on it.")
    anchor, tl = at
    tab = asset_tab(asset)
    room = max(1, _speech_end_f(doc) - tl)
    natural = _natural_f(asset, doc["fps"])
    dur_f = max(1, min(min(natural, BROLL_DEFAULT_F) if tab == "broll" else natural, room))
    clip_id = _unique_id(_element_ids(doc), _slug(asset.get("name") or "") or tab, "_")
    clip = {
        "id": clip_id,
        "media": {"source": workspace_media_ref(workspace_id, asset["file"]), "proxy": None},
        "srcInF": 0, "srcOutF": dur_f, "tlStartF": tl, "gainDb": 0,
        **({"duckDb": MUSIC_DUCK_DB} if tab == "music" else {}),
        "anchor": anchor,
    }
    kind = "audio" if asset.get("kind") == "audio" else "video"
    return _place(doc, kind, clip, dur_f), clip_id, tl
