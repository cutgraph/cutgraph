"""A video's thumbnail: a still poster drawn by motion-core, exported as a PNG.

`thumbnail_of` fills a video's stored `thumbnail` (pipeline.project) with the video's defaults: its title
as the headline, its first animated scene's lead as the lead, and the rest of its cast (up to three)
standing behind. `render_request` resolves every profile against the cast exactly as a scene's shot is
(`pipeline.cast.resolve_shot`), and `motion-core/bin/render-poster.ts` draws it with no browser, the same
engine the Export sheet's preview draws with; ui/src/v3/story/thumbnail/thumbnail.ts is the preview's
half, and tests/fixtures/thumbnail_parity.json holds the two together. PNGs are content-addressed under
`outputs/thumbnails/` and trimmed least recently used first. No ffmpeg is involved.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import config
from pipeline import cast as cast_mod
from pipeline import project as project_mod
from pipeline import scene_render
from pipeline.runtime_tools import node_path

RENDERER = scene_render.MOTION_CORE / "bin" / "render-poster.ts"
# A YouTube thumbnail and a Shorts or Reels cover (motion-core POSTER_FORMATS).
FORMATS: dict[str, tuple[int, int]] = {"16:9": (1280, 720), "9:16": (1080, 1920)}
CACHE_MAX_BYTES = 64 * 1024 * 1024
_ANIMATED = ("animation", "cameo")


class ThumbnailRenderError(RuntimeError):
    pass


def cache_dir() -> Path:
    return config.OUTPUTS_DIR / "thumbnails"


def first_lead(doc: Mapping) -> str | None:
    """The character profile the video's first animated scene casts, or None when it names none (or
    the video has no animated scene): the cast's default then."""
    for scene in doc.get("scenes") or []:
        shot = scene.get("shot") if isinstance(scene, Mapping) else None
        if isinstance(shot, Mapping) and shot.get("mode") in _ANIMATED:
            character = shot.get("character")
            return character if isinstance(character, str) and character else None
    return None


def thumbnail_of(doc: Mapping, cast: Mapping | None) -> dict:
    """The video's thumbnail as stored, with its defaults filled in (profile ids, unresolved): the
    headline is the video's title (else its name), the eyebrow is hidden, the lead is the first
    animated scene's, and without a stored `ensemble` the cast's other characters stand behind it, in
    cast order, up to ENSEMBLE_MAX."""
    stored = doc.get("thumbnail")
    stored = dict(stored) if isinstance(stored, Mapping) else {}
    title = doc.get("title") if isinstance(doc.get("title"), str) and doc.get("title") else doc.get("name")
    out: dict = {"headline": title if isinstance(title, str) else "", "eyebrow": "", **stored}
    lead = stored.get("character") or first_lead(doc)
    if lead:
        out["character"] = lead
    if "ensemble" not in stored:
        lead_id = cast_mod.character_for(cast, lead).get("id")
        others = [c["id"] for c in (cast or {}).get("characters") or []
                  if isinstance(c, Mapping) and isinstance(c.get("id"), str) and c["id"] != lead_id]
        out["ensemble"] = [{"character": c} for c in others[:project_mod.ENSEMBLE_MAX]]
    return out


def render_request(doc: Mapping, cast: Mapping | None, fmt: str) -> dict:
    """The poster renderer's input for one format: the thumbnail with its defaults, every profile
    resolved to its rig and palette (a custom expression's spec riding as `custom`, a member's inside
    it) and the brand's accent, as `pipeline.cast.resolve_shot` resolves a scene's shot."""
    width, height = FORMATS[fmt]
    poster, custom = cast_mod.resolve_shot(thumbnail_of(doc, cast), cast or {})
    return {"width": width, "height": height, "poster": {**poster, **({"custom": custom} if custom else {})}}


def renderer_version() -> str:
    """Content hash of everything that draws a poster: motion-core, the Node env and this renderer."""
    h = hashlib.sha256(scene_render.motion_core_version().encode())
    h.update(RENDERER.read_bytes())
    return h.hexdigest()


def cache_key(request: dict, version: str) -> str:
    return hashlib.sha256(json.dumps({**request, "motionCore": version}, sort_keys=True).encode()).hexdigest()


def render_thumbnail(request: dict, dest: Path) -> dict:
    """Render one poster with the Node renderer into `dest`, atomically. Returns its `done` event."""
    try:
        node = node_path()
    except RuntimeError as error:
        raise ThumbnailRenderError(str(error)) from error
    if node is None:
        raise ThumbnailRenderError("Node.js 24+ is required to render thumbnails")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.stem}.partial{dest.suffix}")
    command = [str(node), str(RENDERER), "-", str(partial)]
    if config.PACKS_DIR.is_dir():
        command.extend(["--packs", str(config.PACKS_DIR)])
    proc = subprocess.run(command, input=json.dumps(request),
                          capture_output=True, encoding="utf-8", errors="replace", cwd=scene_render.MOTION_CORE)
    done: dict = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event") == "done":
            done = event
    if proc.returncode != 0 or not done or not partial.exists():
        partial.unlink(missing_ok=True)
        raise ThumbnailRenderError(f"thumbnail render failed: {(proc.stderr or proc.stdout).strip()[-600:]}")
    os.replace(partial, dest)
    return done


def ensure_thumbnail(doc: Mapping, fmt: str) -> Path:
    """The video's thumbnail PNG at `fmt`, rendered unless the cache already holds it, then the cache
    trimmed around it."""
    workspace = doc.get("workspaceId")
    cast = cast_mod.video_cast(workspace if isinstance(workspace, str) else None)
    request = render_request(doc, cast, fmt)
    dest = cache_dir() / f"{cache_key(request, renderer_version())}.png"
    try:
        os.utime(dest)  # a hit is a use: eviction goes by mtime
    except FileNotFoundError:
        render_thumbnail(request, dest)
    scene_render.evict_scenes(cache_dir(), keep=[dest], cap=CACHE_MAX_BYTES)
    return dest
