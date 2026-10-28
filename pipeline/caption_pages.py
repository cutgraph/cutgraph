"""The pill caption rule, shared by preview and export.

Which transcript words are captioned, where they sit on the timeline, how they page into one-line
captions, and when each page and each word state shows. ui/src/preview/captionPages.ts mirrors this
exactly and tests/fixtures/caption_pages_parity.json holds both to the same output. Every time is an
integer number of centiseconds on the timeline (ASS precision), so the burned ASS events and the
player's per-frame state come from the same numbers.
"""
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path

from pipeline import names
from pipeline import project as project_mod
from pipeline import timebase

CHAR_EM = 0.624085441468254      # mean Inter Bold advance per character (em): the line budget without measuring
LINE_FRAC = 0.82   # a line fills at most 82% of the frame width
GAP_CS = 120       # a silence longer than 1.2 s starts a new page
SWITCH_CS = 5      # the next page replaces a finished one 0.05 s after its last word
HOLD_CS = 50       # a page before a pause, a scene change or the end stays 0.5 s after its last word


def layout(width: int, height: int) -> tuple[float, float]:
    """(baseline y, font px) of the caption line: motion-core `layoutFor` capY/capFs, with capFs
    scaled from its 1080-px reference to the frame."""
    cap_fs = 46 if width > height else 58 if width < height else 44
    cap_y = height * (0.9 if width < height else 0.935)
    return cap_y, cap_fs * min(width, height) / 1080


def line_budget(width: int, height: int) -> int:
    """Characters (spaces included) a page may hold: the 82%-width line at CHAR_EM per character."""
    return math.floor(LINE_FRAC * width / (CHAR_EM * layout(width, height)[1]))


def _cs(t: float) -> int:
    return math.floor(t * 100 + 0.5)


def _timed(w: dict) -> bool:
    s, e = w.get("start"), w.get("end")
    return isinstance(s, (int, float)) and isinstance(e, (int, float)) and math.isfinite(s) and math.isfinite(e)


def transcript_words(proj: project_mod.Project, transcripts_dir: Path) -> list[dict]:
    """The flat words `{w, start, end}` of the speech media's words sidecar (the transcript API's
    shape); [] when there is no speech or the sidecar is missing or unreadable. A sidecar spelled
    differently on disk raises NameNotFound rather than being read."""
    speech = next((c for t in proj.tracks if t.kind == "speech" for c in t.clips), None)
    if speech is None:
        return []
    sidecar = names.exact_path(transcripts_dir / f"{Path(speech.media.source).stem}.words.json", transcripts_dir)
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [{"w": (w.get("word") or "").strip(), "start": w.get("start"), "end": w.get("end")}
            for seg in raw.get("segments", []) for w in seg.get("words", [])]


def caption_words(proj: project_mod.Project, words: list[dict]) -> list[dict]:
    """The kept words `{w, start, end, scene}` in timeline order. `words` are the flat transcript words
    (`w`, `start`/`end` seconds). A timed word is kept when a speech clip of the speech media covers
    its start frame (the Story model's cut rule) and is clipped to that clip's source window. A word
    WhisperX left unaligned rides its nearest timed neighbour (the previous, else the next) as a
    zero-length word at that neighbour's end (or start). `scene` indexes the scene owning the word."""
    speech = next((t for t in proj.tracks if t.kind == "speech"), None)
    if speech is None or not speech.clips:
        return []
    fps = proj.fps
    media = speech.clips[0].media.source
    clips = [c for c in speech.clips if c.media.source == media]
    placed: list[tuple[int, int] | None] = [None] * len(words)
    for i, w in enumerate(words):
        if not _timed(w):
            continue
        f = timebase.sec_to_frame(w["start"], fps)
        clip = next((c for c in clips if c.srcInF <= f < c.srcOutF), None)
        if clip is None:
            continue
        base = (clip.tlStartF - clip.srcInF) / fps
        start = _cs(base + max(w["start"], clip.srcInF / fps))
        end = _cs(base + min(w["end"], clip.srcOutF / fps))
        placed[i] = (start, max(start, end))
    timed = [i for i, w in enumerate(words) if _timed(w)]
    for i, w in enumerate(words):
        if _timed(w) or not timed:
            continue
        k = bisect.bisect_left(timed, i)
        r = timed[k - 1] if k > 0 else timed[k]
        at = placed[r]
        if at is not None:
            t = at[1] if r < i else at[0]
            placed[i] = (t, t)
    starts = sorted({s.startWord for s in proj.scenes or []})
    out = [(p[0], i, {"w": str(w.get("w") or "").strip(), "start": p[0], "end": p[1],
                      "scene": bisect.bisect_right(starts, i) - 1})
           for i, (w, p) in enumerate(zip(words, placed)) if p is not None]
    return [x[2] for x in sorted(out, key=lambda x: (x[0], x[1])) if x[2]["w"]]


def caption_pages(proj: project_mod.Project, words: list[dict], width: int, height: int) -> list[dict]:
    """Page the kept words into one-line captions `{on, off, words: [{w, start, end}]}`. A page ends
    when the next word would overflow the line budget; a scene change or a silence over GAP_CS starts
    a fresh page too (a break). A page shows from its first word (after a break) or from the moment
    the previous page leaves, until SWITCH_CS after its last word when the next page follows on, or
    HOLD_CS after it before a break, never past the next page's first word."""
    budget = line_budget(width, height)
    groups: list[list[dict]] = []
    breaks: list[bool] = []
    chars = 0
    for w in caption_words(proj, words):
        n = len(w["w"])
        prev = groups[-1][-1] if groups else None
        if prev is None or w["scene"] != prev["scene"] or w["start"] - prev["end"] > GAP_CS:
            groups.append([w])
            breaks.append(True)
            chars = n
        elif chars + 1 + n > budget:
            groups.append([w])
            breaks.append(False)
            chars = n
        else:
            groups[-1].append(w)
            chars += 1 + n
    pages: list[dict] = []
    for k, group in enumerate(groups):
        last = group[-1]["end"]
        nxt = groups[k + 1][0]["start"] if k + 1 < len(groups) else None
        on = group[0]["start"] if breaks[k] else pages[-1]["off"]
        if nxt is None:
            off = last + HOLD_CS
        else:
            off = min(last + (HOLD_CS if breaks[k + 1] else SWITCH_CS), nxt)
        pages.append({"on": on, "off": off,
                      "words": [{"w": w["w"], "start": w["start"], "end": w["end"]} for w in group]})
    return pages


def word_states(page: dict, t: float) -> str:
    """Each page word's state at time `t` (centiseconds): C the word being spoken, P already
    spoken, U upcoming."""
    return "".join("C" if w["start"] <= t < w["end"] else "P" if t >= w["end"] else "U"
                   for w in page["words"])


def caption_events(pages: list[dict]) -> list[dict]:
    """One `{start, end, page, states}` interval per stretch of a page with constant word states:
    the ASS Dialogue events the export burns, and exactly what the player shows at those times."""
    events: list[dict] = []
    for p, page in enumerate(pages):
        cuts = sorted({page["on"], page["off"], *(t for w in page["words"] for t in (w["start"], w["end"])
                                                   if page["on"] < t < page["off"])})
        events += [{"start": a, "end": b, "page": p, "states": word_states(page, a)}
                   for a, b in zip(cuts, cuts[1:])]
    return events
