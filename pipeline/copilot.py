"""Story co-pilot tools behind the Ask bar: typed, validated, proposal-only.

Each tool reads the CURRENT project and reshaped transcript and returns a `Proposal`: RFC-6902 ops
plus the review copy (title, a one-line rationale, per-word marks for the Script lens). The agent
host persists it through `pipeline.proposals.write`, so Accept is the existing revision-gated,
one-undo-step path. Nothing here writes a file or applies an edit. Arguments come off the wire from
a model, so every field is checked and a bad one raises `ToolError` worded for the model to revise.

The model's map of the video is `story_outline`: scenes plus the segment-level transcript with the
word index each segment starts at, never the word-level JSON (a 1-hour talk's words are 100k+
tokens; its whole outline stays under STORY_TOKEN_BUDGET). Shot ids mirror motion-core; a test
parses its TS lists so the two cannot drift. A character's pose, expression and move are its rig's
(`pipeline.cast.RIGS`, plus the profile's own expressions), so `set_shot` checks each against the
profile it lands on: the lead's, the co-star's or an ensemble member's.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import config
from pipeline import cast as cast_mod
from pipeline import edits, library, story_insert, timebase
from pipeline import project as project_mod
from pipeline import scenes as scenes_mod
from pipeline import staging
from pipeline.project import BUBBLES, ENSEMBLE_MAX, INTERACTIONS, SCENE_ROLES, SCENE_TITLE_MAX, SHOT_MODES, Project
from pipeline.errors import ValidationError

# motion-core ids: scene.ts BACKDROPS/DIAGRAMS. Boards are independent content-sized layouts.
# A character's look ids are its rig's (cast.RIGS).
BACKDROPS = ("chaos", "work", "route", "proof", "result", "black", "vision")
DIAGRAMS = project_mod.SHOT_DIAGRAMS
SHOT_IDS = {"mode": SHOT_MODES, "backdrop": BACKDROPS, "diagram": DIAGRAMS, "interaction": INTERACTIONS, "bubble": BUBBLES,
            "camera": project_mod.SHOT_CAMERAS, "layout": project_mod.SHOT_LAYOUTS, "headlineAt": project_mod.HEADLINE_SPOTS}
LOOK_SETS = {"pose": "poses", "expression": "expressions", "move": "moves"}
# Every rig's poses and moves, for the tool schema; each shot is checked against its own character's.
POSES = tuple(dict.fromkeys(p for rig in cast_mod.RIGS.values() for p in rig["poses"]))
MOVES = tuple(dict.fromkeys(m for rig in cast_mod.RIGS.values() for m in rig["moves"]))

STORY_PAGE_CHARS = 12_000
STORY_TOKEN_BUDGET = 20_000
_SEGMENT_TEXT_CAP = 400
_HEADLINE_MAX = 80
_LABEL_MAX = 60
_RANGE_MAX_WORDS = 80
_LIBRARY_ROWS = 30
_FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "eh", "hmm", "mm", "mhm"})
_LEGIT_REPEATS = frozenset({"that", "had", "is"})
_STOP = frozenset({"the", "and", "for", "with", "this", "that", "you", "are", "was", "our", "your", "but",
                   "not", "have", "has", "what", "when", "then", "they", "them", "its", "it's", "from"})
_CUE_LABEL = {"broll": "B-roll", "music": "Music", "sfx": "Sound"}


class ToolError(ValueError):
    """A bad argument or an impossible edit, worded so the model can revise its call."""


class NothingToPropose(ToolError):
    """A valid call that found nothing to change (e.g. no filler words): report it, don't retry."""


@dataclass
class Proposal:
    title: str
    rationale: str
    ops: list[dict]
    marks: list[dict] = field(default_factory=list)
    saves_sec: float | None = None

    def result(self) -> dict:
        out: dict = {"title": self.title, "changes": [m["label"] for m in self.marks][:12]}
        if self.saves_sec:
            out["savesSec"] = self.saves_sec
        return out


@dataclass
class Story:
    project: Project
    doc: dict
    words: list[dict]
    fillers: list[dict]
    media: str

    @property
    def fps(self) -> int:
        return self.project.fps


def dumps(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


# ── argument checks ─────────────────────────────────────────────────────────────

def _int(value: object, label: str, lo: int = 0, hi: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < lo or (hi is not None and value >= hi):
        bound = f" below {hi}" if hi is not None else ""
        raise ToolError(f"{label} must be an integer >= {lo}{bound}")
    return value


def _text(value: object, label: str, cap: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{label} must be a non-empty string")
    text = " ".join(value.split())
    if len(text) > cap:
        raise ToolError(f"{label} must be at most {cap} characters")
    return text


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ToolError(f"{label} must be true or false")
    return value


def _list(value: object, label: str, cap: int) -> list:
    if not isinstance(value, list) or not value:
        raise ToolError(f"{label} must be a non-empty list")
    if len(value) > cap:
        raise ToolError(f"{label} takes at most {cap} entries")
    return value


def _obj(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ToolError(f"{label} must be an object")
    return value


def _title(args: dict, default: str) -> str:
    return default if args.get("title") is None else _text(args["title"], "title", _LABEL_MAX)


def _why(args: dict, default: str) -> str:
    return default if args.get("why") is None else _text(args["why"], "why", 200)


def _clip(text: str, cap: int = _LABEL_MAX) -> str:
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ── the story as the Script lens sees it ────────────────────────────────────────

def _speech_media(doc: dict) -> str | None:
    clips = story_insert.speech_clips(doc)
    return clips[0]["media"]["source"] if clips else None


def _covered(doc: dict, media: str, a: int, b: int) -> list[tuple[dict, int, int]]:
    """cutmap.coveredSrcRuns: the source sub-ranges of [a, b) still on the timeline, ascending."""
    runs = [(c, max(a, c["srcInF"]), min(b, c["srcOutF"])) for c in story_insert.speech_clips(doc)
            if c["media"]["source"] == media]
    return sorted([r for r in runs if r[1] < r[2]], key=lambda r: r[1])


def _to_timeline(runs: list[tuple[dict, int, int]]) -> list[tuple[int, int]]:
    return [(c["tlStartF"] + (a - c["srcInF"]), c["tlStartF"] + (b - c["srcInF"])) for c, a, b in runs]


def _word_frame(word: dict, fps: int) -> int:
    return timebase.sec_to_frame(word["start"], fps)


def cut_words(doc: dict, media: str, words: list[dict]) -> set[int]:
    """model.cutWordSet: a word is cut when its resolved-timed source frame is on no speech clip."""
    eff = story_insert.resolve_timed(words)
    return {i for i, t in enumerate(eff)
            if t >= 0 and story_insert.src_to_timeline(doc, media, _word_frame(words[t], doc["fps"])) is None}


def scene_ranges(p: Project, n_words: int) -> dict[str, tuple[int, int]]:
    """Each scene's flat word range [startWord, next-larger startWord) — list order is story order."""
    starts = sorted({s.startWord for s in p.scenes})
    return {s.id: (s.startWord, next((x for x in starts if x > s.startWord), n_words)) for s in p.scenes}


def _story(doc: dict, tdoc: dict | None) -> Story:
    words = (tdoc or {}).get("words") or []
    if not words:
        raise ToolError("this video has no transcript yet")
    try:
        p = project_mod.from_dict(doc)
    except (ValidationError, KeyError, TypeError, ValueError) as e:
        raise ToolError(f"the project could not be read: {e}") from e
    normalized = project_mod.to_dict(p)
    media = _speech_media(normalized)
    if media is None:
        raise ToolError("the project has no speech track")
    return Story(p, normalized, words, (tdoc or {}).get("fillers") or [], media)


def _segments(words: list[dict]) -> list[tuple[int, float, list[str]]]:
    out: list[tuple[int, float, list[str]]] = []
    seg = None
    for i, w in enumerate(words):
        if seg is None or w.get("segIdx") != seg:
            seg = w.get("segIdx")
            start = w.get("start")
            out.append((i, round(float(start), 1) if isinstance(start, (int, float)) else -1.0, []))
        out[-1][2].append(w.get("w") or "")
    return out


def story_outline(doc: dict, revision: int, tdoc: dict | None, cursor: int = 0,
                  assets: list[dict] | None = None) -> str:
    """The compact map every Story tool addresses: scenes (id, role, title, word range, shot), the
    library suggest_broll picks from, and the segment-level transcript as [w, t, text] rows, where w
    is the flat word index the segment starts at. Already-cut words are wrapped in {…}. Paginated
    under STORY_PAGE_CHARS via nextCursor."""
    try:
        story = _story(doc, tdoc)
    except ToolError as e:
        return dumps({"available": False, "reason": str(e)})
    words, p = story.words, story.project
    cut = cut_words(story.doc, story.media, words)
    page: dict = {"revision": revision, "wordCount": len(words),
                  "legend": "segments rows are [w, t, text]: w = wordIdx of the first word, t = start sec; "
                            "{…} = already cut. library rows are [name, kind, seconds, tags]"}
    if cursor <= 0:
        ranges = scene_ranges(p, len(words))
        page["scenes"] = []
        for s in p.scenes:
            a, b = ranges[s.id]
            entry: dict = {"id": s.id, "role": s.role, "title": s.title, "words": [a, b]}
            gone = sum(1 for i in range(a, b) if i in cut)
            if gone:
                entry["cutWords"] = gone
            if s.shot:
                entry["shot"] = s.shot
            page["scenes"].append(entry)
        pending = _pending_fillers(story)
        if pending:
            page["pendingFillers"] = len(pending)
        if assets:
            page["library"] = [[e["asset"].get("name"), story_insert.asset_tab(e["asset"]), e["asset"].get("durationS"),
                                e["asset"].get("tags") or []] for e in assets[:_LIBRARY_ROWS]]
            if len(assets) > _LIBRARY_ROWS:
                page["libraryMore"] = len(assets) - _LIBRARY_ROWS
    rows: list[list] = []
    segments = _segments(words)
    start = max(0, cursor)
    for k in range(start, len(segments)):
        first, t, tokens = segments[k]
        text = " ".join(f"{{{w}}}" if (first + j) in cut else w for j, w in enumerate(tokens))
        rows.append([first, t, _clip(text, _SEGMENT_TEXT_CAP)])
        trial = {**page, "segments": rows, **({"nextCursor": k + 1} if k + 1 < len(segments) else {})}
        if len(dumps(trial)) > STORY_PAGE_CHARS and len(rows) > 1:
            rows.pop()
            return dumps({**page, "segments": rows, "nextCursor": k})
    return dumps({**page, "segments": rows})


# ── ops ─────────────────────────────────────────────────────────────────────────

def _track_ops(before: dict, after: dict) -> list[dict]:
    """Replace each changed track's contents (the whole list when a layer was added), each led by a
    `test` on the track id so the patch fails closed against a different layout."""
    b, a = before["tracks"], after["tracks"]
    if [t["id"] for t in b] != [t["id"] for t in a]:
        guards = [{"op": "test", "path": f"/tracks/{i}/id", "value": t["id"]} for i, t in enumerate(b)]
        return [*guards, {"op": "replace", "path": "/tracks", "value": a}]
    ops: list[dict] = []
    for i, (tb, ta) in enumerate(zip(b, a)):
        if tb != ta:
            key = "items" if ta["kind"] == "text" else "clips"
            ops += [{"op": "test", "path": f"/tracks/{i}/id", "value": ta["id"]},
                    {"op": "replace", "path": f"/tracks/{i}/{key}", "value": ta[key]}]
    return ops


def _apply(work: Project, step: list[dict]) -> Project:
    try:
        return project_mod.apply_ops(work, step)
    except Exception as e:  # noqa: BLE001 — any patch or validation failure is a revisable argument error
        raise ToolError(f"that edit does not apply: {e}") from e


# ── plan_scenes ─────────────────────────────────────────────────────────────────

def _scene(work: Project, scene_id: object, label: str) -> tuple[int, str]:
    sid = _text(scene_id, label, 64)
    for i, s in enumerate(work.scenes):
        if s.id == sid:
            return i, sid
    raise ToolError(f"{label}: no scene {sid!r} (scenes: {', '.join(s.id for s in work.scenes)})")


def _role(value: object, label: str) -> str:
    if value not in SCENE_ROLES:
        raise ToolError(f"{label} must be one of {', '.join(SCENE_ROLES)}")
    return str(value)


def plan_scenes(story: Story, args: dict) -> Proposal:
    """Split, merge, rename or re-role scenes through pipeline.scenes' guarded patch builders."""
    work = story.project
    n = len(story.words)
    ops: list[dict] = []
    marks: list[dict] = []
    for k, raw in enumerate(_list(args.get("edits"), "edits", 24)):
        e = _obj(raw, f"edits[{k}]")
        op = e.get("op")
        i, sid = _scene(work, e.get("scene"), f"edits[{k}].scene")
        scene = work.scenes[i]
        a, b = scene_ranges(work, n)[sid]
        if op == "split":
            at = _int(e.get("word"), f"edits[{k}].word", a + 1, b)
            title = (_text(e["title"], f"edits[{k}].title", SCENE_TITLE_MAX) if e.get("title") is not None
                     else scenes_mod.title_for(story.words, at, b) or "New scene")
            step = scenes_mod.split_ops(work, sid, at, title=title, role=_role(e.get("role", "Beat"), f"edits[{k}].role"))
            marks.append({"a": at, "b": at + 1, "kind": "mark", "label": _clip(f"Split “{scene.title}” at “{story.words[at]['w']}”")})
        elif op == "merge":
            later = [(s.startWord, j) for j, s in enumerate(work.scenes) if s.startWord > scene.startWord]
            if not later:
                raise ToolError(f"edits[{k}]: {sid} is the last scene; there is nothing after it to merge")
            j = min(later)[1]
            if j != i + 1:
                raise ToolError(f"edits[{k}]: {work.scenes[j].id} does not follow {sid} in story order; reorder on the Board first")
            step = scenes_mod.merge_ops(work, sid)
            marks.append({"a": a, "b": scene_ranges(work, n)[work.scenes[j].id][1], "kind": "mark",
                          "label": _clip(f"Merge “{scene.title}” + “{work.scenes[j].title}”")})
        elif op == "rename":
            title = _text(e.get("title"), f"edits[{k}].title", SCENE_TITLE_MAX)
            step = scenes_mod.rename_ops(work, sid, title)
            marks.append({"a": a, "b": b, "kind": "mark", "label": _clip(f"Rename “{scene.title}” → “{title}”")})
        elif op == "role":
            role = _role(e.get("role"), f"edits[{k}].role")
            step = scenes_mod.set_role_ops(work, sid, role)
            marks.append({"a": a, "b": b, "kind": "mark", "label": _clip(f"“{scene.title}” → {role}")})
        else:
            raise ToolError(f"edits[{k}].op must be split, merge, rename or role")
        work = _apply(work, step)
        ops += step
    labels = [m["label"] for m in marks]
    title = _title(args, labels[0] if len(labels) == 1 else f"Replan scenes · {_plural(len(labels), 'change')}")
    return Proposal(title, _why(args, " · ".join(labels)), ops, marks)


# ── cut_fillers ─────────────────────────────────────────────────────────────────

def _norm(token: str) -> str:
    return (token or "").casefold().strip(".,!?;:\"'()[]…—-")


def detect_fillers(words: list[dict], lo: int, hi: int, cut: set[int]) -> list[tuple[int, int, str]]:
    """Filler words and false starts in [lo, hi) as inclusive (first, last, label) word runs:
    um/uh-style tokens, a comma-set "you know," / "I mean," / "like,", a cut-off word ("wh-"), and an
    immediate repeat ("I I", "we were we were") whose first copy is the false start."""
    out: list[tuple[int, int, str]] = []
    i = lo
    while i < hi:
        if i in cut:
            i += 1
            continue
        raw = words[i].get("w") or ""
        w = _norm(raw)
        nxt = _norm(words[i + 1].get("w") or "") if i + 1 < hi else ""
        nxt_raw = (words[i + 1].get("w") or "") if i + 1 < hi else ""
        if w in _FILLERS:
            out.append((i, i, f"“{raw.strip(',.')}”"))
        elif (w, nxt) in {("you", "know"), ("i", "mean")} and nxt_raw.endswith(","):
            out.append((i, i + 1, f"“{w} {nxt}”"))
            i += 1
        elif w == "like" and raw.endswith(","):
            out.append((i, i, "“like”"))
        elif raw.rstrip().endswith("-") and w:
            out.append((i, i, f"false start “{raw}”"))
        elif i + 3 < hi and w and (w, nxt) == (_norm(words[i + 2].get("w") or ""), _norm(words[i + 3].get("w") or "")):
            out.append((i, i + 1, f"repeat “{w} {nxt}”"))
            i += 1
        elif w and w == nxt and w not in _LEGIT_REPEATS:
            out.append((i, i, f"repeat “{w}”"))
        i += 1
    return out


def _word_run_tl(story: Story, eff: list[int], first: int, last: int) -> list[tuple[int, int]]:
    """script.selectionCuts: the kept source under words first..last, mapped onto the timeline."""
    a, b = eff[first], eff[last]
    if a < 0 or b < 0:
        return []
    start = _word_frame(story.words[a], story.fps)
    end = timebase.sec_to_frame(story.words[b]["end"], story.fps)
    return _to_timeline(_covered(story.doc, story.media, start, end)) if end > start else []


def _pending_fillers(story: Story) -> list[tuple[dict, int, int]]:
    """Verbatim fillers still on the timeline as (filler, src start, src end): cutmap.fillerToCut's
    padded span, clipped to the neighbouring words so the padding never eats real speech."""
    fps = story.fps
    pad = timebase.sec_to_frame(config.FILLER_PAD_MS / 1000, fps)
    timed = [w for w in story.words if story_insert.is_timed(w)]
    out: list[tuple[dict, int, int]] = []
    for f in story.fillers:
        if not all(isinstance(f.get(k), (int, float)) for k in ("start", "end")):
            continue
        w0, w1 = timebase.sec_to_frame(f["start"], fps), timebase.sec_to_frame(f["end"], fps)
        if w1 <= w0:
            continue
        a, b = w0 - pad, w1 + pad
        prev_end = max((w["end"] for w in timed if w["end"] <= f["start"]), default=None)
        next_start = min((w["start"] for w in timed if w["start"] >= f["end"]), default=None)
        if prev_end is not None:
            a = max(a, timebase.sec_to_frame(prev_end, fps))
        if next_start is not None:
            b = min(b, timebase.sec_to_frame(next_start, fps))
        run = next(((ra, rb) for _c, ra, rb in _covered(story.doc, story.media, a, b) if ra < w1 and w0 < rb), None)
        if run is not None:
            out.append((f, run[0], run[1]))
    return out


def _filler_word_idx(words: list[dict], at_sec: float) -> int:
    """fillersByWord: the word a verbatim filler displays before (the first starting after it)."""
    return next((i for i, w in enumerate(words) if isinstance(w.get("start"), (int, float)) and w["start"] > at_sec),
                len(words))


def _speech_frames(doc: dict) -> int:
    return sum(c["srcOutF"] - c["srcInF"] for c in story_insert.speech_clips(doc))


def cut_fillers(story: Story, args: dict) -> Proposal:
    """Ripple-cut filler words and false starts: detected in scope, or the ranges the model chose."""
    words, n = story.words, len(story.words)
    eff = story_insert.resolve_timed(words)
    cut = cut_words(story.doc, story.media, words)
    runs: list[tuple[int, int, str]] = []
    marks: list[dict] = []
    tl: list[tuple[int, int]] = []
    if args.get("ranges") is not None:
        for k, raw in enumerate(_list(args["ranges"], "ranges", 40)):
            r = _obj(raw, f"ranges[{k}]")
            first = _int(r.get("wordIdx"), f"ranges[{k}].wordIdx", 0, n)
            last = _int(r.get("endWordIdx", first), f"ranges[{k}].endWordIdx", first, n)
            if last - first >= _RANGE_MAX_WORDS:
                raise ToolError(f"ranges[{k}] spans more than {_RANGE_MAX_WORDS} words; cut a false start, not a section")
            quoted = " ".join(w.get("w") or "" for w in words[first:last + 1])
            reason = _text(r["reason"], f"ranges[{k}].reason", 200) if r.get("reason") is not None else None
            runs.append((first, last, _clip(f"“{quoted}” · {reason}" if reason else f"“{quoted}”")))
    else:
        lo, hi = 0, n
        if args.get("scene") is not None:
            _i, sid = _scene(story.project, args["scene"], "scene")
            lo, hi = scene_ranges(story.project, n)[sid]
        runs = detect_fillers(words, lo, hi, cut)
        seen: set[tuple[int, str]] = set()
        for f, a, b in _pending_fillers(story):
            at = _filler_word_idx(words, f["start"])
            label = f"“{(f.get('w') or '').strip(',.')}”"
            # A segment-boundary duplicate in the transcript's verbatim fillers (the same disfluency
            # tagged twice) would otherwise propose the same cut twice; one mark per (word, filler).
            if not (lo <= at < hi or at == hi == n) or (at, label) in seen:
                continue
            seen.add((at, label))
            tl += _to_timeline(_covered(story.doc, story.media, a, b))
            marks.append({"a": at, "b": at, "kind": "cut", "label": label})
    for first, last, label in runs:
        ranges = _word_run_tl(story, eff, first, last)
        if ranges:
            tl += ranges
            marks.append({"a": first, "b": last + 1, "kind": "cut", "label": label})
    if not tl:
        where = "those words are already cut" if args.get("ranges") is not None else "no filler words or false starts found"
        raise NothingToPropose(where)
    frames = edits.resolve_word_frames([(w.get("start"), w.get("end")) for w in words], story.fps)
    try:
        after, warnings = edits.apply_cuts(story.project, tl, word_frames={story.media: frames})
    except ValidationError as e:
        raise ToolError(f"those cuts do not apply: {e}") from e
    after_doc = project_mod.to_dict(after)
    saved = round((_speech_frames(story.doc) - _speech_frames(after_doc)) / story.fps, 2)
    count = len(marks)
    default_title = (f"Cut {_plural(count, 'filler word')}" if args.get("ranges") is None
                     else f"Tighten · cut {_plural(sum(m['b'] - m['a'] for m in marks), 'word')}")
    moved = len({w["itemId"] for w in warnings if w.get("itemId")})
    summary = " · ".join(m["label"] for m in marks[:6]) + (" …" if count > 6 else "")
    summary += f" · saves {saved:g}s" + (f" · {_plural(moved, 'cue')} to check" if moved else "")
    return Proposal(_title(args, default_title), _why(args, summary), _track_ops(story.doc, after_doc), marks, saved)


# ── suggest_broll ───────────────────────────────────────────────────────────────

def library_assets(workspace_id: str | None) -> list[dict]:
    """The drawer's library: the video's own workspace plus the built-in brand kit, as
    {workspaceId, workspaceName, asset} entries."""
    out: list[dict] = []
    ids = [i for i in dict.fromkeys([workspace_id, library.BUILTIN_WORKSPACE_ID])
           if i and i != library.UNSORTED_WORKSPACE_ID]
    for wid in ids:
        try:
            ws = library.get_workspace(wid)
            if ws is not None:
                out += [{"workspaceId": wid, "workspaceName": ws.get("name") or wid, "asset": a}
                        for a in library.list_assets(wid)]
        except (FileNotFoundError, OSError, ValueError):
            continue
    return out


def _terms(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9']+", text.casefold()):
        if len(t) > 2 and t not in _STOP:
            out.add(t[:-1] if t.endswith("s") and len(t) > 3 else t)
    return out


def _asset_terms(asset: dict) -> set[str]:
    name = re.sub(r"\.[^.]+$", "", asset.get("name") or "")
    return _terms(" ".join([name.replace("_", " ").replace("-", " "), *(asset.get("tags") or [])]))


def _pick_asset(entries: list[dict], kind: str, want: set[str], asset_ref: str | None, phrase_s: float, label: str) -> dict:
    pool = [e for e in entries if story_insert.asset_tab(e["asset"]) == kind]
    if not pool:
        raise ToolError(f"{label}: the library has no {_CUE_LABEL[kind]} assets")
    names = ", ".join(sorted({e["asset"].get("name") or "" for e in pool})[:15])
    if asset_ref is not None:
        ref = asset_ref.casefold()
        hit = next((e for e in pool if ref in (e["asset"].get("id", "").casefold(), (e["asset"].get("name") or "").casefold(),
                                                re.sub(r"\.[^.]+$", "", e["asset"].get("name") or "").casefold())), None)
        if hit is None:
            raise ToolError(f"{label}: no {_CUE_LABEL[kind]} asset named {asset_ref!r} (have: {names})")
        return hit

    def rank(e: dict) -> tuple:
        seconds = e["asset"].get("durationS")
        fit = abs(seconds - max(phrase_s, 2.0)) if isinstance(seconds, (int, float)) else 0.0
        return (-len(want & _asset_terms(e["asset"])), fit, e["asset"].get("name") or "")

    best = min(pool, key=rank)
    if want and not want & _asset_terms(best["asset"]):
        raise ToolError(f"{label}: nothing in the library matches {' '.join(sorted(want))} (have: {names}); pass asset=<name>")
    return best


def suggest_broll(story: Story, args: dict, assets: list[dict]) -> Proposal:
    """Word-anchored cues from the workspace + brand library, placed exactly as the drawer would."""
    words, n = story.words, len(story.words)
    doc = story.doc
    marks: list[dict] = []
    heads: list[str] = []
    for k, raw in enumerate(_list(args.get("cues"), "cues", 8)):
        cue = _obj(raw, f"cues[{k}]")
        first = _int(cue.get("wordIdx"), f"cues[{k}].wordIdx", 0, n)
        last = _int(cue.get("endWordIdx", first), f"cues[{k}].endWordIdx", first, n)
        kind = cue.get("kind", "broll")
        if kind not in _CUE_LABEL:
            raise ToolError(f"cues[{k}].kind must be broll, music or sfx")
        query = _text(cue["query"], f"cues[{k}].query", 120) if cue.get("query") is not None else None
        asset_ref = _text(cue["asset"], f"cues[{k}].asset", 200) if cue.get("asset") is not None else None
        phrase = " ".join(w.get("w") or "" for w in words[first:last + 1])
        s0, s1 = words[first].get("start"), words[last].get("end")
        phrase_s = s1 - s0 if isinstance(s0, (int, float)) and isinstance(s1, (int, float)) else 0.0
        want = _terms(query if query is not None else phrase) if kind == "broll" or query is not None else set()
        entry = _pick_asset(assets, kind, want, asset_ref, phrase_s, f"cues[{k}]")
        try:
            doc, _id, _tl = story_insert.insert_asset(doc, words, story.media, first, entry["workspaceId"], entry["asset"])
        except ValueError as e:
            raise ToolError(f"cues[{k}]: word {first} is cut, so nothing can start on it") from e
        heads.append(f"{_CUE_LABEL[kind]}: {entry['asset'].get('name') or entry['asset'].get('id')}")
        marks.append({"a": first, "b": last + 1, "kind": "mark", "label": _clip(f"{heads[-1]} at “{_clip(phrase, 24)}”")})
    try:
        after = project_mod.from_dict(doc)
        project_mod.validate(after)
    except (ValidationError, KeyError, TypeError, ValueError) as e:
        raise ToolError(f"those cues do not fit: {e}") from e
    title = _title(args, _clip(f"Add {heads[0]}") if len(heads) == 1 else f"Add {_plural(len(heads), 'cue')}")
    why = " · ".join(m["label"] for m in marks)
    return Proposal(title, _why(args, why), _track_ops(story.doc, project_mod.to_dict(after)), marks)


# ── set_shot ────────────────────────────────────────────────────────────────────

def _shot_label(title: str, shot: dict) -> str:
    mode = shot.get("mode", "footage")
    head = {"animation": f"Animate “{title}”", "cameo": f"Character cameo in “{title}”"}.get(mode, f"Footage for “{title}”")
    extra = [shot[k] for k in ("layout", "camera", "expression", "pose", "move", "bubble") if shot.get(k)]
    if isinstance(shot.get("costar"), dict):
        extra += [f"with {shot['costar']['character']}", *([shot["interaction"]] if shot.get("interaction", "none") != "none" else [])]
    if shot.get("ensemble"):
        extra.append(f"+{len(shot['ensemble'])} more")
    return _clip(" · ".join([head, *extra]))


def _character(value: object, cast: dict, label: str) -> str:
    ids = [c["id"] for c in cast["characters"]]
    if value not in ids:
        raise ToolError(f"{label} must be one of {', '.join(ids)}")
    return str(value)


def _rig_of(profile: dict) -> dict:
    return cast_mod.RIGS.get(str(profile.get("rig")), cast_mod.RIGS[cast_mod.DEFAULT_RIG])


def _wearable(profile: dict) -> list[str]:
    """The expressions a scene can give this profile: its rig's own and its approved custom ones."""
    return [*_rig_of(profile)["expressions"], *(e["id"] for e in profile.get("expressions") or [] if not e.get("draft"))]


def _looks(given: dict, look: dict, cast: dict, label: str) -> None:
    """Merge the pose, expression and move in `given` over `look` (null clears one), each checked
    against the profile `look` casts: its rig's own, plus the profile's custom expressions."""
    profile = cast_mod.character_for(cast, look.get("character"))
    rig = _rig_of(profile)
    for key, value in given.items():
        if key not in LOOK_SETS:
            continue
        allowed = _wearable(profile) if key == "expression" else list(rig[LOOK_SETS[key]])
        if value is None:
            look.pop(key, None)
        elif value in allowed:
            look[key] = value
        else:
            raise ToolError(f"{label}.{key} must be one of {', '.join(allowed)} for {profile.get('name')}")


def _recast(look: dict, character: str | None, cast: dict) -> None:
    """Cast `look` as another character (None: the video's default), dropping an expression it can't
    wear, as the Shot view does."""
    if character is None:
        look.pop("character", None)
    else:
        look["character"] = character
    if look.get("expression") not in (None, *_wearable(cast_mod.character_for(cast, character))):
        del look["expression"]


def _costar(value: object, current: object, cast: dict, label: str) -> dict:
    """A co-star's (or an ensemble member's) fields merged over its current ones (null clears one): a
    character the video can cast, look ids its rig has, and a bubble."""
    given = _obj(value, label)
    unknown = set(given) - {"character", "bubble", *LOOK_SETS}
    if unknown:
        raise ToolError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    costar = dict(current) if isinstance(current, dict) else {}
    if given.get("character") is not None:
        _recast(costar, _character(given["character"], cast, f"{label}.character"), cast)
    elif "character" in given or "character" not in costar:
        raise ToolError(f"{label}.character is required: one of {', '.join(c['id'] for c in cast['characters'])}")
    _looks(given, costar, cast, label)
    if "bubble" in given:
        bubble = given["bubble"]
        if bubble is None:
            costar.pop("bubble", None)
        elif bubble in BUBBLES:
            costar["bubble"] = bubble
        else:
            raise ToolError(f"{label}.bubble must be one of {', '.join(BUBBLES)}")
    return costar


def _ensemble(value: object, cast: dict, label: str) -> list[dict]:
    """A whole new ensemble: up to ENSEMBLE_MAX performers, each given like a new co-star."""
    return [_costar(m, None, cast, f"{label}[{j}]") for j, m in enumerate(_list(value, label, ENSEMBLE_MAX))]


def set_shot(story: Story, args: dict, assets: Callable[[str | None], list[dict]] = library_assets) -> Proposal:
    """Direct each scene's shot (mode, its character and that character's pose/expression/move and
    bubble, backdrop, diagram, headline, whether the headline and eyebrow draw at all or go over the
    co-star, cue word, a co-star and its interaction, an ensemble, and its staging: camera, layout, the
    layouts' screen image, badges and mark, and a content-sized board's one to six labels). Board
    layouts are independent; blank entries draw empty nodes and omitted labels draw three blank nodes.
    Given fields merge over the scene's current shot, null clears one (a null costar takes its interaction with it), and an ensemble
    replaces the scene's whole line-up. Each
    look id is checked against the rig of the character it lands on, and a screen image against the
    video's library."""
    work = story.project
    n = len(story.words)
    ops: list[dict] = []
    marks: list[dict] = []
    cast = cast_mod.video_cast(work.workspaceId)
    for k, raw in enumerate(_list(args.get("shots"), "shots", 16)):
        s = _obj(raw, f"shots[{k}]")
        i, sid = _scene(work, s.get("scene"), f"shots[{k}].scene")
        a, b = scene_ranges(work, n)[sid]
        unknown = set(s) - {"scene", "character", *SHOT_IDS, *LOOK_SETS, "headline", "hideHeadline",
                             "cueWord", "costar", "ensemble", *staging.ARGS}
        if unknown:
            raise ToolError(f"shots[{k}] has unknown fields: {', '.join(sorted(unknown))}")
        shot = dict(work.scenes[i].shot or {})
        if "character" in s:
            _recast(shot, None if s["character"] is None else _character(s["character"], cast, f"shots[{k}].character"), cast)
        _looks(s, shot, cast, f"shots[{k}]")
        for key, value in s.items():
            if key in ("scene", "character", *LOOK_SETS):
                continue
            if value is None:
                shot.pop(key, None)
                if key == "costar":
                    shot.pop("interaction", None)
            elif key == "costar":
                shot[key] = _costar(value, shot.get(key), cast, f"shots[{k}].costar")
            elif key == "ensemble":
                shot[key] = _ensemble(value, cast, f"shots[{k}].ensemble")
            elif key == "headline":
                shot[key] = _text(value, f"shots[{k}].headline", _HEADLINE_MAX)
            elif key == "hideHeadline":
                shot[key] = _bool(value, f"shots[{k}].hideHeadline")
            elif key == "cueWord":
                shot[key] = _int(value, f"shots[{k}].cueWord", a, b)
            elif key in staging.ARGS:
                try:
                    shot[key] = staging.shot_arg(key, value, f"shots[{k}].{key}", lambda: assets(work.workspaceId))
                except ValueError as e:
                    raise ToolError(str(e)) from e
            elif value in SHOT_IDS[key]:
                shot[key] = value
            else:
                raise ToolError(f"shots[{k}].{key} must be one of {', '.join(SHOT_IDS[key])}")
        if shot == (work.scenes[i].shot or {}):
            continue
        step = scenes_mod.set_shot_ops(work, sid, shot or None)
        marks.append({"a": a, "b": b, "kind": "mark", "label": _shot_label(work.scenes[i].title, shot)})
        work = _apply(work, step)
        ops += step
    if not ops:
        raise NothingToPropose("those shots are already set")
    labels = [m["label"] for m in marks]
    title = _title(args, labels[0].split(" · ")[0] if len(labels) == 1 else f"Direct {_plural(len(labels), 'scene')}")
    return Proposal(title, _why(args, " · ".join(labels)), ops, marks)


# ── dispatch ────────────────────────────────────────────────────────────────────

TOOLS = ("plan_scenes", "cut_fillers", "suggest_broll", "set_shot")


def propose(tool: str, doc: dict, tdoc: dict | None, args: object,
            assets: Callable[[str | None], list[dict]] = library_assets) -> Proposal:
    """Build `tool`'s proposal against the current doc + transcript. Raises ToolError to revise."""
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    story = _story(doc, tdoc)
    if tool == "plan_scenes":
        return plan_scenes(story, args)
    if tool == "cut_fillers":
        return cut_fillers(story, args)
    if tool == "suggest_broll":
        return suggest_broll(story, args, assets(story.project.workspaceId))
    if tool == "set_shot":
        return set_shot(story, args, assets)
    raise ToolError(f"unknown tool {tool!r}")
