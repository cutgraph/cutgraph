"""The canonical project.json model.

One EDL: a Project is a list of Tracks; each Track holds Clips (media) or TextItems (text
kind). All cut points/durations are INTEGER FRAMES at project.fps — seconds only exist at the
render boundary (pipeline.compile). Media is always a {source, proxy?} object:
render resolves source, the UI prefers proxy. schemaVersion drives an OpenCut-style migration chain
so any older document loads.

Dependencies are stdlib + config only. jsonpatch is imported lazily inside apply_ops
(the only op-log caller) so the module itself stays import-light.
"""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

import config
from pipeline.errors import ValidationError
from pipeline.names import check_case_collision, exact_path, project_name

CURRENT_SCHEMA_VERSION = 5
_WRITE_LOCK = threading.RLock()


@contextmanager
def write_lock() -> Iterator[None]:
    """Serialize project mutations at their atomic write point.

    Studio runs one application worker. Project routes share this process-wide
    reentrant lock so their writes use one local writer boundary.
    """
    with _WRITE_LOCK:
        yield


_ALLOWED_KINDS = {"speech", "video", "text", "audio"}
_ANCHOR_MODES = {"word", "time"}
# Every id ends up interpolated into a filename or an ffmpeg filtergraph token, so it must never
# carry a path/metacharacter. Lock it to a slug charset with a sane length bound.
# gainDb/duckDb are interpolated into `volume=`/`sidechaincompress` — bound them to a sane audio
# range and reject non-numbers (a string would inject filters, a bool would coerce to 0/1).
_DB_MIN, _DB_MAX = -60.0, 12.0
_TRANSFORM_FITS = {"fill", "contain", "cover"}
_TRANSFORM_SCALE_MIN, _TRANSFORM_SCALE_MAX = 0.1, 4.0
SCENE_ROLES = ("Hook", "Problem", "Approach", "Build", "Example", "Check", "Payoff", "Wrap", "Beat")
SCENE_TITLE_MAX = 120
_SHOT_TEXT_FIELDS = {"character", "pose", "expression", "move", "backdrop", "diagram", "headline", "eyebrow"}
# The line over an animated headline: the shot's own, or "" to hide it; absent = the workspace brand.
SHOT_EYEBROW_MAX = 40
# Animation only: true draws no headline or eyebrow on the stage, a duo or an ensemble; a
# screen, title or sign-off layout ignores it. Absent or false draws exactly as before.
_SHOT_BOOL_FIELDS = {"hideHeadline"}
# How a scene is pictured: its footage, a full-frame animation, or the default rig as an overlay on footage.
SHOT_MODES = ("footage", "animation", "cameo")
# A duo shot's second character: a cast profile id plus its own look, and how the two interact.
_COSTAR_FIELDS = {"character", "pose", "expression", "move", "bubble"}
INTERACTIONS = ("none", "face", "fistbump", "highfive", "point")
# Acting: a bubble over any performer (a "?" or two simultaneous neutral "!!" marks), and an ensemble after the lead and co-star.
BUBBLES = ("think", "say", "exclaim", "question", "exclaims")
ENSEMBLE_MAX = 3
_ACTING_FIELDS = {"bubble", "ensemble"}
# A script-origin video: its words are the written script, timed by estimate until a narration
# take is recorded and aligned to them.
NARRATION_STATES = ("estimated", "recorded")


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class Media:
    source: str
    proxy: Optional[str] = None


@dataclass
class Anchor:
    mode: str                                # "word" | "time"
    ref: Optional[dict] = None               # {"media": str, "wordIdx": int} for word mode
    offsetF: int = 0


@dataclass
class Clip:
    id: str
    media: Media
    srcInF: int
    srcOutF: int
    tlStartF: int
    gainDb: float = 0.0
    anchor: Optional[Anchor] = None
    duckDb: Optional[float] = None
    seams: Optional[list[dict]] = None       # each: {"atF": int, "fadeMs": int}, clip-relative
    # Optional visual placement in normalized project space.  Omitting this preserves the legacy
    # full-canvas presentation; the browser resolves that default for the media type/crop mode.
    transform: Optional[dict] = None          # {x: 0..1, y: 0..1, scale: .1..4, fit: fill|contain|cover}


@dataclass
class TextItem:
    id: str
    text: str
    preset: str
    pos: dict                                # {"x": float, "y": float} in 0..1
    tlStartF: int
    durF: int
    anchor: Optional[Anchor] = None
    # Optional normalized text scale; omitted legacy documents resolve to 1.0.
    scale: float = 1.0


@dataclass
class GraphicItem:
    """A data-only first-party graphic selected from the trusted registry."""

    id: str
    componentId: str
    componentVersion: int
    props: dict
    pos: dict
    scale: float
    tlStartF: int
    durF: int
    anchor: Optional[Anchor] = None
    type: str = "graphic"


@dataclass
class Track:
    id: str
    kind: str                                # "speech" | "video" | "text" | "audio"
    clips: list[Clip] = field(default_factory=list)
    items: list[TextItem | GraphicItem] = field(default_factory=list)


@dataclass
class Scene:
    """A topical group of transcript words. List order is story order; the words a scene covers are
    the flat wordIdx range [startWord, the next-larger startWord among all scenes)."""

    id: str
    title: str
    role: str                                # one of SCENE_ROLES
    startWord: int
    shot: Optional[dict] = None              # {mode?, character?, pose?, expression?, move?, backdrop?, diagram?, headline?, eyebrow?: str, hideHeadline?: bool, cueWord?: wordIdx, costar?, interaction?, bubble?, ensemble?}


@dataclass
class Project:
    name: str
    title: Optional[str] = None              # human title; None ⇒ the UI falls back to name/slug
    fps: int = config.DEFAULT_FPS
    width: int = 1920
    height: int = 1080
    revision: int = 1
    transcript: Optional[str] = None
    channel: Optional[str] = None            # channels.json profile key; None ⇒ long-form
    crop: Optional[dict] = None              # {mode:"center"|"reframe", metaPath?}; None ⇒ legacy letterbox
    captions: Optional[dict] = None          # {enabled:bool, style?:dict, emphasis?:[wordIdx]}; None ⇒ no burn
    workspaceId: Optional[str] = None        # pipeline.library workspace id; None ⇒ the virtual "unsorted" group
    narration: Optional[str] = None          # one of NARRATION_STATES; None ⇒ not a script-origin video
    thumbnail: Optional[dict] = None         # the video's thumbnail: a still of THUMBNAIL_FIELDS; None ⇒ its defaults
    tracks: list[Track] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    schemaVersion: int = CURRENT_SCHEMA_VERSION


# ── Migrations (OpenCut pattern) ─────────────────────────────────────────────────

def migrate_v0_to_v1(d: dict) -> dict:
    """v0 = no fps/width/height/revision and `media` as a bare string.

    Injects the timebase fields (fps/width/height/revision) and wraps each bare `media` string
    into the {source, proxy} object while preserving existing document content.
    """
    d = dict(d)
    d.setdefault("fps", config.DEFAULT_FPS)
    d.setdefault("width", 1920)
    d.setdefault("height", 1080)
    d.setdefault("revision", 1)
    tracks = []
    for raw in d.get("tracks", []):
        t = dict(raw)
        if t.get("kind") != "text":
            clips = []
            for rawc in t.get("clips", []):
                c = dict(rawc)
                if isinstance(c.get("media"), str):
                    c["media"] = {"source": c["media"], "proxy": None}
                clips.append(c)
            t["clips"] = clips
        tracks.append(t)
    d["tracks"] = tracks
    d["schemaVersion"] = 1
    return d


def migrate_v1_to_v2(d: dict) -> dict:
    """Migrate v1 documents to the v2 project contract.

    v2 adds the versioned, discriminated first-party ``GraphicItem`` contract while retaining
    legacy text items and existing clip transforms. No fields are materialized here: omitted
    graphics remain absent, and pre-v2 documents retain their exact legacy presentation.
    """
    d = dict(d)
    d["schemaVersion"] = 2
    return d


def migrate_v2_to_v3(d: dict) -> dict:
    """v3 adds ordered topical ``scenes`` over the transcript's flat wordIdx space.

    A document with a speech clip (the only route to its words.json) gets one "Full video" scene from
    word 0 so every transcript word keeps a scene; one without speech has no words to group and gets
    none. The document alone cannot tell whether a transcript exists yet, so speech is the proxy.
    """
    d = dict(d)
    has_speech = any(t.get("kind") == "speech" and t.get("clips") for t in d.get("tracks", []))
    d.setdefault("scenes", [{"id": "scene-1", "title": "Full video", "role": "Beat", "startWord": 0}]
                 if has_speech else [])
    d["schemaVersion"] = 3
    return d


def migrate_v3_to_v4(d: dict) -> dict:
    """v4 normalises badge tones outside BADGE_TONES to ink, matching the renderer's fallback."""
    d = deepcopy(d)

    def migrate_badge(badge: object) -> None:
        if isinstance(badge, dict) and badge.get("tone") not in BADGE_TONES:
            badge["tone"] = "ink"

    thumbnail = d.get("thumbnail")
    holders = [scene.get("shot") for scene in d.get("scenes", [])]
    holders.append(thumbnail)
    for holder in holders:
        if isinstance(holder, dict) and isinstance(holder.get("badges"), list):
            for badge in holder["badges"]:
                migrate_badge(badge)
    if isinstance(thumbnail, dict) and isinstance(thumbnail.get("ensemble"), list):
        for member in thumbnail["ensemble"]:
            if isinstance(member, dict):
                migrate_badge(member.get("badge"))
    d["schemaVersion"] = 4
    return d


def migrate_v4_to_v5(d: dict) -> dict:
    """v5 drops unsupported diagram overrides so the renderer uses its role default."""
    d = deepcopy(d)
    for scene in d.get("scenes", []):
        shot = scene.get("shot")
        if isinstance(shot, dict) and "diagram" in shot and shot["diagram"] not in SHOT_DIAGRAMS:
            del shot["diagram"]
    d["schemaVersion"] = 5
    return d


_MIGRATIONS = {0: migrate_v0_to_v1, 1: migrate_v1_to_v2, 2: migrate_v2_to_v3, 3: migrate_v3_to_v4, 4: migrate_v4_to_v5}


# ── dict -> model ────────────────────────────────────────────────────────────────

def _media_from_dict(m: dict) -> Media:
    return Media(source=m["source"], proxy=m.get("proxy"))


def _anchor_from_dict(a: Optional[dict]) -> Optional[Anchor]:
    if a is None:
        return None
    return Anchor(mode=a["mode"], ref=a.get("ref"), offsetF=a.get("offsetF", 0))


def _clip_from_dict(c: dict) -> Clip:
    return Clip(
        id=c["id"],
        media=_media_from_dict(c["media"]),
        srcInF=c["srcInF"],
        srcOutF=c["srcOutF"],
        tlStartF=c["tlStartF"],
        gainDb=c.get("gainDb", 0.0),
        anchor=_anchor_from_dict(c.get("anchor")),
        duckDb=c.get("duckDb"),
        seams=c.get("seams"),
        transform=c.get("transform"),
    )


def _text_from_dict(i: dict) -> TextItem:
    allowed = {"id", "text", "preset", "pos", "scale", "tlStartF", "durF", "anchor"}
    if not (set(i) <= allowed):
        raise ValidationError(f"text item contains unknown fields: {sorted(set(i) - allowed)}")
    return TextItem(
        id=i["id"],
        text=i["text"],
        preset=i["preset"],
        pos=i["pos"],
        tlStartF=i["tlStartF"],
        durF=i["durF"],
        anchor=_anchor_from_dict(i.get("anchor")),
        scale=i.get("scale", 1.0),
    )


def _graphic_from_dict(i: dict) -> GraphicItem:
    from pipeline.graphics import validate_props

    allowed = {"type", "id", "componentId", "componentVersion", "props", "pos", "scale", "tlStartF", "durF", "anchor"}
    if not (set(i) <= allowed):
        raise ValidationError(f"graphic contains unknown fields: {sorted(set(i) - allowed)}")
    if not (i.get("type") == "graphic"):
        raise ValidationError("graphic item type must be 'graphic'")
    if not (isinstance(i.get("componentVersion"), int) and not isinstance(i["componentVersion"], bool)):
        raise ValidationError()
    if not (i["componentVersion"] == 1):
        raise ValidationError("unsupported graphic component version")
    props = validate_props(i["componentId"], i["props"])
    return GraphicItem(
        id=i["id"], componentId=i["componentId"], componentVersion=i["componentVersion"], props=props,
        pos=i["pos"], scale=i["scale"], tlStartF=i["tlStartF"], durF=i["durF"],
        anchor=_anchor_from_dict(i.get("anchor")),
    )


def _track_from_dict(t: dict) -> Track:
    kind = t["kind"]
    if kind == "text":
        items = []
        for i in t.get("items", []):
            item_type = i.get("type")
            if item_type is None:
                items.append(_text_from_dict(i))
            elif item_type == "graphic":
                items.append(_graphic_from_dict(i))
            else:
                raise ValidationError(f"unknown text item type: {item_type!r}")
        return Track(id=t["id"], kind=kind, items=items)
    return Track(id=t["id"], kind=kind, clips=[_clip_from_dict(c) for c in t.get("clips", [])])


def _scene_from_dict(s: dict) -> Scene:
    allowed = {"id", "title", "role", "startWord", "shot"}
    if not (set(s) <= allowed):
        raise ValidationError(f"scene contains unknown fields: {sorted(set(s) - allowed)}")
    return Scene(id=s["id"], title=s["title"], role=s["role"], startWord=s["startWord"], shot=s.get("shot"))


def from_dict(d: dict) -> Project:
    """Run the migration chain to CURRENT_SCHEMA_VERSION, then build the dataclasses."""
    d = dict(d)
    version = d.get("schemaVersion", 0)
    while version < CURRENT_SCHEMA_VERSION:
        d = _MIGRATIONS[version](d)
        version = d.get("schemaVersion", version + 1)
    return Project(
        name=d["name"],
        title=d.get("title"),
        fps=d.get("fps", config.DEFAULT_FPS),
        width=d.get("width", 1920),
        height=d.get("height", 1080),
        revision=d.get("revision", 1),
        transcript=d.get("transcript"),
        channel=d.get("channel"),
        crop=d.get("crop"),
        captions=d.get("captions"),
        workspaceId=d.get("workspaceId"),
        narration=d.get("narration"),
        thumbnail=d.get("thumbnail"),
        tracks=[_track_from_dict(t) for t in d.get("tracks", [])],
        scenes=[_scene_from_dict(s) for s in d.get("scenes", [])],
        schemaVersion=d.get("schemaVersion", CURRENT_SCHEMA_VERSION),
    )


# ── model -> dict ────────────────────────────────────────────────────────────────

def _media_to_dict(m: Media) -> dict:
    return {"source": m.source, "proxy": m.proxy}


def _anchor_to_dict(a: Anchor) -> dict:
    d: dict = {"mode": a.mode}
    if a.ref is not None:
        d["ref"] = a.ref
    d["offsetF"] = a.offsetF
    return d


def _clip_to_dict(c: Clip) -> dict:
    d: dict = {
        "id": c.id,
        "media": _media_to_dict(c.media),
        "srcInF": c.srcInF,
        "srcOutF": c.srcOutF,
        "tlStartF": c.tlStartF,
        "gainDb": c.gainDb,
    }
    if c.anchor is not None:
        d["anchor"] = _anchor_to_dict(c.anchor)
    if c.duckDb is not None:
        d["duckDb"] = c.duckDb
    if c.seams is not None:
        d["seams"] = c.seams
    if c.transform is not None:
        d["transform"] = c.transform
    return d


def _text_to_dict(i: TextItem) -> dict:
    d: dict = {
        "id": i.id,
        "text": i.text,
        "preset": i.preset,
        "pos": i.pos,
        "tlStartF": i.tlStartF,
        "durF": i.durF,
    }
    if i.scale != 1.0:
        d["scale"] = i.scale
    if i.anchor is not None:
        d["anchor"] = _anchor_to_dict(i.anchor)
    return d


def _graphic_to_dict(i: GraphicItem) -> dict:
    d: dict = {
        "type": "graphic", "id": i.id, "componentId": i.componentId,
        "componentVersion": i.componentVersion, "props": dict(i.props), "pos": dict(i.pos),
        "scale": i.scale, "tlStartF": i.tlStartF, "durF": i.durF,
    }
    if i.anchor is not None:
        d["anchor"] = _anchor_to_dict(i.anchor)
    return d


def _track_to_dict(t: Track) -> dict:
    if t.kind == "text":
        return {"id": t.id, "kind": t.kind, "items": [
            _graphic_to_dict(i) if isinstance(i, GraphicItem) else _text_to_dict(i) for i in t.items
        ]}
    return {"id": t.id, "kind": t.kind, "clips": [_clip_to_dict(c) for c in t.clips]}


def _scene_to_dict(s: Scene) -> dict:
    d: dict = {"id": s.id, "title": s.title, "role": s.role, "startWord": s.startWord}
    if s.shot is not None:
        d["shot"] = s.shot
    return d


def to_dict(p: Project) -> dict:
    """Deterministic serialization; text tracks emit `items`, all others emit `clips`. Additive
    fields (`channel`/`crop`/`captions`/`scenes`) are emitted only when set, so a legacy long-form
    doc round-trips byte-identically."""
    d: dict = {
        "schemaVersion": p.schemaVersion,
        "name": p.name,
        "fps": p.fps,
        "width": p.width,
        "height": p.height,
        "revision": p.revision,
        "transcript": p.transcript,
        "tracks": [_track_to_dict(t) for t in p.tracks],
    }
    if p.title is not None:
        d["title"] = p.title
    if p.channel is not None:
        d["channel"] = p.channel
    if p.crop is not None:
        d["crop"] = p.crop
    if p.captions is not None:
        d["captions"] = p.captions
    if p.workspaceId is not None:
        d["workspaceId"] = p.workspaceId
    if p.narration is not None:
        d["narration"] = p.narration
    if p.thumbnail is not None:
        d["thumbnail"] = p.thumbnail
    if p.scenes:
        d["scenes"] = [_scene_to_dict(s) for s in p.scenes]
    return d


# ── Validation (hand-written asserts) ────────────────────────

def _check_rel_path(path: str, label: str) -> None:
    if not path:
        raise ValidationError(f"{label} must be non-empty")
    if path.startswith("/"):
        raise ValidationError(f"{label} must be project-relative")
    if "\\" in path:
        raise ValidationError(f"{label} must use POSIX separators")
    # A colon means a Windows drive ('C:/x', 'C:x') or a URL scheme — both let Path() discard
    # project_dir at join time and escape the sandbox. Project-relative POSIX refs never contain one.
    if ":" in path:
        raise ValidationError(f"{label} must not contain a drive or scheme")
    if ".." in path.split("/"):
        raise ValidationError(f"{label} must not contain a parent directory segment")


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValidationError(f"{label} must match ^[A-Za-z0-9_-]{{1,64}}$: {value!r}")


def _validate_db(value: object, field_name: str, cid: str) -> None:
    # bool is an int subclass — exclude it so `True` can't sneak past as a 1 dB gain.
    if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
        raise ValidationError(f"{cid}: {field_name} must be a number, not {type(value).__name__}")
    if not (_DB_MIN <= value <= _DB_MAX):
        raise ValidationError(f"{cid}: {field_name} must be in [{_DB_MIN}, {_DB_MAX}]: {value}")


def _validate_media(media: Media, cid: str) -> None:
    if not (isinstance(media.source, str) and media.source):
        raise ValidationError(f"{cid}: media.source must be non-empty")
    _check_media_path(media.source, f"{cid}: media.source")
    if media.proxy is not None:
        if not (isinstance(media.proxy, str) and media.proxy):
            raise ValidationError(f"{cid}: media.proxy must be non-empty or null")
        _check_media_path(media.proxy, f"{cid}: media.proxy")


def _check_media_path(path: str, label: str) -> None:
    _check_rel_path(path, label)
    if re.search(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", path) or any(not part.strip(". ") for part in path.split("/")):
        raise ValidationError(f"{label} must not contain control characters or dot/space-only segments")
    parts = PurePosixPath(path).parts
    extension = PurePosixPath(path).suffix.lower()
    media_root = (
        len(parts) >= 2 and parts[0] in {"data", "media", "proxies"}
        or len(parts) >= 3 and parts[:2] in {
            ("outputs", "proxies"), ("outputs", "scenes"), ("outputs", "final"), ("outputs", "preview")}
        or len(parts) >= 4 and parts[0] == "workspaces" and parts[2] == "library"
        or len(parts) >= 3 and parts[:2] == ("outputs", "thumbnails") and extension in {".png", ".jpg", ".jpeg"}
    )
    extensions = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".png", ".jpg", ".jpeg",
                  ".webp", ".gif", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    if not media_root or extension not in extensions:
        raise ValidationError(f"{label} must name a media file under a media root")


def _validate_anchor(anchor: Optional[Anchor], cid: str) -> None:
    if anchor is None:
        return
    if anchor.mode not in _ANCHOR_MODES:
        raise ValidationError(f"{cid}: anchor.mode must be one of {_ANCHOR_MODES}")
    if not isinstance(anchor.offsetF, int):
        raise ValidationError(f"{cid}: anchor.offsetF must be an int")
    if anchor.mode == "word":
        if not isinstance(anchor.ref, dict):
            raise ValidationError(f"{cid}: word anchor requires a ref")
        if not isinstance(anchor.ref.get("media"), str):
            raise ValidationError(f"{cid}: anchor.ref.media must be a str")
        if not isinstance(anchor.ref.get("wordIdx"), int):
            raise ValidationError(f"{cid}: anchor.ref.wordIdx must be an int")


def _validate_frames(src_in: int, src_out: int, tl_start: int, cid: str) -> None:
    for name, v in (("srcInF", src_in), ("srcOutF", src_out), ("tlStartF", tl_start)):
        if not (isinstance(v, int) and v >= 0):
            raise ValidationError(f"{cid}: {name} must be an int >= 0")
    if not (src_out > src_in):
        raise ValidationError(f"{cid}: srcOutF must be > srcInF")


def _validate_seams(clip: Clip) -> None:
    if clip.seams is None:
        return
    if not isinstance(clip.seams, list):
        raise ValidationError(f"{clip.id}: seams must be a list")
    dur = clip.srcOutF - clip.srcInF
    for seam in clip.seams:
        if not isinstance(seam, dict):
            raise ValidationError(f"{clip.id}: each seam must be an object")
        at, fade = seam.get("atF"), seam.get("fadeMs")
        if not (isinstance(at, int) and 0 <= at <= dur):
            raise ValidationError(f"{clip.id}: seam.atF must be an int in [0, {dur}]")
        if not (isinstance(fade, int) and fade > 0):
            raise ValidationError(f"{clip.id}: seam.fadeMs must be an int > 0")


def _validate_transform(transform: Optional[dict], cid: str) -> None:
    """Validate optional normalized placement for a visual clip.

    Coordinates are resolution-independent project-space fractions.  Keeping the descriptor
    optional is intentional: old documents retain their established full-frame behaviour and a
    reset can simply remove the override instead of writing a brittle snapshot of defaults.
    """
    if transform is None:
        return
    if not isinstance(transform, dict):
        raise ValidationError(f"{cid}: transform must be an object or null")
    if not (set(transform) == {"x", "y", "scale", "fit"}):
        raise ValidationError(f"{cid}: transform must contain only x, y, scale, fit")
    for key in ("x", "y", "scale"):
        value = transform.get(key)
        if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
            raise ValidationError(f"{cid}: transform.{key} must be a number")
    if not (0 <= transform["x"] <= 1):
        raise ValidationError(f"{cid}: transform.x must be in [0,1]")
    if not (0 <= transform["y"] <= 1):
        raise ValidationError(f"{cid}: transform.y must be in [0,1]")
    if not (_TRANSFORM_SCALE_MIN <= transform["scale"] <= _TRANSFORM_SCALE_MAX):
        raise ValidationError(f"{cid}: transform.scale must be in [{_TRANSFORM_SCALE_MIN}, {_TRANSFORM_SCALE_MAX}]")
    if transform.get("fit") not in _TRANSFORM_FITS:
        raise ValidationError(f"{cid}: transform.fit must be one of {_TRANSFORM_FITS}")


def _validate_clip_track(track: Track, all_ids: set) -> None:
    for clip in track.clips:
        _validate_id(clip.id, "clip id")
        if clip.id in all_ids:
            raise ValidationError(f"duplicate id: {clip.id}")
        all_ids.add(clip.id)
        _validate_frames(clip.srcInF, clip.srcOutF, clip.tlStartF, clip.id)
        _validate_media(clip.media, clip.id)
        _validate_db(clip.gainDb, "gainDb", clip.id)
        if clip.duckDb is not None:
            _validate_db(clip.duckDb, "duckDb", clip.id)
        if track.kind == "speech":
            if clip.anchor is not None:
                raise ValidationError(f"speech clip {clip.id} must not carry an anchor")
        if track.kind == "audio":
            if clip.transform is not None:
                raise ValidationError(f"audio clip {clip.id} must not carry a visual transform")
        _validate_anchor(clip.anchor, clip.id)
        _validate_seams(clip)
        _validate_transform(clip.transform, clip.id)
    ordered = sorted(track.clips, key=lambda c: c.tlStartF)
    for prev, cur in zip(ordered, ordered[1:]):
        prev_end = prev.tlStartF + (prev.srcOutF - prev.srcInF)
        if not (cur.tlStartF >= prev_end):
            raise ValidationError(f"overlap in track {track.id}: {cur.id} starts before {prev.id} ends")


def _validate_graphic(item: GraphicItem) -> None:
    from pipeline.graphics import validate_props

    _validate_id(item.id, "graphic id")
    if not (item.type == "graphic"):
        raise ValidationError(f"{item.id}: graphic type must be 'graphic'")
    if not (item.componentVersion == 1):
        raise ValidationError(f"{item.id}: unsupported graphic component version")
    if not (isinstance(item.pos, dict) and set(item.pos) == {"x", "y"}):
        raise ValidationError(f"{item.id}: invalid graphic position")
    for key in ("x", "y"):
        value = item.pos[key]
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1):
            raise ValidationError(f"{item.id}: graphic pos.{key} must be in [0,1]")
    if not (isinstance(item.scale, (int, float)) and not isinstance(item.scale, bool) and 0.1 <= item.scale <= 4):
        raise ValidationError(f"{item.id}: graphic scale must be in [0.1,4]")
    if not (isinstance(item.tlStartF, int) and item.tlStartF >= 0):
        raise ValidationError(f"{item.id}: tlStartF must be int >= 0")
    if not (isinstance(item.durF, int) and item.durF > 0):
        raise ValidationError(f"{item.id}: durF must be > 0")
    validate_props(item.componentId, item.props)
    _validate_anchor(item.anchor, item.id)


def _validate_text_track(track: Track, all_ids: set) -> None:
    for item in track.items:
        _validate_id(item.id, "text/graphic id")
        if item.id in all_ids:
            raise ValidationError(f"duplicate id: {item.id}")
        if isinstance(item, GraphicItem):
            _validate_graphic(item)
            all_ids.add(item.id)
            continue
        all_ids.add(item.id)
        if not (isinstance(item.tlStartF, int) and item.tlStartF >= 0):
            raise ValidationError(f"{item.id}: tlStartF must be int >= 0")
        if not (isinstance(item.durF, int) and item.durF > 0):
            raise ValidationError(f"{item.id}: durF must be > 0")
        if not isinstance(item.pos, dict):
            raise ValidationError(f"{item.id}: pos must be a dict")
        x, y = item.pos.get("x"), item.pos.get("y")
        if not (isinstance(x, (int, float)) and 0 <= x <= 1):
            raise ValidationError(f"{item.id}: pos.x must be in [0,1]")
        if not (isinstance(y, (int, float)) and 0 <= y <= 1):
            raise ValidationError(f"{item.id}: pos.y must be in [0,1]")
        if not (isinstance(item.scale, (int, float)) and not isinstance(item.scale, bool) and 0.1 <= item.scale <= 4):
            raise ValidationError(f"{item.id}: scale must be in [0.1,4]")
        _validate_anchor(item.anchor, item.id)
    ordered = sorted(track.items, key=lambda i: i.tlStartF)
    for prev, cur in zip(ordered, ordered[1:]):
        if not (cur.tlStartF >= prev.tlStartF + prev.durF):
            raise ValidationError(f"overlap in track {track.id}: {cur.id} starts before {prev.id} ends")


_CROP_MODES = {"center", "reframe"}


def _validate_crop(crop: Optional[dict]) -> None:
    """The 9:16 crop descriptor. None ⇒ legacy letterbox (regression hinge). When set it is a
    {mode, metaPath?} dict; metaPath (a reframe.json ref) must be a project-relative POSIX path."""
    if crop is None:
        return
    if not isinstance(crop, dict):
        raise ValidationError("crop must be an object or null")
    if crop.get("mode") not in _CROP_MODES:
        raise ValidationError(f"crop.mode must be one of {_CROP_MODES}")
    meta_path = crop.get("metaPath")
    if meta_path is not None:
        if not isinstance(meta_path, str):
            raise ValidationError("crop.metaPath must be a string")
        _check_rel_path(meta_path, "crop.metaPath")


def _validate_captions(captions: Optional[dict]) -> None:
    """The caption-burn descriptor. None/absent ⇒ no ASS filter (legacy long-form unchanged).
    When set, `enabled` is a bool and `emphasis` (if present) is a list of non-negative flat wordIdx."""
    if captions is None:
        return
    if not isinstance(captions, dict):
        raise ValidationError("captions must be an object or null")
    enabled = captions.get("enabled")
    if not isinstance(enabled, bool):
        raise ValidationError("captions.enabled must be a bool")
    style = captions.get("style")
    if style is not None:
        if not isinstance(style, dict):
            raise ValidationError("captions.style must be an object")
        from pipeline.channels import validate_caption_style

        validate_caption_style(style)
    emphasis = captions.get("emphasis")
    if emphasis is not None:
        if not isinstance(emphasis, list):
            raise ValidationError("captions.emphasis must be a list")
        for i in emphasis:
            if not (isinstance(i, int) and not isinstance(i, bool) and i >= 0):
                raise ValidationError("captions.emphasis entries must be non-negative ints")


# Short-film staging (motion-core/STAGING.md): a camera move, how an animation's frame is composed
# (`stage` is the character shot), and each layout's own fields: a `screen` shot's library images (phone 1,
# plus up to SHOT_SCREEN_MORE_MAX more), a `title` card's badges and a `signoff`'s optional pixel text.
SHOT_DIAGRAMS = ("scatter", "blank", "brief", "route", "checks", "glow", "none", "grid", "chain", "radial", "tree", "list")
SHOT_CAMERAS = ("push-in", "pull-out", "pan")
SHOT_LAYOUTS = ("stage", "screen", "title", "signoff")
BADGE_TONES = ("ink", "paper", "accent")
SHOT_BADGES_MAX = 4
SHOT_BADGE_TEXT_MAX = 24
SHOT_MARK_PATTERN = r"^[A-Za-z0-9&]{1,4}$"
SHOT_SCREEN_MORE_MAX = 2
_STAGING_FIELDS = {"camera", "layout", "screen", "badges", "mark", "headlineAt", "boardLabels"}
_ASSET_ID_RE = re.compile(r"^[a-f0-9]{16}$")
# Short-film staging: a stage shot's headline can go over its co-star instead of its own place; `badges`
# on a stage shot with an ensemble drop under its members, and `camera: pan` carries the stage in from the left.
HEADLINE_SPOTS = ("costar",)
# Content-sized board labels in node order; blanks draw empty boxes.
SHOT_BOARD_LABELS_MAX = 6
SHOT_BOARD_LABEL_MAX = 12


def _asset_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ASSET_ID_RE.fullmatch(value))


def _validate_staging(shot: dict, sid: str) -> None:
    if "camera" in shot:
        if shot["camera"] not in SHOT_CAMERAS:
            raise ValidationError(f"{sid}: shot.camera must be one of {SHOT_CAMERAS}")
    if "layout" in shot:
        if shot["layout"] not in SHOT_LAYOUTS:
            raise ValidationError(f"{sid}: shot.layout must be one of {SHOT_LAYOUTS}")
    if "screen" in shot:
        screen = shot["screen"]
        if not (isinstance(screen, dict) and "asset" in screen and set(screen) <= {"asset", "more"} \
            and _asset_id(screen["asset"])):
            raise ValidationError(f"{sid}: shot.screen must be {{asset, more?}}: library asset ids")
        if "more" in screen:
            more = screen["more"]
            if not (isinstance(more, list) and 1 <= len(more) <= SHOT_SCREEN_MORE_MAX and all(map(_asset_id, more))):
                raise ValidationError(f"{sid}: shot.screen.more must list 1 to {SHOT_SCREEN_MORE_MAX} library asset ids")
    if "badges" in shot:
        badges = shot["badges"]
        if not (isinstance(badges, list) and 1 <= len(badges) <= SHOT_BADGES_MAX):
            raise ValidationError(f"{sid}: shot.badges must list 1 to {SHOT_BADGES_MAX} badges")
        for b in badges:
            if not (isinstance(b, dict) and set(b) == {"text", "tone"}):
                raise ValidationError(f"{sid}: a badge is {{text, tone}}")
            if not (isinstance(b["text"], str) and b["text"].strip() and len(b["text"]) <= SHOT_BADGE_TEXT_MAX):
                raise ValidationError(f"{sid}: a badge's text must be 1 to {SHOT_BADGE_TEXT_MAX} characters")
            if b["tone"] not in BADGE_TONES:
                raise ValidationError(f"{sid}: a badge's tone must be one of {BADGE_TONES}")
    if "mark" in shot:
        if not (isinstance(shot["mark"], str) and re.fullmatch(SHOT_MARK_PATTERN, shot["mark"])):
            raise ValidationError(f"{sid}: shot.mark must be 1 to 4 letters, digits or &")
    if "headlineAt" in shot:
        if shot["headlineAt"] not in HEADLINE_SPOTS:
            raise ValidationError(f"{sid}: shot.headlineAt must be one of {HEADLINE_SPOTS}")
    if "boardLabels" in shot:
        labels = shot["boardLabels"]
        if not (isinstance(labels, list) and 1 <= len(labels) <= SHOT_BOARD_LABELS_MAX \
            and all(isinstance(label, str) and len(label) <= SHOT_BOARD_LABEL_MAX for label in labels) \
            and any(label.strip() for label in labels)):
            raise ValidationError(f"{sid}: shot.boardLabels must list 1 to {SHOT_BOARD_LABELS_MAX} labels of at most {SHOT_BOARD_LABEL_MAX} characters, not all blank")


def _validate_shot(shot: Optional[dict], sid: str) -> None:
    if shot is None:
        return
    if not isinstance(shot, dict):
        raise ValidationError(f"{sid}: shot must be an object or null")
    unknown = set(shot) - _SHOT_TEXT_FIELDS - _SHOT_BOOL_FIELDS - _ACTING_FIELDS \
        - {"cueWord", "mode", "costar", "interaction"} - _STAGING_FIELDS
    if unknown:
        raise ValidationError(f"{sid}: shot contains unknown fields: {sorted(unknown)}")
    for key in _SHOT_TEXT_FIELDS & set(shot):
        if not isinstance(shot[key], str):
            raise ValidationError(f"{sid}: shot.{key} must be a string")
    for key in _SHOT_BOOL_FIELDS & set(shot):
        if not isinstance(shot[key], bool):
            raise ValidationError(f"{sid}: shot.{key} must be a boolean")
    if not (len(shot.get("eyebrow", "")) <= SHOT_EYEBROW_MAX):
        raise ValidationError(f"{sid}: shot.eyebrow is longer than {SHOT_EYEBROW_MAX} characters")
    if "mode" in shot:
        if shot["mode"] not in SHOT_MODES:
            raise ValidationError(f"{sid}: shot.mode must be one of {SHOT_MODES}")
    if "costar" in shot:
        costar = shot["costar"]
        if not isinstance(costar, dict):
            raise ValidationError(f"{sid}: shot.costar must be an object")
        unknown = set(costar) - _COSTAR_FIELDS
        if unknown:
            raise ValidationError(f"{sid}: shot.costar contains unknown fields: {sorted(unknown)}")
        if not (isinstance(costar.get("character"), str) and costar["character"]):
            raise ValidationError(f"{sid}: shot.costar.character must name a character")
        for key in _COSTAR_FIELDS & set(costar):
            if not isinstance(costar[key], str):
                raise ValidationError(f"{sid}: shot.costar.{key} must be a string")
    if "interaction" in shot:
        if shot["interaction"] not in INTERACTIONS:
            raise ValidationError(f"{sid}: shot.interaction must be one of {INTERACTIONS}")
    if "cueWord" in shot:
        cue = shot["cueWord"]
        if not (isinstance(cue, int) and not isinstance(cue, bool) and cue >= 0):
            raise ValidationError(f"{sid}: shot.cueWord must be a non-negative wordIdx")
    _validate_acting(shot, sid)
    _validate_staging(shot, sid)


def _validate_acting(shot: dict, sid: str) -> None:
    """A shot's bubbles (the lead's, the co-star's, each member's) and its ensemble: at most
    ENSEMBLE_MAX performers, each a cast profile id plus its own look, like the co-star."""
    looks = [("shot", shot), *([("shot.costar", shot["costar"])] if isinstance(shot.get("costar"), dict) else [])]
    if "ensemble" in shot:
        ensemble = shot["ensemble"]
        if not (isinstance(ensemble, list) and len(ensemble) <= ENSEMBLE_MAX):
            raise ValidationError(f"{sid}: shot.ensemble must be a list of at most {ENSEMBLE_MAX} performers")
        for i, member in enumerate(ensemble):
            where = f"shot.ensemble[{i}]"
            if not isinstance(member, dict):
                raise ValidationError(f"{sid}: {where} must be an object")
            unknown = set(member) - _COSTAR_FIELDS
            if unknown:
                raise ValidationError(f"{sid}: {where} contains unknown fields: {sorted(unknown)}")
            if not (isinstance(member.get("character"), str) and member["character"]):
                raise ValidationError(f"{sid}: {where}.character must name a character")
            for key in _COSTAR_FIELDS & set(member):
                if not isinstance(member[key], str):
                    raise ValidationError(f"{sid}: {where}.{key} must be a string")
            looks.append((where, member))
    for where, look in looks:
        if "bubble" in look:
            if look["bubble"] not in BUBBLES:
                raise ValidationError(f"{sid}: {where}.bubble must be one of {BUBBLES}")


# A video's thumbnail (motion-core/POSTER.md): a still, stored as a shot restricted to what a poster
# draws: the title (`headline`), an eyebrow pill and badges, the lead's character and look, an ensemble of
# up to ENSEMBLE_MAX behind it, and the backdrop. Absent fields take the video's defaults (pipeline.thumbnail).
THUMBNAIL_FIELDS = ("headline", "eyebrow", "badges", "character", "pose", "expression", "ensemble", "backdrop")
THUMBNAIL_MEMBER_FIELDS = ("character", "pose", "expression", "badge")
THUMBNAIL_HEADLINE_MAX = 60


def _validate_thumbnail(thumbnail: Optional[dict]) -> None:
    """The shot's own rules (`_validate_shot`) over the fields a thumbnail may hold."""
    if thumbnail is None:
        return
    if not isinstance(thumbnail, dict):
        raise ValidationError("thumbnail must be an object or null")
    unknown = set(thumbnail) - set(THUMBNAIL_FIELDS)
    if unknown:
        raise ValidationError(f"thumbnail contains unknown fields: {sorted(unknown)}")
    for i, member in enumerate(thumbnail.get("ensemble") or []):
        if isinstance(member, dict):
            unknown = set(member) - set(THUMBNAIL_MEMBER_FIELDS)
            if unknown:
                raise ValidationError(f"thumbnail.ensemble[{i}] contains unknown fields: {sorted(unknown)}")
            if "badge" in member:
                label = member["badge"]
                # A member's label over its head, held to a title card badge's rules.
                if not (isinstance(label, dict) and set(label) == {"text", "tone"}):
                    raise ValidationError(f"thumbnail.ensemble[{i}].badge must be {{text, tone}}")
                if not (isinstance(label["text"], str) and label["text"].strip() and len(label["text"]) <= SHOT_BADGE_TEXT_MAX):
                    raise ValidationError(f"thumbnail.ensemble[{i}].badge text must be 1 to {SHOT_BADGE_TEXT_MAX} characters")
                if label["tone"] not in BADGE_TONES:
                    raise ValidationError(f"thumbnail.ensemble[{i}].badge tone must be one of {BADGE_TONES}")
    ensemble = thumbnail.get("ensemble")
    if isinstance(ensemble, list):
        thumbnail = {**thumbnail, "ensemble": [{k: v for k, v in m.items() if k != "badge"} if isinstance(m, dict) else m for m in ensemble]}
    _validate_shot(thumbnail, "thumbnail")
    if not (len(thumbnail.get("headline", "")) <= THUMBNAIL_HEADLINE_MAX):
        raise ValidationError(f"thumbnail.headline is longer than {THUMBNAIL_HEADLINE_MAX} characters")


def _validate_scenes(scenes: list[Scene]) -> None:
    """Scene ids live in their own namespace (unique among scenes, same slug charset as clips). The
    transcript length is unknown here, so coverage is enforced structurally: distinct startWords with
    the earliest at word 0 give every word exactly one scene."""
    ids: set = set()
    starts: set = set()
    for s in scenes:
        _validate_id(s.id, "scene id")
        if s.id in ids:
            raise ValidationError(f"duplicate scene id: {s.id}")
        ids.add(s.id)
        if not (isinstance(s.title, str) and s.title.strip() and len(s.title) <= SCENE_TITLE_MAX):
            raise ValidationError(f"{s.id}: title must be a non-empty string of at most {SCENE_TITLE_MAX} chars")
        if s.role not in SCENE_ROLES:
            raise ValidationError(f"{s.id}: role must be one of {SCENE_ROLES}")
        if not (isinstance(s.startWord, int) and not isinstance(s.startWord, bool) and s.startWord >= 0):
            raise ValidationError(f"{s.id}: startWord must be a non-negative wordIdx")
        if s.startWord in starts:
            raise ValidationError(f"{s.id}: another scene already starts at word {s.startWord}")
        starts.add(s.startWord)
        _validate_shot(s.shot, s.id)
    if not (not starts or min(starts) == 0):
        raise ValidationError("the earliest scene must start at word 0")


def validate(p: Project) -> None:
    if project_name(p.name) is None:
        raise ValidationError("invalid video name")
    if not (p.schemaVersion == CURRENT_SCHEMA_VERSION):
        raise ValidationError(f"schemaVersion must be {CURRENT_SCHEMA_VERSION}")
    if not (isinstance(p.fps, int) and p.fps == config.DEFAULT_FPS):
        raise ValidationError("fps must be 30 (fixed non-drop timebase)")
    if not (isinstance(p.width, int) and p.width > 0):
        raise ValidationError("width must be a positive int")
    if not (isinstance(p.height, int) and p.height > 0):
        raise ValidationError("height must be a positive int")
    if not (isinstance(p.revision, int) and p.revision >= 0):
        raise ValidationError("revision must be a non-negative int")
    if p.title is not None:
        if not (isinstance(p.title, str) and p.title == p.title.strip() and 0 < len(p.title) <= SCENE_TITLE_MAX):
            raise ValidationError(f"title must be a non-empty trimmed string of at most {SCENE_TITLE_MAX} chars, or null")
    # `channel` is a free string (the channels.json key) — project.py stays decoupled from channels.json.
    if p.channel is not None:
        if not (isinstance(p.channel, str) and p.channel):
            raise ValidationError("channel must be a non-empty string or null")
    if p.workspaceId is not None:
        if not (isinstance(p.workspaceId, str) and p.workspaceId):
            raise ValidationError("workspaceId must be a non-empty string or null")
    if p.narration is not None:
        if p.narration not in NARRATION_STATES:
            raise ValidationError(f"narration must be one of {NARRATION_STATES} or null")
    _validate_crop(p.crop)
    _validate_captions(p.captions)
    _validate_scenes(p.scenes)
    _validate_thumbnail(p.thumbnail)

    track_ids: set = set()
    all_ids: set = set()
    for track in p.tracks:
        _validate_id(track.id, "track id")
        if track.kind not in _ALLOWED_KINDS:
            raise ValidationError(f"invalid track kind: {track.kind}")
        if track.id in track_ids:
            raise ValidationError(f"duplicate track id: {track.id}")
        track_ids.add(track.id)
        if track.kind == "text":
            if track.clips:
                raise ValidationError(f"text track {track.id} must use items, not clips")
            _validate_text_track(track, all_ids)
        else:
            if track.items:
                raise ValidationError(f"non-text track {track.id} must use clips, not items")
            _validate_clip_track(track, all_ids)


# ── Op-log + persistence + media resolution ──────────────────────────────────────

def apply_ops(p: Project, ops: list[dict]) -> Project:
    """Apply an RFC-6902 patch to the project doc, rebuild the model, and re-validate.

    Used by the PATCH route and tested here. jsonpatch stays a lazy import so the
    module remains dependency-free at collection time.
    """
    import jsonpatch

    patched = jsonpatch.apply_patch(to_dict(p), ops)
    result = from_dict(patched)
    validate(result)
    return result


def write_project(path: Path, p: Project) -> None:
    """Atomic write: serialize to a temp sibling then os.replace."""
    path = Path(path)
    if not path.exists():
        check_case_collision(path)
    else:
        exact_path(path, path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(to_dict(p), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def resolve_media(project_dir: Path, media: Media, *, prefer_proxy: bool) -> Path:
    """Resolve a validated media ref against project_dir. Render passes prefer_proxy=False."""
    rel = media.proxy if (prefer_proxy and media.proxy) else media.source
    _check_media_path(rel, "media ref")
    project_dir = Path(project_dir)
    joined = project_dir / rel
    return exact_path(joined, project_dir)
