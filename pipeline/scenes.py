"""Scenes: deterministic topical grouping of a transcript, plus patch builders for scene edits.

`group` reads a words.json (schema.TranscriptResult) and splits its flat wordIdx space (segments→words
enumerated in order; fillers are never indexed) into 3–8 scenes. Candidate boundaries are sentence
ends and segment starts, scored by the pause before them; the strongest candidate nearest each evenly
spaced target wins, so scenes stay balanced.

Scene edits are ordinary RFC-6902 patches for the revision-gated PATCH route. Every `*_ops` builder
leads with a `test` on the targeted scene id, so a patch built against one state fails cleanly on
another instead of editing the wrong scene. Dependency-free: stdlib + pipeline.project.
"""
from __future__ import annotations

import re

from pipeline.project import SCENE_TITLE_MAX, Project

_MIN_SCENES, _MAX_SCENES = 3, 8
_TARGET_SCENE_S = 90.0
_PAUSE_CAP_S = 2.0
_BALANCE_WEIGHT = 3.0
_TITLE_WORDS = 6
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
_TITLE_TRIM = " \t.,;:!?-\"'()[]"
_TRAILING_FILLER = {
    "a", "an", "and", "are", "at", "but", "for", "in", "is", "it", "of", "on", "or", "so", "that",
    "the", "to", "was", "with",
}


# ── Grouping ──────────────────────────────────────────────────────────────────────

def flat_words(words_doc: dict) -> list[dict]:
    """The wordIdx contract: `{w, start, end, segIdx}` per word, in segments→words order."""
    return [
        {"w": (w.get("word") or "").strip(), "start": float(w["start"]), "end": float(w["end"]),
         "segIdx": seg_idx}
        for seg_idx, seg in enumerate(words_doc.get("segments", []))
        for w in seg.get("words", [])
    ]


def word_ranges(start_words: list[int], word_count: int) -> list[tuple[int, int]]:
    """Each scene's [start, end) in list order: it runs to the next-larger startWord, or to the end."""
    ordered = sorted(start_words)
    ends = dict(zip(ordered, ordered[1:] + [word_count]))
    return [(s, ends[s]) for s in start_words]


def title_for(words: list[dict], start: int, end: int) -> str:
    """The scene's opening words: at most one sentence and `_TITLE_WORDS` words, trimmed of edge
    punctuation and dangling function words, first letter capitalized. Empty when nothing is left."""
    picked: list[str] = []
    for w in words[start:min(end, start + _TITLE_WORDS)]:
        if w["w"]:
            picked.append(w["w"])
        if _SENTENCE_END.search(w["w"]):
            break
    while len(picked) > 1 and picked[-1].strip(_TITLE_TRIM).lower() in _TRAILING_FILLER:
        picked.pop()
    title = " ".join(picked).strip(_TITLE_TRIM)[:SCENE_TITLE_MAX].rstrip()
    return title[:1].upper() + title[1:]


def _strength(words: list[dict], b: int) -> float:
    """How strongly a scene wants to start at word `b`: the pause before it (capped), whether the
    previous word ends a sentence, and whether a new segment starts."""
    prev, cur = words[b - 1], words[b]
    score = min(max(cur["start"] - prev["end"], 0.0), _PAUSE_CAP_S) / _PAUSE_CAP_S
    if _SENTENCE_END.search(prev["w"]):
        score += 0.5
    if cur["segIdx"] != prev["segIdx"]:
        score += 0.25
    return score


def _scene_count(words: list[dict]) -> int:
    span = words[-1]["end"] - words[0]["start"]
    return min(max(round(span / _TARGET_SCENE_S), _MIN_SCENES), _MAX_SCENES, len(words))


def _boundaries(words: list[dict], k: int) -> list[int]:
    """Greedily pick k-1 increasing boundaries, each the best-scoring candidate for its evenly spaced
    target (strength minus distance from target), always leaving enough candidates for the rest."""
    n = len(words)
    cands = [b for b in range(1, n)
             if words[b]["segIdx"] != words[b - 1]["segIdx"] or _SENTENCE_END.search(words[b - 1]["w"])]
    if len(cands) < k - 1:
        cands = list(range(1, n))
    span = words[-1]["end"] - words[0]["start"]
    if span > 0:
        pos = [w["start"] - words[0]["start"] for w in words]
        total = span
    else:
        pos, total = [float(i) for i in range(n)], float(n)
    ideal = total / k
    strength = {b: _strength(words, b) for b in cands}
    picks: list[int] = []
    lo = 0
    for i in range(1, k):
        target = i * ideal
        hi = len(cands) - (k - 1 - i)
        best = max(range(lo, hi), key=lambda j: (
            strength[cands[j]] - _BALANCE_WEIGHT * abs(pos[cands[j]] - target) / ideal, -j))
        picks.append(cands[best])
        lo = best + 1
    return picks


def _roles(k: int) -> list[str]:
    return ["Beat"] if k == 1 else ["Hook"] + ["Beat"] * (k - 2) + ["Wrap"]


def group(words_doc: dict) -> list[dict]:
    """Propose scenes (JSON-shaped, word order) for a words.json. Deterministic: same inputs, same
    scenes. No words yields no scenes."""
    words = flat_words(words_doc)
    if not words:
        return []
    k = _scene_count(words)
    starts = [0] + _boundaries(words, k)
    ends = starts[1:] + [len(words)]
    return [
        {"id": f"scene-{i + 1}", "title": title_for(words, s, e) or f"Scene {i + 1}", "role": role,
         "startWord": s}
        for i, (s, e, role) in enumerate(zip(starts, ends, _roles(k)))
    ]


# ── Patch builders (applied through the PATCH route / project.apply_ops) ──────────

def _index(p: Project, scene_id: str) -> int:
    for i, s in enumerate(p.scenes):
        if s.id == scene_id:
            return i
    raise ValueError(f"unknown scene: {scene_id}")


def _guard(i: int, scene_id: str) -> dict:
    return {"op": "test", "path": f"/scenes/{i}/id", "value": scene_id}


def _next_in_word_order(p: Project, i: int) -> int | None:
    start = p.scenes[i].startWord
    later = [(s.startWord, j) for j, s in enumerate(p.scenes) if s.startWord > start]
    return min(later)[1] if later else None


def _fresh_id(p: Project) -> str:
    taken = {s.id for s in p.scenes}
    n = len(p.scenes) + 1
    while f"scene-{n}" in taken:
        n += 1
    return f"scene-{n}"


def set_scenes_ops(scenes: list[dict]) -> list[dict]:
    """Replace the whole scene list. `add` on an object member creates or replaces it, so this holds
    whether or not the document currently emits `scenes`."""
    return [{"op": "add", "path": "/scenes", "value": scenes}]


def split_ops(p: Project, scene_id: str, word_idx: int, *, title: str = "New scene",
              role: str = "Beat") -> list[dict]:
    """Start a new scene at `word_idx`, which must fall strictly inside `scene_id`'s range. The new
    scene follows it in story order and takes the words from `word_idx` onward."""
    i = _index(p, scene_id)
    j = _next_in_word_order(p, i)
    if not (p.scenes[i].startWord < word_idx and (j is None or word_idx < p.scenes[j].startWord)):
        raise ValueError(f"word {word_idx} is not strictly inside scene {scene_id}")
    scene = {"id": _fresh_id(p), "title": title, "role": role, "startWord": word_idx}
    return [_guard(i, scene_id), {"op": "add", "path": f"/scenes/{i + 1}", "value": scene}]


def merge_ops(p: Project, scene_id: str) -> list[dict]:
    """Merge `scene_id` with the scene that follows it in word order: the follower is removed and its
    words fall to `scene_id`, which keeps its title, role, and shot."""
    i = _index(p, scene_id)
    j = _next_in_word_order(p, i)
    if j is None:
        raise ValueError(f"scene {scene_id} has no following scene to merge")
    return [_guard(i, scene_id), _guard(j, p.scenes[j].id), {"op": "remove", "path": f"/scenes/{j}"}]


def rename_ops(p: Project, scene_id: str, title: str) -> list[dict]:
    i = _index(p, scene_id)
    return [_guard(i, scene_id), {"op": "replace", "path": f"/scenes/{i}/title", "value": title}]


def set_role_ops(p: Project, scene_id: str, role: str) -> list[dict]:
    i = _index(p, scene_id)
    return [_guard(i, scene_id), {"op": "replace", "path": f"/scenes/{i}/role", "value": role}]


def move_ops(p: Project, scene_id: str, to_index: int) -> list[dict]:
    """Reorder the story: move `scene_id` to `to_index` in the list. Word coverage is unchanged
    because a scene's range follows startWord, not list position."""
    i = _index(p, scene_id)
    if not 0 <= to_index < len(p.scenes):
        raise ValueError(f"scene index out of range: {to_index}")
    if i == to_index:
        return []
    return [_guard(i, scene_id), {"op": "move", "from": f"/scenes/{i}", "path": f"/scenes/{to_index}"}]


def set_shot_ops(p: Project, scene_id: str, shot: dict | None) -> list[dict]:
    """Set (or with None, clear) the animated-scene direction for `scene_id`."""
    i = _index(p, scene_id)
    if shot is None:
        if p.scenes[i].shot is None:
            return []
        return [_guard(i, scene_id), {"op": "remove", "path": f"/scenes/{i}/shot"}]
    return [_guard(i, scene_id), {"op": "add", "path": f"/scenes/{i}/shot", "value": shot}]
