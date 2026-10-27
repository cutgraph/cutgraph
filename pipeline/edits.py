"""Ripple-cut engine for the transcript panel.

Pure timeline surgery: remove a frame range from the SPEECH track and ripple later clips left,
recording a clip-relative audio `seam` at every cut join so the compiler can crossfade.
Dependency-free like project.py — stdlib + config + pipeline.project/timebase only; no ffmpeg/torch/numpy.

`ripple_cut` never mutates the input Project (deep-copies via to_dict/from_dict at entry) and returns
a NEW, validate()-passing Project plus a warnings list for word-anchored / time-pinned overlay items
that the cut disturbed. Only speech-track clips are ever cut; other tracks only move.

`reorder_scenes` is the Story board's reorder: same purity and return contract, re-sequencing the
speech track so each scene's kept material plays in the new story order.

`timeline_end_f` is the video's length after any of these edits: a word-anchored cue follows its word
but never lengthens the video, so no edit can leave a silent tail.
"""
from __future__ import annotations

import dataclasses
import math
from bisect import bisect_right

import config
from pipeline.errors import ValidationError
from pipeline import project as project_mod
from pipeline import timebase
from pipeline.project import Clip, GraphicItem, Media, Project, TextItem

TimelineItem = TextItem | GraphicItem


def _clip_dur(c: Clip) -> int:
    return c.srcOutF - c.srcInF


def _make_piece(orig: Clip, new_id: str, new_in: int, new_out: int, new_tl: int,
                join_atFs: list[int], fade_ms: int) -> Clip:
    """Build a surviving clip piece from `orig`, carrying seams (clip-relative) through the trim.

    Existing seams are remapped by source frame into the new [new_in, new_out] window; the join
    frames get a fresh seam at `fade_ms`. Deduped by atF so touching cuts collapse to one join.
    """
    seams: dict[int, int] = {}
    for sm in orig.seams or []:
        src = orig.srcInF + int(sm["atF"])
        if new_in <= src <= new_out:
            seams.setdefault(src - new_in, int(sm["fadeMs"]))
    for at in join_atFs:
        seams.setdefault(at, fade_ms)
    seam_list = [{"atF": a, "fadeMs": f} for a, f in sorted(seams.items())]
    return Clip(
        id=new_id,
        media=Media(source=orig.media.source, proxy=orig.media.proxy),
        srcInF=new_in,
        srcOutF=new_out,
        tlStartF=new_tl,
        gainDb=orig.gainDb,
        anchor=None,
        duckDb=orig.duckDb,
        seams=seam_list or None,
    )


def _cut_speech_clip(c: Clip, startF: int, endF: int, fade_ms: int) -> list[Clip]:
    """Apply the cut [startF, endF) to one speech clip; return its 0..2 surviving pieces."""
    s, d = c.tlStartF, _clip_dur(c)
    e = s + d
    if e <= startF:                                  # entirely before the cut
        return [c]
    if s >= endF:                                    # entirely after the cut -> ripple left
        c.tlStartF = s - (endF - startF)
        return [c]
    if startF <= s and endF >= e:                    # cut spans the whole clip -> drop it
        return []
    if s < startF and endF < e:                      # cut fully inside -> split in two
        left = _make_piece(c, c.id, c.srcInF, c.srcInF + (startF - s), s, [startF - s], fade_ms)
        right = _make_piece(c, f"{c.id}-r{startF}", c.srcInF + (endF - s), c.srcOutF, startF,
                            [0], fade_ms)
        return [left, right]
    if s < startF:                                   # cut trims the tail (endF >= e)
        return [_make_piece(c, c.id, c.srcInF, c.srcInF + (startF - s), s, [startF - s], fade_ms)]
    # cut trims the head (startF <= s, endF < e): advance srcInF, land at the join point
    return [_make_piece(c, c.id, c.srcInF + (endF - s), c.srcOutF, startF, [0], fade_ms)]


def _item_dur(item: Clip | TimelineItem) -> int:
    return _clip_dur(item) if isinstance(item, Clip) else item.durF


def _word_anchor_start(item: Clip | TimelineItem,
                       speech_by_media: dict[str, list[Clip]],
                       word_frames: dict[str, list[int]] | None) -> tuple[bool, int | None]:
    """Where a word-anchored overlay lands now: (True, tlStartF) while its word is on the timeline,
    (True, None) once that word is cut. (False, None) for a time-pinned item or a word whose media /
    index isn't in `word_frames` — callers treat those as time-pinned."""
    anchor = item.anchor
    if anchor is None or anchor.mode != "word" or word_frames is None:
        return False, None
    ref = anchor.ref or {}
    media, idx = ref.get("media"), ref.get("wordIdx")
    if not isinstance(media, str) or not isinstance(idx, int):
        return False, None
    frames = word_frames.get(media)
    if frames is None or not 0 <= idx < len(frames):
        return False, None
    wf = frames[idx]
    covering = next((c for c in speech_by_media.get(media, []) if c.srcInF <= wf < c.srcOutF), None)
    if covering is None:
        return True, None
    # Clamp: a negative anchor.offsetF on a left-shifted word must never drive tlStartF below 0
    # (validate() forbids it) — the edit still applies.
    return True, max(0, covering.tlStartF + (wf - covering.srcInF) + anchor.offsetF)


def _reposition(item: Clip | TimelineItem, startF: int, endF: int,
                speech_by_media: dict[str, list[Clip]],
                word_frames: dict[str, list[int]] | None,
                warnings: list[dict]) -> bool:
    """Recompute a non-speech overlay's position (word-anchored) or flag a time-pinned crossing.

    Returns True iff the item was word-anchor recomputed to a NEW position (a candidate for the
    per-track collision revert in `_resolve_anchor_collisions`); False for orphaned / time-pinned.
    """
    anchored, start = _word_anchor_start(item, speech_by_media, word_frames)
    if anchored:
        if start is None:
            warnings.append({"type": "orphaned-anchor", "itemId": item.id})
            return False
        item.tlStartF = start
        return True
    dur = _item_dur(item)
    item_end = item.tlStartF + dur
    if startF < item_end and item.tlStartF < endF:
        # The cut range overlaps the item's own span — the item straddles removed footage.
        warnings.append({"type": "time-pinned-crossing", "itemId": item.id})
    elif item.tlStartF >= endF:
        # The cut is entirely BEFORE the item: the speech beneath it rippled left but the time-pinned
        # item did not, so it now sits over different content — silent drift, the time-pinned drift warning.
        warnings.append({"type": "time-pinned-drift", "itemId": item.id})
    return False


def _resolve_anchor_collisions(elements: list[Clip | TimelineItem], old_pos: dict[int, int],
                               movable: set[int], warnings: list[dict]) -> None:
    """After word-anchor recompute, a moved overlay can land on a neighbor on its own track. Revert
    the moved item to its old (time-pinned) position and warn, until the track is overlap-free — so
    ripple_cut's final validate() invariant never fires on a valid input project. Each revert
    pins one item at its original (valid) slot, so this terminates in ≤ len(elements) passes."""
    while True:
        ordered = sorted(elements, key=lambda it: it.tlStartF)
        clash = next(((a, b) for a, b in zip(ordered, ordered[1:])
                      if b.tlStartF < a.tlStartF + _item_dur(a)), None)
        if clash is None:
            return
        target = next((c for c in clash
                       if id(c) in movable and c.tlStartF != old_pos[id(c)]), None)
        if target is None:
            return
        target.tlStartF = old_pos[id(target)]
        movable.discard(id(target))
        warnings.append({"type": "anchor-collision", "itemId": target.id})


def ripple_cut(
    project: Project, startF: int, endF: int, *,
    word_frames: dict[str, list[int]] | None = None,
    fade_ms: int = config.EDITS_SEAM_FADE_MS,
) -> tuple[Project, list[dict]]:
    """Remove timeline range [startF, endF) from the SPEECH track and ripple later clips left.

    Pure: never mutates `project` (deep-copy via to_dict/from_dict at entry). Returns a NEW,
    validate()-passing Project plus a warnings list.
    """
    if not (endF > startF):
        raise ValidationError("cut range must be non-empty (endF > startF)")
    proj = project_mod.from_dict(project_mod.to_dict(project))
    warnings: list[dict] = []

    for track in proj.tracks:
        if track.kind != "speech":
            continue
        new_clips: list[Clip] = []
        for c in track.clips:
            new_clips.extend(_cut_speech_clip(c, startF, endF, fade_ms))
        track.clips = new_clips

    speech_by_media: dict[str, list[Clip]] = {}
    for track in proj.tracks:
        if track.kind == "speech":
            for c in track.clips:
                speech_by_media.setdefault(c.media.source, []).append(c)

    for track in proj.tracks:
        if track.kind == "speech":
            continue
        elements: list[Clip | TimelineItem] = list(track.items) if track.kind == "text" else list(track.clips)
        old_pos = {id(it): it.tlStartF for it in elements}
        movable: set[int] = set()
        for item in elements:
            if _reposition(item, startF, endF, speech_by_media, word_frames, warnings):
                movable.add(id(item))
        _resolve_anchor_collisions(elements, old_pos, movable, warnings)

    project_mod.validate(proj)
    return proj, warnings


def _uncovered_runs(clips: list[Clip], srcStartF: int, srcEndF: int) -> list[tuple[int, int]]:
    """The sub-ranges of source [srcStartF, srcEndF) that no clip in `clips` covers, ascending."""
    runs: list[tuple[int, int]] = []
    at = srcStartF
    for c in sorted(clips, key=lambda c: c.srcInF):
        if c.srcOutF <= at or c.srcInF >= srcEndF:
            continue
        if c.srcInF > at:
            runs.append((at, c.srcInF))
        at = max(at, c.srcOutF)
    if at < srcEndF:
        runs.append((at, srcEndF))
    return runs


def _add_seam(c: Clip, atF: int, fade_ms: int) -> None:
    seams = {int(sm["atF"]): int(sm["fadeMs"]) for sm in c.seams or []}
    seams.setdefault(atF, fade_ms)
    c.seams = [{"atF": a, "fadeMs": f} for a, f in sorted(seams.items())]


def _absorb(c: Clip, nxt: Clip) -> None:
    """Extend `c` over the source-contiguous `nxt` that follows it; the join's seams disappear."""
    d = _clip_dur(c)
    seams = {int(sm["atF"]): int(sm["fadeMs"]) for sm in c.seams or [] if int(sm["atF"]) != d}
    for sm in nxt.seams or []:
        if int(sm["atF"]) != 0:
            seams.setdefault(int(sm["atF"]) + d, int(sm["fadeMs"]))
    c.srcOutF = nxt.srcOutF
    c.seams = [{"atF": a, "fadeMs": f} for a, f in sorted(seams.items())] or None


def _insert_speech(proj: Project, media: str, a: int, b: int, fade_ms: int) -> int:
    """Insert source [a, b) of `media` into the speech track right after the kept clip that precedes
    it in source (before the first one that follows it when none precedes), rippling later speech
    right. Source-contiguous neighbours are joined back into one clip. Returns the insert frame."""
    speech = [t for t in proj.tracks if t.kind == "speech"]
    if not speech:
        raise ValidationError("project has no speech track")
    mine = [(t, c) for t in speech for c in t.clips if c.media.source == media]
    before = [tc for tc in mine if tc[1].srcOutF <= a]
    after = [tc for tc in mine if tc[1].srcInF >= b]
    if before:
        track, ref = max(before, key=lambda tc: tc[1].srcOutF)
        at = ref.tlStartF + _clip_dur(ref)
    elif after:
        track, ref = min(after, key=lambda tc: tc[1].srcInF)
        at = ref.tlStartF
    else:
        track, ref = speech[0], None
        at = max((c.tlStartF + _clip_dur(c) for t in speech for c in t.clips), default=0)
    for t in speech:
        for c in t.clips:
            if c.tlStartF >= at:
                c.tlStartF += b - a
    ids = {c.id for t in proj.tracks for c in t.clips} | {i.id for t in proj.tracks for i in t.items}
    new_id = f"{ref.id if ref else 'speech'}-u{a}"
    while new_id in ids:
        new_id += "x"
    piece = Clip(id=new_id, media=Media(source=media, proxy=ref.media.proxy if ref else None),
                 srcInF=a, srcOutF=b, tlStartF=at, gainDb=ref.gainDb if ref else 0.0,
                 duckDb=ref.duckDb if ref else None)
    prev = next((c for c in track.clips if c.tlStartF + _clip_dur(c) == at), None)
    nxt = next((c for c in track.clips if c.tlStartF == b - a + at), None)
    cur = piece
    if prev is not None and prev.media.source == media and prev.srcOutF == a:
        _absorb(prev, piece)
        cur = prev
    else:
        if prev is not None:
            _add_seam(prev, _clip_dur(prev), fade_ms)
            _add_seam(piece, 0, fade_ms)
        track.clips.append(piece)
    if nxt is not None and nxt.media.source == media and nxt.srcInF == b:
        _absorb(cur, nxt)
        track.clips.remove(nxt)
        if cur is piece:
            piece.id = nxt.id
    elif nxt is not None:
        _add_seam(cur, _clip_dur(cur), fade_ms)
        _add_seam(nxt, 0, fade_ms)
    track.clips.sort(key=lambda c: c.tlStartF)
    return at


def ripple_restore(
    project: Project, media: str, srcStartF: int, srcEndF: int, *,
    word_frames: dict[str, list[int]] | None = None,
    fade_ms: int = config.EDITS_SEAM_FADE_MS,
) -> tuple[Project, list[dict]]:
    """Put the cut parts of source [srcStartF, srcEndF) of `media` back on the speech track: the
    inverse of ripple_cut. Overlays follow ripple_cut's rules — word anchors re-derive from their word
    (so an orphaned b-roll returns with its restored word), time pins stay put and warn.

    Pure: never mutates `project`. Returns a NEW, validate()-passing Project plus a warnings list.
    """
    if not (srcEndF > srcStartF):
        raise ValidationError("restore range must be non-empty (srcEndF > srcStartF)")
    proj = project_mod.from_dict(project_mod.to_dict(project))
    warnings: list[dict] = []
    kept = [c for t in proj.tracks if t.kind == "speech" for c in t.clips if c.media.source == media]
    for a, b in _uncovered_runs(kept, srcStartF, srcEndF):
        at = _insert_speech(proj, media, a, b, fade_ms)
        speech_by_media: dict[str, list[Clip]] = {}
        for track in proj.tracks:
            if track.kind == "speech":
                for c in track.clips:
                    speech_by_media.setdefault(c.media.source, []).append(c)
        for track in proj.tracks:
            if track.kind == "speech":
                continue
            elements: list[Clip | TimelineItem] = list(track.items) if track.kind == "text" else list(track.clips)
            old_pos = {id(it): it.tlStartF for it in elements}
            movable: set[int] = set()
            for item in elements:
                # An empty [at, at) range: time pins straddling the insert point cross it, later ones drift.
                if _reposition(item, at, at, speech_by_media, word_frames, warnings):
                    movable.add(id(item))
            _resolve_anchor_collisions(elements, old_pos, movable, warnings)
    project_mod.validate(proj)
    return proj, warnings


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Normalize a cut batch into disjoint ranges: drop empties, sort ascending, then union any
    overlapping OR adjacent ranges. Right-to-left folding only equals removing the union when the
    ranges are DISJOINT — otherwise a leftward range gets re-interpreted in already-shifted
    coordinates and over-removes footage. Merging first makes the fold exact for any input."""
    ordered = sorted((r for r in ranges if r[1] > r[0]), key=lambda r: r[0])
    merged: list[tuple[int, int]] = []
    for startF, endF in ordered:
        if merged and startF <= merged[-1][1]:          # overlapping or touching -> extend
            merged[-1] = (merged[-1][0], max(merged[-1][1], endF))
        else:
            merged.append((startF, endF))
    return merged


def apply_cuts(
    project: Project, ranges: list[tuple[int, int]], *,
    word_frames: dict[str, list[int]] | None = None,
    fade_ms: int = config.EDITS_SEAM_FADE_MS,
) -> tuple[Project, list[dict]]:
    """Normalize the batch (merge overlapping/adjacent ranges) then fold ripple_cut over the disjoint
    ranges DESCENDING by startF (right-to-left) so earlier ranges need no re-mapping after left-shifts.
    Merges warnings; seams dedup per clip. Used by the accept-cut and accept-N-proposals verb for ONE
    batch = ONE undo/one revision."""
    proj = project
    warnings: list[dict] = []
    for startF, endF in sorted(_merge_ranges(ranges), key=lambda r: r[0], reverse=True):
        proj, w = ripple_cut(proj, startF, endF, word_frames=word_frames, fade_ms=fade_ms)
        warnings.extend(w)
    return proj, warnings


# ── the video's length ──────────────────────────────────────────────────────────

def timeline_end_f(project: Project) -> int:
    """The video's length in frames: where the speech ends, or later only where a time-pinned element
    runs past it. A word-anchored cue never lengthens the video. It starts on its word and keeps its
    authored length, and whatever of it runs past this end isn't played, so a reorder, cut or restore
    that moves its word can't add a silent tail, and moving the word back plays the cue whole again.
    With no speech and no time pin, the longest element sets the length.

    The preview uses the same rule (`projectDurationF` in ui/src/timeline/geometry.ts) and the export
    trims every element to it (pipeline/compile.py)."""
    pinned = longest = 0
    for track in project.tracks:
        for element in [*track.clips, *track.items]:
            end = element.tlStartF + _item_dur(element)
            longest = max(longest, end)
            anchor = element.anchor
            if track.kind == "speech" or anchor is None or anchor.mode != "word":
                pinned = max(pinned, end)
    return pinned or longest


# ── scene reorder (Story board) ─────────────────────────────────────────────────

_ID_MAX = 64  # project._ID_RE's length bound


def _timed_frame(start: float | None, end: float | None, fps: int) -> int | None:
    if start is None or end is None or not (math.isfinite(start) and math.isfinite(end)):
        return None
    return timebase.sec_to_frame(start, fps)


def resolve_word_frames(timings: list[tuple[float | None, float | None]], fps: int) -> list[int]:
    """Source start frame of every flat word. A word WhisperX left unaligned takes its nearest timed
    neighbour (the previous one, else the next), the UI's resolveTimed rule, so the server and the
    Story model put scene boundaries on the same frame. An all-untimed transcript resolves to 0."""
    own = [_timed_frame(start, end, fps) for start, end in timings]
    out = list(own)
    prev: int | None = None
    for i, f in enumerate(own):
        prev = f if f is not None else prev
        out[i] = prev
    nxt: int | None = None
    for i in range(len(own) - 1, -1, -1):
        nxt = own[i] if own[i] is not None else nxt
        if out[i] is None:
            out[i] = nxt
    return [f if f is not None else 0 for f in out]


def _fresh_id(stem: str, suffix: str, taken: set[str]) -> str:
    n = 0
    while True:
        tail = suffix if n == 0 else f"{suffix}-{n}"
        cand = stem[:_ID_MAX - len(tail)] + tail
        if cand not in taken:
            taken.add(cand)
            return cand
        n += 1


def _split_at(clip: Clip, points: list[int], taken: set[str]) -> list[Clip]:
    """Split a speech clip at ascending source frames strictly inside it. Every piece keeps the clip's
    fields and the seams that fall inside it; the first piece keeps the clip's id."""
    edges = [clip.srcInF, *points, clip.srcOutF]
    pieces: list[Clip] = []
    for k, (a, b) in enumerate(zip(edges, edges[1:])):
        seams = [{"atF": clip.srcInF + int(s["atF"]) - a, "fadeMs": int(s["fadeMs"])}
                 for s in clip.seams or [] if a <= clip.srcInF + int(s["atF"]) <= b]
        pieces.append(dataclasses.replace(
            clip, id=clip.id if k == 0 else _fresh_id(clip.id, f"-s{a}", taken),
            srcInF=a, srcOutF=b, tlStartF=clip.tlStartF + (a - clip.srcInF), seams=seams or None))
    return pieces


def _set_seam(clip: Clip, at: int, fade_ms: int | None) -> None:
    """Add (fade_ms) or drop (None) the seam at clip-relative frame `at`; an existing fade is kept."""
    seams = {int(s["atF"]): int(s["fadeMs"]) for s in clip.seams or []}
    if fade_ms is None:
        seams.pop(at, None)
    else:
        seams.setdefault(at, fade_ms)
    clip.seams = [{"atF": a, "fadeMs": f} for a, f in sorted(seams.items())] or None


def reorder_scenes(
    project: Project, order: list[str], *,
    word_frames: dict[str, list[int]] | None = None,
    fade_ms: int = config.EDITS_SEAM_FADE_MS,
) -> tuple[Project, list[dict]]:
    """Put the scenes in story `order` (every scene id once) and re-sequence the speech track to match.

    A scene owns the source span from its first word to the next scene's first word (the Story
    model's rule, over `word_frames` of the primary speech media). Speech clips are split at those
    boundaries and each scene's pieces move as one block into the new order; the gaps between blocks
    keep their slots, so the running time is unchanged. A clip of another media travels with the
    scene before it. Joins that become discontinuous gain seams; joins that become continuous lose
    them. Word-anchored overlays follow their words; time-pinned (or orphaned) ones stay put and are
    flagged `time-pinned-drift` when the speech under them moved. Pure, like ripple_cut.
    """
    proj = project_mod.from_dict(project_mod.to_dict(project))
    ids = [s.id for s in proj.scenes]
    if len(order) != len(ids) or set(order) != set(ids):
        raise ValueError("order must list every scene id exactly once")
    if order == ids:
        return proj, []
    by_id = {s.id: s for s in proj.scenes}
    proj.scenes = [by_id[i] for i in order]
    speech = next((t for t in proj.tracks if t.kind == "speech" and t.clips), None)
    if speech is None:
        project_mod.validate(proj)
        return proj, []
    media = speech.clips[0].media.source
    frames = (word_frames or {}).get(media)
    if frames is None:
        raise ValueError(f"no word timings for {media}")

    # Scene source starts in word order: frame f belongs to the last scene starting at or before it.
    by_word = sorted(proj.scenes, key=lambda s: s.startWord)
    starts = [0.0 if s.startWord == 0 else float(frames[s.startWord]) if s.startWord < len(frames)
              else math.inf for s in by_word]
    points = sorted({int(f) for f in starts[1:] if math.isfinite(f)})
    taken = {x.id for t in proj.tracks for x in [*t.clips, *t.items]}
    pieces: list[Clip] = []
    owners: list[str | None] = []
    for clip in sorted(speech.clips, key=lambda c: c.tlStartF):
        if clip.media.source != media:
            pieces.append(clip)
            owners.append(None)
            continue
        for piece in _split_at(clip, [f for f in points if clip.srcInF < f < clip.srcOutF], taken):
            pieces.append(piece)
            owners.append(by_word[max(0, bisect_right(starts, piece.srcInF) - 1)].id)
    # Another media's clip rides with the scene before it; a leading one with the scene after it.
    last = next(o for o in owners if o is not None)
    scene_of: list[str] = []
    for owner in owners:
        last = owner if owner is not None else last
        scene_of.append(last)

    blocks: list[tuple[str, list[Clip]]] = []
    for piece, sid in zip(pieces, scene_of):
        if blocks and blocks[-1][0] == sid:
            blocks[-1][1].append(piece)
        else:
            blocks.append((sid, [piece]))
    head = [ps[0].tlStartF for _, ps in blocks]
    tail = [max(p.tlStartF + _clip_dur(p) for p in ps) for _, ps in blocks]
    gaps = [0] + [head[k] - tail[k - 1] for k in range(1, len(blocks))]
    rank = {sid: i for i, sid in enumerate(order)}
    cursor = head[0]
    moved: set[int] = set()
    for slot, k in enumerate(sorted(range(len(blocks)), key=lambda k: rank[blocks[k][0]])):
        cursor += gaps[slot]
        shift = cursor - head[k]
        for p in blocks[k][1]:
            p.tlStartF += shift
            if shift:
                moved.add(id(p))
        cursor += tail[k] - head[k]
    speech.clips = sorted(pieces, key=lambda c: c.tlStartF)

    for prev, nxt in zip(speech.clips, speech.clips[1:]):
        if (id(prev) in moved or id(nxt) in moved) and prev.tlStartF + _clip_dur(prev) == nxt.tlStartF:
            fade = None if prev.media.source == nxt.media.source and prev.srcOutF == nxt.srcInF else fade_ms
            _set_seam(prev, _clip_dur(prev), fade)
            _set_seam(nxt, 0, fade)

    speech_by_media: dict[str, list[Clip]] = {}
    for track in proj.tracks:
        if track.kind == "speech":
            for c in track.clips:
                speech_by_media.setdefault(c.media.source, []).append(c)
    moved_spans = [(c.tlStartF, c.tlStartF + _clip_dur(c)) for c in speech.clips if id(c) in moved]
    warnings: list[dict] = []
    for track in proj.tracks:
        if track.kind == "speech":
            continue
        elements: list[Clip | TimelineItem] = list(track.items) if track.kind == "text" else list(track.clips)
        old_pos = {id(it): it.tlStartF for it in elements}
        movable: set[int] = set()
        for item in elements:
            _anchored, start = _word_anchor_start(item, speech_by_media, word_frames)
            if start is not None:
                if start != item.tlStartF:
                    item.tlStartF = start
                    movable.add(id(item))
            elif any(a < item.tlStartF + _item_dur(item) and item.tlStartF < b for a, b in moved_spans):
                warnings.append({"type": "time-pinned-drift", "itemId": item.id})
        _resolve_anchor_collisions(elements, old_pos, movable, warnings)

    project_mod.validate(proj)
    return proj, warnings
