"""Short-film staging on a scene's shot: the host's half of motion-core's staging
(`motion-core/src/staging.ts`, `motion-core/STAGING.md`).

`pipeline.project` validates the stored fields (`camera`, `layout`, `screen`, `badges`, `mark`, the independent
diagram's `boardLabels`). Here a
`screen` shot's library images are resolved for the export: each phone's asset id (`asset`, then each of
`more`) becomes the file the Node renderer decodes, looked up exactly as the preview looks it up
(ui/src/v3/story/shot/screenLibrary.ts): the video's own workspace library first, then the brand kit,
image assets only. The co-pilot's `set_shot` arguments for the layouts' fields are checked and
normalised here too; `camera` (with its pan) and `headlineAt` ride in `copilot.SHOT_IDS`.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from pipeline import library
from pipeline.project import (
    BADGE_TONES,
    SHOT_BADGE_TEXT_MAX,
    SHOT_BADGES_MAX,
    SHOT_BOARD_LABEL_MAX,
    SHOT_BOARD_LABELS_MAX,
    SHOT_MARK_PATTERN,
    SHOT_SCREEN_MORE_MAX,
)

# set_shot's staging fields beside its id fields (`camera` and `layout` ride in copilot.SHOT_IDS).
ARGS = ("screen", "badges", "mark", "boardLabels")

TOOL_PROPS = {
    "screen": {"type": ["object", "null"], "description": "layout screen: phone 1 shows this library image, "
                                                         "phones 2 and 3 (optional) show `more`",
               "properties": {"asset": {"type": "string", "description": "a library image's name or id"},
                              "more": {"type": "array", "maxItems": SHOT_SCREEN_MORE_MAX, "items": {"type": "string"},
                                       "description": "1-2 more library images' names or ids, beside phone 1"}}},
    "badges": {"type": ["array", "null"], "description": "layout title: 1-4 badges dropping in above the headline; "
                                                         "on a stage with an ensemble, one under each member",
               "items": {"type": "object", "properties": {"text": {"type": "string"},
                                                          "tone": {"type": "string", "enum": list(BADGE_TONES)}},
                         "required": ["text"]}},
    "mark": {"type": ["string", "null"], "description": "layout signoff: 1-4 optional pixel text characters "
                                                       "(omitted means no mark)"},
    "boardLabels": {"type": ["array", "null"], "maxItems": SHOT_BOARD_LABELS_MAX,
                    "items": {"type": "string", "maxLength": SHOT_BOARD_LABEL_MAX},
                    "description": "content-sized boards: 1-6 short labels in node order; blank entries draw empty boxes. "
                                   "Omitted labels draw three blank nodes. "
                                   "Grid by row; chain along its path; radial centre then clockwise from the top; "
                                   "tree by level; list top to bottom"},
}


def _libraries(workspace_id: str | None) -> list[str]:
    return [w for w in dict.fromkeys([workspace_id, library.BUILTIN_WORKSPACE_ID])
            if w and w != library.UNSORTED_WORKSPACE_ID]


def screen_image(asset_id: str, workspace_id: str | None) -> Path | None:
    """The file of a `screen` shot's image asset: the video's workspace library's, else the brand
    kit's, or None when neither holds it as an image."""
    for wid in _libraries(workspace_id):
        try:
            asset = library.get_asset(wid, asset_id)
        except (FileNotFoundError, library.LibraryError):
            continue
        if asset is not None and asset.get("kind") == "image":
            path = library.asset_file_path(wid, asset_id)
            if path is not None:
                return path
    return None


def _resolved(asset_id: str, workspace_id: str | None) -> dict:
    path = screen_image(asset_id, workspace_id)
    return {"asset": asset_id, **({"image": str(path.resolve())} if path is not None else {})}


def resolve_screen(shot: dict, workspace_id: str | None) -> dict:
    """The shot as the renderer draws it: each `screen` phone whose asset the libraries hold gains
    `image`, its file; `more`'s asset ids become `{asset, image?}` the same way. A phone whose asset they
    don't hold draws the generated app screen, as in the preview."""
    screen = shot.get("screen")
    if not isinstance(screen, dict) or not isinstance(screen.get("asset"), str):
        return shot
    more = screen.get("more")
    return {**shot, "screen": {**screen, **_resolved(screen["asset"], workspace_id),
                               **({"more": [_resolved(a, workspace_id) for a in more]} if isinstance(more, list) else {})}}


def _keyed(phone: dict) -> dict:
    return {**phone, "image": Path(phone["image"]).name} if isinstance(phone.get("image"), str) else phone


def keyed_shot(shot: dict) -> dict:
    """The shot as a scene's cache key sees it: every resolved image by its content-addressed file name
    (`<sha256 prefix><ext>`), not by where the state home keeps it."""
    screen = shot.get("screen")
    if not isinstance(screen, dict):
        return shot
    more = screen.get("more")
    return {**shot, "screen": {**_keyed(screen), **({"more": [_keyed(p) for p in more]} if isinstance(more, list) else {})}}


def _stem(name: str) -> str:
    return re.sub(r"\.[^.]+$", "", name)


def shot_arg(key: str, value: object, label: str, images: Callable[[], list[dict]]) -> object:
    """A set_shot argument for one of ARGS, checked and normalised as the project stores it. `images`
    lists the video's library entries ({workspaceId, asset}); a screen names an image by name or id.
    Raises ValueError worded for the model."""
    if key == "mark":
        if not isinstance(value, str) or not re.match(SHOT_MARK_PATTERN, value.strip()):
            raise ValueError(f"{label} must be 1 to 4 letters, digits or &")
        return value.strip().upper()
    if key == "boardLabels":
        labels = [" ".join(v.split()) for v in value] if isinstance(value, list) and all(isinstance(v, str) for v in value) else None
        if not labels or len(labels) > SHOT_BOARD_LABELS_MAX or not any(labels) \
                or any(len(v) > SHOT_BOARD_LABEL_MAX for v in labels):
            raise ValueError(f"{label} must list 1 to {SHOT_BOARD_LABELS_MAX} labels of at most {SHOT_BOARD_LABEL_MAX} "
                             "characters, not all blank")
        return labels
    if key == "badges":
        if not isinstance(value, list) or not 1 <= len(value) <= SHOT_BADGES_MAX:
            raise ValueError(f"{label} must list 1 to {SHOT_BADGES_MAX} badges")
        badges = []
        for i, badge in enumerate(value):
            if not isinstance(badge, dict) or set(badge) - {"text", "tone"}:
                raise ValueError(f"{label}[{i}] must be {{text, tone?}}")
            text = " ".join(badge["text"].split()) if isinstance(badge.get("text"), str) else ""
            if not text or len(text) > SHOT_BADGE_TEXT_MAX:
                raise ValueError(f"{label}[{i}].text must be 1 to {SHOT_BADGE_TEXT_MAX} characters")
            tone = badge.get("tone", "ink")
            if tone not in BADGE_TONES:
                raise ValueError(f"{label}[{i}].tone must be one of {', '.join(BADGE_TONES)}")
            badges.append({"text": text, "tone": tone})
        return badges
    if not isinstance(value, dict) or "asset" not in value or set(value) - {"asset", "more"} \
            or not isinstance(value["asset"], str):
        raise ValueError(f"{label} must be {{asset: a library image's name or id, more?: 1-2 more}}")
    more = value.get("more", [])
    if not isinstance(more, list) or len(more) > SHOT_SCREEN_MORE_MAX or not all(isinstance(m, str) for m in more):
        raise ValueError(f"{label}.more must list up to {SHOT_SCREEN_MORE_MAX} library images' names or ids")
    pool = [e["asset"] for e in images() if e["asset"].get("kind") == "image"]

    def find(given: str, where: str) -> str:
        ref = given.strip().casefold()
        hit = next((a for a in pool if ref in (str(a.get("id", "")).casefold(), str(a.get("name") or "").casefold(),
                                               _stem(str(a.get("name") or "")).casefold())), None)
        if hit is None:
            names = ", ".join(sorted({str(a.get("name") or a.get("id")) for a in pool})[:15]) or "none"
            raise ValueError(f"{where}: no library image named {given!r} (have: {names})")
        return hit["id"]

    ids = [find(m, f"{label}.more[{i}]") for i, m in enumerate(more)]
    return {"asset": find(value["asset"], f"{label}.asset"), **({"more": ids} if ids else {})}
