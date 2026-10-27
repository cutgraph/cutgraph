"""The cast: character profiles, stored as data per workspace.

A character profile is a character a video can cast: the motion-core rig that draws it, its approved
reference art (image asset ids in its workspace library), its palette (one of the rig's colour
variants), whether the user has approved its look, and its custom expressions (a named combination
of the rig's parts, never code):

    STATE_HOME/workspaces/<id>/cast.json   {"default": characterId | null, "characters": [
        {id, name, rig, art: [assetId], palette, approved, expressions: [{id, name, spec, createdAt, draft?}],
         createdAt}]}

A profile on a client's rig (a rig drafted from that client's art) starts as a draft, `approved:
false`, until the user approves its look in Library -> Cast; one on a brand rig is approved already.
A described expression likewise lands as a draft, `draft: true`, that no scene can wear until
the user approves it.

Brand profiles are visible to every video; a client workspace's profiles only to that workspace's
videos, so a client's character never appears in anyone else's work. The brand workspace always holds
the Lens. Pre-profile expressions and unknown top-level data are preserved without interpretation.
Profile ids are unique across
workspaces, so a scene's `shot.character` names one profile. A workspace may mark a default character;
a video's default is its workspace's, else the brand's, else the Lens.

`BUILTIN_RIGS` mirrors motion-core's built-in registry; `RIGS` also loads character pack manifests:
each rig's owner, palettes, spec space, built-in expressions, poses and moves. tests/test_cast.py parses
them so they cannot drift. `resolve_shot` is the host side of motion-core's character contract, shared
with the preview (ui/src/v3/library/cast.ts `castShot`); it also carries the video's brand, the
eyebrow over its animated headlines.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path

import config
from pipeline import library, packs

BUILTIN_EXPRESSIONS = (
    "neutral", "happy", "excited", "surprised", "curious", "focused", "sleepy", "idea", "confused",
    "determined", "proud",
)
POSES = ("rest", "point", "hold", "cheer", "shrug", "think", "wave", "hips", "flex")
MOVES = ("idle", "enter", "hop", "walk", "nod", "think", "wave", "celebrate", "sway")
BUILTIN_RIGS: dict[str, dict] = {
    "lens": {
        "owner": "brand",
        "palettes": ("periwinkle",),
        "parts": {
            "eyes": ("open", "arc", "wide", "tall", "side", "narrow", "closed", "uneven"),
            "mouth": ("smile", "grin", "round", "flat", "small", "crooked"),
        },
        "expressions": BUILTIN_EXPRESSIONS,
        "poses": POSES,
        "moves": MOVES,
    },
}


def rigs_from_packs(dir: Path) -> dict[str, dict]:
    rigs = dict(BUILTIN_RIGS)
    for rig in packs.load(dir, builtin_ids=BUILTIN_RIGS):
        rigs[rig["id"]] = {
            "owner": rig["owner"], "palettes": tuple(rig["palettes"]),
            "parts": {part: tuple(options) for part, options in rig["parts"].items()},
            **{key: tuple(rig[key]) for key in ("expressions", "poses", "moves")},
        }
    return rigs


RIGS = rigs_from_packs(config.PACKS_DIR)
DEFAULT_RIG = "lens"

BRAND = library.BUILTIN_WORKSPACE_ID
DEFAULT_CHARACTER_ID = "default-character"
DEFAULT_CHARACTER_NAME = "Lens"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ASSET_ID_RE = re.compile(r"^[a-f0-9]{16}$")
_MAX_NAME = 40
_MAX_DESCRIPTION = 200
_MAX_ART = 12
_lock = threading.Lock()


class CastError(ValueError):
    """A caller-facing 400: a malformed name, spec, rig, palette, art list or id."""


class CastConflict(CastError):
    """A caller-facing 409: the name is taken by a built-in or another entry."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _path(workspace_id: str) -> Path:
    return library._workspace_dir(workspace_id) / "cast.json"


def validate_spec(spec: object, rig: str = DEFAULT_RIG) -> dict:
    """The spec as one of the rig's options for each of its parts, or CastError."""
    if rig not in RIGS:
        raise CastError("character rig is unavailable")
    parts = RIGS[rig]["parts"]
    if not isinstance(spec, dict) or set(spec) != set(parts):
        raise CastError("spec must be {" + ", ".join(parts) + "}")
    for part, options in parts.items():
        if spec[part] not in options:
            raise CastError(f"invalid {part}: {spec[part]!r} (one of {', '.join(options)})")
    return {part: spec[part] for part in parts}


def _clean_name(name: object) -> str:
    if not isinstance(name, str):
        raise CastError("name is required")
    clean = " ".join(name.split())
    if not clean:
        raise CastError("name must not be empty")
    if len(clean) > _MAX_NAME:
        raise CastError(f"name is longer than {_MAX_NAME} characters")
    return clean


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:_MAX_NAME].strip("-")
    if not slug:
        raise CastError("name needs a letter or a digit")
    return slug


def _free_id(base: str, taken: set[str]) -> str:
    eid, n = base, 1
    while eid in taken:
        n += 1
        eid = f"{base[:36]}-{n}"
    return eid


def _rig(rig: object, workspace_id: str) -> str:
    """A registered rig a character in this workspace can use: a client's rig stays in client workspaces."""
    if not isinstance(rig, str) or rig not in RIGS:
        raise CastError(f"unknown rig: {rig!r} (one of {', '.join(RIGS)})")
    if workspace_id == BRAND and RIGS[rig]["owner"] == "client":
        raise CastError(f"the {rig} rig is a client's: add the character to that client's workspace")
    return rig


def _drafts(rig: str) -> bool:
    """A profile on a client's rig starts as a draft until the user approves its look."""
    return RIGS[rig]["owner"] == "client"


def _palette(palette: object, rig: str) -> str:
    if rig not in RIGS:
        raise CastError("character rig is unavailable")
    palettes = RIGS[rig]["palettes"]
    if palette is None:
        return palettes[0]
    if not isinstance(palette, str) or palette not in palettes:
        raise CastError(f"invalid palette: {palette!r} (one of {', '.join(palettes)})")
    return palette


# ── reading ──────────────────────────────────────────────────────────────────────

def _valid_expression(entry: object, rig: str) -> dict | None:
    if not isinstance(entry, dict):
        return None
    try:
        spec = validate_spec(entry.get("spec"), rig)
        name = _clean_name(entry.get("name"))
    except CastError:
        return None
    eid = entry.get("id")
    if not isinstance(eid, str) or not _ID_RE.match(eid) or eid in RIGS[rig]["expressions"]:
        return None
    return {"id": eid, "name": name, "spec": spec, "createdAt": str(entry.get("createdAt") or ""),
            **({"draft": True} if entry.get("draft") is True else {})}


def _valid_character(entry: object) -> dict | None:
    if not isinstance(entry, dict):
        return None
    cid, rig = entry.get("id"), entry.get("rig")
    if not isinstance(cid, str) or not _ID_RE.match(cid) or not isinstance(rig, str):
        return None
    if rig not in RIGS:
        return dict(entry)
    try:
        name = _clean_name(entry.get("name"))
        palette = _palette(entry.get("palette"), rig)
    except CastError:
        return None
    approved = entry.get("approved")
    raw_art = entry.get("art")
    art = list(dict.fromkeys(a for a in (raw_art if isinstance(raw_art, list) else [])
                             if isinstance(a, str) and _ASSET_ID_RE.match(a)))[:_MAX_ART]
    raw = entry.get("expressions")
    expressions: list[dict] = []
    for e in raw if isinstance(raw, list) else []:
        valid = _valid_expression(e, rig)
        if valid is not None and all(x["id"] != valid["id"] for x in expressions):
            expressions.append(valid)
    return {"id": cid, "name": name, "rig": rig, "art": art, "palette": palette,
            "approved": approved if isinstance(approved, bool) else not _drafts(rig),
            "expressions": expressions, "createdAt": str(entry.get("createdAt") or "")}


def _default_character() -> dict:
    return {"id": DEFAULT_CHARACTER_ID, "name": DEFAULT_CHARACTER_NAME, "rig": DEFAULT_RIG, "art": [],
            "palette": RIGS[DEFAULT_RIG]["palettes"][0], "approved": True, "expressions": [], "createdAt": ""}


def _read(workspace_id: str) -> dict:
    """Read profiles defensively while retaining opaque top-level data."""
    doc: object = None
    path = _path(workspace_id)
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = None
    doc = doc if isinstance(doc, dict) else {}
    raw = doc.get("characters")
    if not isinstance(raw, list):
        raw = []
    characters: list[dict] = []
    for entry in raw:
        valid = _valid_character(entry)
        if valid is None or any(c["id"] == valid["id"] for c in characters):
            continue
        if valid["rig"] in RIGS and valid["id"] == DEFAULT_CHARACTER_ID and (workspace_id != BRAND or valid["rig"] != DEFAULT_RIG):
            continue
        characters.append(valid)
    if workspace_id == BRAND and not any(c["id"] == DEFAULT_CHARACTER_ID for c in characters):
        characters.insert(0, _default_character())
    default = doc.get("default")
    return {**doc, "default": default if isinstance(default, str) else None, "characters": characters}


def _write(workspace_id: str, doc: dict) -> None:
    if workspace_id == BRAND:
        library.ensure_brand_workspace()
    path = _path(workspace_id)
    opaque: dict[str, str] = {}
    if path.is_file():
        try:
            source = path.read_bytes().decode("utf-8")
            decoder = json.JSONDecoder()
            original = decoder.decode(source)
            if isinstance(original, dict):
                pos = source.index("{") + 1
                while pos < len(source):
                    while source[pos].isspace():
                        pos += 1
                    if source[pos] == "}":
                        break
                    key, pos = decoder.raw_decode(source, pos)
                    pos = source.index(":", pos) + 1
                    while source[pos].isspace():
                        pos += 1
                    start = pos
                    _, pos = decoder.raw_decode(source, pos)
                    if key not in ("default", "characters"):
                        opaque[key] = source[start:pos]
                    while source[pos].isspace():
                        pos += 1
                    if source[pos] == ",":
                        pos += 1
        except (OSError, UnicodeDecodeError, ValueError):
            opaque = {}
    members = [json.dumps(key) + ": " + opaque.get(key, json.dumps(value, indent=2))
               for key, value in {**doc, **{key: None for key in opaque if key not in doc}}.items()]
    # Preserve opaque JSON bytes, including number spellings and interior whitespace.
    payload = ("{\n" + ",\n".join(members) + "\n}\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        for delay in library._REPLACE_RETRY_DELAYS_S:
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                time.sleep(delay)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def _client_workspaces() -> Iterator[str]:
    if not config.WORKSPACES_DIR.is_dir():
        return
    for entry in sorted(config.WORKSPACES_DIR.iterdir()):
        if entry.name != BRAND and _ID_RE.match(entry.name) and (entry / "cast.json").is_file():
            yield entry.name


def _all() -> dict[str, dict]:
    """Every workspace that has characters, brand first."""
    return {BRAND: _read(BRAND), **{ws: _read(ws) for ws in _client_workspaces()}}


def _find(docs: dict[str, dict], character_id: str) -> tuple[str, dict]:
    for ws, doc in docs.items():
        for character in doc["characters"]:
            if character["id"] == character_id:
                return ws, character
    raise FileNotFoundError(f"unknown character: {character_id}")


def _client_workspace(workspace_id: object) -> dict | None:
    """A video's client workspace as `{id, name}`, or None for brand, unsorted and unknown ids."""
    if not isinstance(workspace_id, str) or workspace_id in (BRAND, library.UNSORTED_WORKSPACE_ID):
        return None
    try:
        found = library.get_workspace(workspace_id)
    except library.LibraryError:
        return None
    return {"id": workspace_id, "name": str(found.get("name") or workspace_id)} if found else None


def _owner(workspace_id: object) -> str:
    """A workspace that can own characters: the brand or an existing client workspace."""
    if workspace_id == BRAND:
        return BRAND
    client = _client_workspace(workspace_id)
    if client is None:
        raise CastError(f"unknown workspace: {workspace_id!r}")
    return client["id"]


def _tag(character: dict, workspace_id: str) -> dict:
    return {**character, "workspaceId": workspace_id, "available": character["rig"] in RIGS}


def video_cast(workspace_id: str | None) -> dict:
    """Everything a video in `workspace_id` can cast, as `{characters, default, workspace, brand}`: the
    brand's characters, then a client workspace's own, each with its `workspaceId`. `default` is the
    character a shot gets when it names none (or one the video can't see); `workspace` is the client
    workspace as `{id, name}`, or None for brand and unsorted videos; `brand` is the eyebrow its
    animated headlines carry (`library.workspace_brand`)."""
    brand = _read(BRAND)
    client = _client_workspace(workspace_id)
    own = _read(client["id"]) if client else {"default": None, "characters": []}
    brand_ids = {c["id"] for c in brand["characters"]}
    characters = [_tag(c, BRAND) for c in brand["characters"]]
    if client:
        characters += [_tag(c, client["id"]) for c in own["characters"] if c["id"] not in brand_ids]
    ids = {c["id"] for c in characters}
    default = (own["default"] if own["default"] in ids
               else brand["default"] if brand["default"] in brand_ids else DEFAULT_CHARACTER_ID)
    return {"characters": characters, "default": default, "workspace": client,
            "brand": library.workspace_brand(client["id"] if client else None)}


# ── characters ───────────────────────────────────────────────────────────────────

def _art(art: object, workspace_id: str) -> list[str]:
    """Approved art: images in the character's own workspace library."""
    if not isinstance(art, list) or len(art) > _MAX_ART:
        raise CastError(f"art must be a list of at most {_MAX_ART} asset ids")
    out: list[str] = []
    for asset_id in art:
        try:
            asset = library.get_asset(workspace_id, asset_id) if isinstance(asset_id, str) else None
        except (library.LibraryError, FileNotFoundError):
            asset = None
        if asset is None or asset.get("kind") != "image":
            raise CastError(f"art must be images in the character's workspace library: {asset_id!r}")
        if asset_id not in out:
            out.append(asset_id)
    return out


def _check_character_name(name: str, docs: dict[str, dict], workspace_id: str, *, keep: str | None = None) -> None:
    """A name is unique wherever its character shows: a brand one in every workspace, a client one in
    its workspace (next to the brand's)."""
    scope = docs.values() if workspace_id == BRAND else [docs[ws] for ws in (BRAND, workspace_id) if ws in docs]
    if any(str(c.get("name") or "").casefold() == name.casefold() and c["id"] != keep
           for d in scope for c in d["characters"]):
        raise CastConflict(f"“{name}” already exists")


def create_character(workspace_id: object, name: object, rig: object = DEFAULT_RIG, *,
                     art: object = None, palette: object = None) -> dict:
    """Add a character profile to a workspace. Its id is the name's slug (suffixed when any workspace
    holds it) and never changes, so scenes that cast it keep it through renames. On a client's rig it
    starts as a draft (`approved: false`)."""
    ws, clean = _owner(workspace_id), _clean_name(name)
    rig_id = _rig(rig, ws)
    entry_art = _art([] if art is None else art, ws)
    entry_palette = _palette(palette, rig_id)
    with _lock:
        docs = _all()
        docs.setdefault(ws, _read(ws))
        _check_character_name(clean, docs, ws)
        taken = {c["id"] for d in docs.values() for c in d["characters"]}
        entry = {"id": _free_id(_slug(clean), taken), "name": clean, "rig": rig_id, "art": entry_art,
                 "palette": entry_palette, "approved": not _drafts(rig_id), "expressions": [], "createdAt": _now()}
        docs[ws]["characters"].append(entry)
        _write(ws, docs[ws])
    return _tag(entry, ws)


def update_character(character_id: str, *, name: object = None, art: object = None, palette: object = None,
                     rig: object = None, approved: object = None) -> dict:
    """Rename a character, replace its approved art, move it to another rig or palette, or approve its
    look; FileNotFoundError for an unknown id. A new rig keeps the palette when that rig has it (else
    takes the rig's first) and, on a client's rig, makes the look a draft again. A character with custom
    expressions keeps its rig, since those are combinations of that rig's parts."""
    if approved is not None and not isinstance(approved, bool):
        raise CastError("approved must be true or false")
    with _lock:
        docs = _all()
        ws, character = _find(docs, character_id)
        if name is not None:
            clean = _clean_name(name)
            _check_character_name(clean, docs, ws, keep=character_id)
            character["name"] = clean
        if art is not None:
            character["art"] = _art(art, ws)
        if rig is not None and rig != character["rig"]:
            if character_id == DEFAULT_CHARACTER_ID:
                raise CastError(f"{DEFAULT_CHARACTER_NAME} is drawn by the default rig")
            new_rig = _rig(rig, ws)
            if character["expressions"]:
                raise CastError(f"{character['name']} has custom expressions for its rig; delete them first")
            character["rig"] = new_rig
            if character["palette"] not in RIGS[new_rig]["palettes"]:
                character["palette"] = RIGS[new_rig]["palettes"][0]
            character["approved"] = not _drafts(new_rig)
        if palette is not None:
            character["palette"] = _palette(palette, character["rig"])
        if approved is not None:
            character["approved"] = approved
        _write(ws, docs[ws])
    return _tag(character, ws)


def character_usage(character_id: str, docs: Iterable[dict]) -> dict:
    """How many animated scenes (and videos) draw a character, as lead, co-star or ensemble member, among the project
    documents `docs`: a scene that names no character draws its video's default. What deleting the
    character would change. FileNotFoundError for an unknown id."""
    _find(_all(), character_id)
    casts: dict[str | None, dict] = {}
    scenes = videos = 0
    for doc in docs:
        ws = doc.get("workspaceId") if isinstance(doc.get("workspaceId"), str) else None
        if ws not in casts:
            casts[ws] = video_cast(ws)
        cast = casts[ws]
        drawn = 0
        for scene in doc.get("scenes") or []:
            shot = scene.get("shot") if isinstance(scene, dict) else None
            if not isinstance(shot, dict) or shot.get("mode") not in ("animation", "cameo"):
                continue
            costar = shot.get("costar") if shot.get("mode") == "animation" else None
            members = _members(shot) if shot.get("mode") == "animation" else []
            looks = [shot, *([costar] if isinstance(costar, dict) else []), *members]
            drawn += any(character_for(cast, look.get("character"))["id"] == character_id for look in looks)
        scenes, videos = scenes + drawn, videos + (drawn > 0)
    return {"scenes": scenes, "videos": videos}


def delete_character(character_id: str) -> bool:
    """Remove a character and its custom expressions; scenes that cast it get the video's default.
    The Lens is built in and stays."""
    if character_id == DEFAULT_CHARACTER_ID:
        raise CastError(f"{DEFAULT_CHARACTER_NAME} is built in")
    with _lock:
        docs = _all()
        try:
            ws, character = _find(docs, character_id)
        except FileNotFoundError:
            return False
        docs[ws]["characters"].remove(character)
        _write(ws, docs[ws])
    return True


def set_default(workspace_id: object, character_id: object) -> dict:
    """Mark the character a workspace's videos get when a shot names none (None clears the mark): one
    of the brand's or, for a client workspace, one of its own. Returns that workspace's video cast."""
    ws = _owner(workspace_id)
    scope = None if ws == BRAND else ws
    with _lock:
        if character_id is not None and character_id not in {c["id"] for c in video_cast(scope)["characters"]}:
            raise CastError(f"unknown character for this workspace: {character_id!r}")
        doc = _read(ws)
        doc["default"] = character_id
        _write(ws, doc)
    return video_cast(scope)


# ── custom expressions ──────────────────────────────────────────────────────────

def _check_expression_name(name: str, character: dict, *, keep: str | None = None) -> None:
    if character["rig"] not in RIGS:
        raise CastError("character rig is unavailable")
    if _slug(name) in RIGS[character["rig"]]["expressions"]:
        raise CastConflict(f"“{name}” is a built-in expression")
    if any(e["name"].casefold() == name.casefold() and e["id"] != keep for e in character["expressions"]):
        raise CastConflict(f"“{name}” already exists")


def create_expression(character_id: str, name: object, spec: object, draft: object = False) -> dict:
    """Add a custom expression to a character, as a draft to approve when `draft`. Its id is the name's
    slug (suffixed if a renamed expression holds it) and never changes, so scenes that use it keep
    working through renames."""
    if not isinstance(draft, bool):
        raise CastError("draft must be true or false")
    clean = _clean_name(name)
    with _lock:
        docs = _all()
        ws, character = _find(docs, character_id)
        valid = validate_spec(spec, character["rig"])
        _check_expression_name(clean, character)
        eid = _free_id(_slug(clean), {e["id"] for e in character["expressions"]})
        entry = {"id": eid, "name": clean, "spec": valid, "createdAt": _now(), **({"draft": True} if draft else {})}
        character["expressions"].append(entry)
        _write(ws, docs[ws])
    return entry


def update_expression(character_id: str, expression_id: str, *, name: object = None, spec: object = None,
                      approved: object = None) -> dict:
    """Rename and/or respec a custom expression, or approve a draft (`approved=True`); FileNotFoundError
    for an unknown character or id."""
    if approved not in (None, True):
        raise CastError("approved can only be true: discard a draft to drop it")
    with _lock:
        docs = _all()
        ws, character = _find(docs, character_id)
        entry = next((e for e in character["expressions"] if e["id"] == expression_id), None)
        if entry is None:
            raise FileNotFoundError(f"unknown expression: {expression_id}")
        if name is not None:
            clean = _clean_name(name)
            _check_expression_name(clean, character, keep=expression_id)
            entry["name"] = clean
        if spec is not None:
            entry["spec"] = validate_spec(spec, character["rig"])
        if approved:
            entry.pop("draft", None)
        _write(ws, docs[ws])
    return entry


def delete_expression(character_id: str, expression_id: str) -> bool:
    """Remove a custom expression. Scenes that used it fall back to their role's look."""
    with _lock:
        docs = _all()
        try:
            ws, character = _find(docs, character_id)
        except FileNotFoundError:
            return False
        remaining = [e for e in character["expressions"] if e["id"] != expression_id]
        if len(remaining) == len(character["expressions"]):
            return False
        character["expressions"] = remaining
        _write(ws, docs[ws])
    return True


# ── resolving a scene's shot ────────────────────────────────────────────────────

def character_for(cast: Mapping | None, character_id: object) -> dict:
    """The profile a shot casts: the one it names when its video can see it, else the video's default
    (the Lens without a cast)."""
    characters = [c for c in (cast or {}).get("characters") or [] if isinstance(c, dict)]
    by_id = {c.get("id"): c for c in characters}
    named = by_id.get(character_id) if isinstance(character_id, str) else None
    return named or by_id.get((cast or {}).get("default")) or (characters[0] if characters else _default_character())


def _cast_look(look: dict, cast: Mapping | None) -> tuple[dict, dict | None]:
    profile = character_for(cast, look.get("character"))
    stored_rig = str(profile.get("rig") or DEFAULT_RIG)
    rig = stored_rig if stored_rig in RIGS else DEFAULT_RIG
    palettes = RIGS[rig]["palettes"]
    palette = profile.get("palette") if profile.get("palette") in palettes else palettes[0]
    out = {**look, "character": stored_rig, "palette": palette}
    if "pose" in out and out["pose"] not in RIGS[rig]["poses"]:
        del out["pose"]
    expression = out.get("expression")
    if not isinstance(expression, str) or not expression or expression in RIGS[rig]["expressions"]:
        return out, None
    custom = next((e.get("spec") for e in profile.get("expressions") or []
                   if isinstance(e, Mapping) and e.get("id") == expression), None)
    if isinstance(custom, dict):
        try:
            return out, validate_spec(custom, rig)
        except CastError:
            pass
    del out["expression"]
    return out, None


def _brand(cast: Mapping | None) -> dict:
    brand = (cast or {}).get("brand")
    brand = brand if isinstance(brand, Mapping) else {}
    name, accent = brand.get("name"), brand.get("accent")
    return {"name": name if isinstance(name, str) else library.BRAND_EYEBROW,
            "accent": accent if isinstance(accent, str) else library.BRAND_ACCENT}


def resolve_shot(shot: dict | None, cast: Mapping | None) -> tuple[dict, dict | None]:
    """A scene's stored shot resolved against its video's cast: `(shot, custom)`. `character` becomes
    the registry id of the rig of the profile it casts (`character_for`) and `palette` that profile's
    palette. A custom expression of that profile brings its spec as `custom`; an id that is neither the
    rig's built-in nor the profile's (a deleted one, or another character's) is dropped so the shot
    falls back to the role's look, and so is a pose the rig doesn't have. Built-in ids pass through
    untouched. A duo's `costar` is resolved the same way against its own profile, its custom spec riding
    inside it as `costar.custom`, and so is each `ensemble` member. The eyebrow over its headline
    is the shot's own (an empty one hides it), else the video's brand name, in the brand's accent."""
    out, custom = _cast_look(shot or {}, cast)
    brand = _brand(cast)
    if not isinstance(out.get("eyebrow"), str):
        out["eyebrow"] = brand["name"]
    out["accent"] = brand["accent"]
    costar = out.get("costar")
    if isinstance(costar, dict):
        look, spec = _cast_look(costar, cast)
        out["costar"] = {**look, "custom": spec} if spec else look
    if isinstance(out.get("ensemble"), list):
        members = (_cast_look(m, cast) for m in _members(out))
        out["ensemble"] = [{**look, "custom": spec} if spec else look for look, spec in members]
    return out, custom


def _members(shot: dict) -> list[dict]:
    """A shot's ensemble members: the performers after the lead and the co-star."""
    ensemble = shot.get("ensemble")
    return [m for m in ensemble if isinstance(m, dict)] if isinstance(ensemble, list) else []


# ── Describe: a short phrase to a proposed spec and name ────────────────────────
# A deterministic keyword mapper stands behind `DESCRIBER`, rig by rig, and always answers: it is the
# offline fallback `scripts/agent_host.py`'s model-backed one-shot (the co-pilot's own SDK session,
# tool-less) falls back to on anything that keeps it from a clean answer. `agent_host` is the only
# module that touches the SDK, so it calls `validate_description` here directly instead of going
# through `DESCRIBER`; `describe` (this module's own entry point) always uses the keyword path.

Describer = Callable[[str, str], dict]

_EYE_WORDS = (
    ("side", r"eyebrow|brow|skeptic|sceptic|suspicious|doubt|dubious|wink|sly|quizzical"),
    ("closed", r"sleep|tired|drows|asleep|bored|yawn|calm|peaceful|serene|relax|zen|bliss|dream"),
    ("tall", r"surpris|shock|startl|astonish|scared|afraid|fright|alarm|gasp|panic|terrif"),
    ("wide", r"excit|amaz|wow|thrill|eager|delight|starstruck|awe|love"),
    ("uneven", r"confus|puzzl|baffl|dizzy|lost|weird|silly|goofy|derp|crazy"),
    ("narrow", r"focus|determin|serious|stern|angry|grump|annoy|unimpress|deadpan|concentrat|intense|smug|sassy|cool"),
    ("arc", r"happy|proud|pleased|glad|joy|cheer|content|grateful|warm|smil|laugh|friendly|cute"),
    ("open", r"neutral|plain|default|normal|blank"),
)
_MOUTH_WORDS = (
    ("round", r"surpris|shock|gasp|scared|alarm"),
    ("small", r"sleep|tired|sad|bored|shy"),
    ("flat", r"focus|serious|stern|angry|smug|concentrat"),
    ("crooked", r"confus|puzzl|skeptic|quizzical|sly"),
    ("grin", r"happy|proud|excit|cheer|joy|laugh|delight"),
    ("smile", r"idea|curious|neutral|calm|plain"),
)
_RULES = tuple(
    (part, tuple((value, re.compile(rf"\b(?:{words})\w*")) for value, words in table))
    for part, table in (("eyes", _EYE_WORDS), ("mouth", _MOUTH_WORDS))
)
_FILLER = {
    "a", "an", "the", "very", "really", "super", "so", "bit", "little", "kind", "sort", "of", "looking",
    "feeling", "slightly", "quite", "too", "and", "with", "but", "is", "character", "face", "look", "expression",
    "extremely", "one", "totally", "just", "all", "his", "her", "its",
}


def _neutral_spec(rig: str) -> dict:
    """Each part's first (calmest) option: RIGS lists them that way, so this is also the fallback look
    when nothing else matches."""
    return {part: options[0] for part, options in RIGS[rig]["parts"].items()}


def describe_by_keywords(text: str, rig: str = DEFAULT_RIG) -> dict:
    """Propose `{name, spec, matched, source}` from keywords, offline. On the default rig, each part takes the
    first rule (in the order above) whose word appears, else its neutral option. Another rig has no
    Python-side copy of its built-ins' actual looks (only their ids, mirrored from motion-core in
    `RIGS`), so it matches the phrase against ITS OWN built-in expression ids instead and otherwise
    starts neutral either way; the composer's part pickers do the rest. The name is the first
    meaningful word, on every rig."""
    lowered = text.lower()
    spec = _neutral_spec(rig)
    matched: list[str] = []
    if rig == DEFAULT_RIG:
        for part, rules in _RULES:
            for value, pattern in rules:
                hit = pattern.search(lowered)
                if hit:
                    spec[part] = value
                    if hit.group(0) not in matched:
                        matched.append(hit.group(0))
                    break
    else:
        expr = next((e for e in RIGS[rig]["expressions"] if re.search(rf"\b{re.escape(e)}\w*", lowered)), None)
        if expr:
            matched.append(expr)
    word = next((w for w in re.findall(r"[a-z][a-z'-]*", lowered) if w not in _FILLER and len(w) > 1), "")
    return {"name": word[:_MAX_NAME].capitalize() or "New look", "spec": spec, "matched": matched, "source": "keywords"}


DESCRIBER: Describer = describe_by_keywords


def validate_description(proposal: object, rig: str, source: str) -> dict:
    """A describer's raw `{name, spec}` (this module's own proposal, or the co-pilot's structured
    completion) as the wire shape `{name, spec, matched, source}`, validated against `rig`'s spec
    space. CastError for a proposal that isn't `{name, spec}`-shaped or whose spec doesn't fit."""
    if not isinstance(proposal, dict):
        raise CastError("a description must propose {name, spec}")
    matched = proposal.get("matched")
    return {
        "name": _clean_name(proposal.get("name")),
        "spec": validate_spec(proposal.get("spec"), rig),
        "matched": [m for m in matched if isinstance(m, str)] if isinstance(matched, list) else [],
        "source": source,
    }


def describe_rig(rig: object = None, character: object = None) -> str:
    """The rig Describe should propose for: an explicit rig id, else an existing character's own rig,
    else the default rig. CastError for an unknown rig or character."""
    if rig is not None:
        if not isinstance(rig, str) or rig not in RIGS:
            raise CastError(f"unknown rig: {rig!r} (one of {', '.join(RIGS)})")
        return rig
    if character is not None:
        if not isinstance(character, str):
            raise CastError("character must be a character id")
        try:
            _, found = _find(_all(), character)
        except FileNotFoundError:
            raise CastError(f"unknown character: {character!r}") from None
        if found["rig"] not in RIGS:
            raise CastError("character rig is unavailable")
        return found["rig"]
    return DEFAULT_RIG


def describe(text: object, rig: str = DEFAULT_RIG) -> dict:
    """`DESCRIBER`'s (keyword) proposal for `text` on `rig` (the default rig), cleaned and validated
    against that rig's spec space. CastError for empty text, an unknown rig, or an invalid proposal."""
    if not isinstance(text, str) or not text.strip():
        raise CastError("describe the expression first")
    if rig not in RIGS:
        raise CastError(f"unknown rig: {rig!r} (one of {', '.join(RIGS)})")
    proposal = DESCRIBER(text.strip()[:_MAX_DESCRIPTION], rig)
    return validate_description(proposal, rig, str((proposal or {}).get("source") or "keywords"))
