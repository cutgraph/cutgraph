"""Animated scene renders for export.

A scene whose shot mode is animation or cameo is drawn by motion-core in Node
(`motion-core/bin/render-scene.ts`: @napi-rs/canvas frames piped to ffmpeg, no browser) and composited
by `pipeline.compile` over its timeline span. `scene_specs` is the shared scene render spec rule: the
TS preview derives the same list from the same project doc and transcript. Renders are
content-addressed under `outputs/scenes/`, so an unchanged scene never re-renders, and the cache is
trimmed least recently used first past `config.SCENE_CACHE_MAX_BYTES`.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections.abc import Callable, Collection, Mapping
from pathlib import Path

import config
from pipeline import cast as cast_mod
from pipeline import compile as compile_stage
from pipeline import edits
from pipeline import names, packs
from pipeline import project as project_mod
from pipeline import staging
from pipeline import timebase
from pipeline.runtime_tools import font_path, node_path
from pipeline.compile import SceneLayer
from pipeline.runtime_tools import tool_path

MOTION_CORE = config.CODE_ROOT / "motion-core"
RENDERER = MOTION_CORE / "bin" / "render-scene.ts"
_ANIMATED = ("animation", "cameo")


class SceneRenderError(RuntimeError):
    pass


def flat_words(path: Path) -> list[dict] | None:
    """Flat raw words of a words sidecar in segments→words order (the `wordIdx` space), or None when
    the file is missing or unreadable."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return [w for seg in raw.get("segments", []) for w in seg.get("words", [])]


def transcript_words(proj: project_mod.Project, root: Path) -> list[dict]:
    """The transcript words the Story model reads for this project: its speech source's sidecar."""
    source = next((c.media.source for t in proj.tracks if t.kind == "speech" for c in t.clips), None)
    if source is None:
        return []
    transcripts = root / "outputs" / "transcripts"
    path = names.exact_path(transcripts / f"{Path(source).stem}.words.json", transcripts)
    return flat_words(path) or []


def shot_mode(shot: dict | None) -> str:
    mode = (shot or {}).get("mode")
    return mode if mode in _ANIMATED else "footage"


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _specs(proj: project_mod.Project, words: list[dict]) -> list[dict]:
    speech = next((t for t in proj.tracks if t.kind == "speech"), None)
    if not proj.scenes or not words or speech is None or not speech.clips:
        return []
    fps = proj.fps
    media = speech.clips[0].media.source
    clips = [c for c in speech.clips if c.media.source == media]

    def to_timeline(src_f: int) -> int | None:
        clip = next((c for c in clips if c.srcInF <= src_f < c.srcOutF), None)
        return None if clip is None else clip.tlStartF + src_f - clip.srcInF

    frames = edits.resolve_word_frames([(w.get("start"), w.get("end")) for w in words], fps)
    last_end = max((float(w["end"]) for w in words if _finite(w.get("end"))), default=0.0)
    media_end_f = max([timebase.sec_to_frame(max(last_end, 0.0), fps), *(c.srcOutF for c in clips)])

    def src_at(idx: int) -> int:
        return frames[idx] if idx < len(words) else media_end_f

    starts = sorted({s.startWord for s in proj.scenes})
    specs: list[dict] = []
    for scene in proj.scenes:
        mode = shot_mode(scene.shot)
        if mode == "footage":
            continue
        start_word = min(max(scene.startWord, 0), len(words))
        end_word = min(next((s for s in starts if s > scene.startWord), len(words)), len(words))
        src_start = 0 if start_word == 0 else src_at(start_word)
        src_end = src_at(end_word)
        runs = sorted(((max(src_start, c.srcInF), min(src_end, c.srcOutF), c) for c in clips
                       if max(src_start, c.srcInF) < min(src_end, c.srcOutF)), key=lambda r: r[0])
        dur_f = sum(b - a for a, b, _c in runs)
        if dur_f == 0:
            continue
        first_a, _b, first_clip = runs[0]
        start_f = first_clip.tlStartF + first_a - first_clip.srcInF
        first_word_f = next((f for i in range(start_word, end_word)
                             if (f := to_timeline(frames[i])) is not None), None)
        cue = (scene.shot or {}).get("cueWord")
        cue_word_f = to_timeline(frames[cue]) if isinstance(cue, int) and start_word <= cue < end_word else None
        specs.append({
            "sceneId": scene.id, "mode": mode, "shot": dict(scene.shot or {}), "role": scene.role,
            "title": scene.title, "startF": start_f, "durF": dur_f,
            "firstWordF": first_word_f, "cueWordF": cue_word_f,
        })
    return specs


def scene_specs(doc: dict, words: list[dict]) -> list[dict]:
    """The scene render spec rule, in story order: one entry per animation/cameo scene still on the
    timeline, `{sceneId, mode, shot, role, title, startF, durF, firstWordF, cueWordF}` in timeline
    frames, `shot` as stored. `words` are the flat transcript words (`start`/`end` seconds). Mirrors the
    Story model (ui/src/v3/story/model.ts `sceneSpans`, cutmap `srcToTimeline`) exactly."""
    return _specs(project_mod.from_dict(doc), words)


def has_animated_scenes(doc: dict, *, root: Path) -> bool:
    """Whether an export of `doc` composites any animated scene (one still on the timeline)."""
    proj = project_mod.from_dict(doc)
    return bool(_specs(proj, transcript_words(proj, root)))


def motion_core_version() -> str:
    """Content hash of everything that draws or encodes a scene: motion-core's sources, the Node env
    (fonts), the pack loader, the renderer itself and the character packs it loads."""
    h = hashlib.sha256()
    scripts = MOTION_CORE / "scripts"
    sources = [p for p in (MOTION_CORE / "src").rglob("*") if p.is_file()]
    for path in sorted([*sources, scripts / "node-env.ts", scripts / "packs.ts", scripts / "encoder-ladder.ts", RENDERER]):
        h.update(path.relative_to(MOTION_CORE).as_posix().encode() + b"\0" + path.read_bytes())
    from pipeline.encoder_ladder import DEFINITION_PATH

    definition = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
    scene_policy = {
        "orders": {"withLibx264": definition["orders"]["withLibx264"]["scene"],
                   "withoutLibx264": definition["orders"]["withoutLibx264"]},
        "defaults": definition["defaults"],
        "profiles": definition["profiles"]["scene"],
    }
    h.update(b"schema/encoder-ladder.json\0" + json.dumps(scene_policy, sort_keys=True).encode())
    for path in packs.files(config.PACKS_DIR):
        h.update(b"packs/" + path.relative_to(config.PACKS_DIR).as_posix().encode() + b"\0" + path.read_bytes())
    for style in ("regular", "semibold", "bold", "black"):
        h.update(style.encode() + b"\0" + font_path(style, role="sans").read_bytes())
    return h.hexdigest()


def render_request(spec: dict, *, width: int, height: int, fps: int, cast: Mapping | None = None,
                   workspace_id: str | None = None) -> dict:
    """The renderer CLI's input for one spec. A cameo renders with alpha to overlay the footage. The
    shot is resolved against the video's cast (`pipeline.cast.video_cast`): its character becomes the
    profile's rig and palette, a custom expression travels as `custom`, and one the character doesn't
    have falls back to the role look. A duo's co-star and interaction ride in `shot` (the co-star
    resolved the same way), so the cache key covers both characters. A `screen` shot's library image
    rides in it too, resolved in the video's workspace (`pipeline.staging.resolve_screen`)."""
    keys = ("durF", "mode", "role", "title", "startF", "firstWordF", "cueWordF")
    shot, custom = cast_mod.resolve_shot(spec["shot"], cast or {})
    shot = staging.resolve_screen(shot, workspace_id)
    return {"width": width, "height": height, "fps": fps, "shot": shot, **{k: spec[k] for k in keys},
            "alpha": spec["mode"] == "cameo", **({"custom": custom} if custom else {})}


def cache_key(request: dict, version: str) -> str:
    """Content hash of what a scene's pixels depend on. Word frames only matter relative to the scene
    start (shotTiming), so a scene that merely slides along the timeline keeps its render. A resolved
    image counts by its content-addressed file name."""
    start = request["startF"]
    body = {k: v for k, v in request.items() if k not in {"startF", "firstWordF", "cueWordF"}}
    body["shot"] = staging.keyed_shot(request["shot"])
    for key in ("firstWordF", "cueWordF"):
        body[key] = None if request[key] is None else request[key] - start
    body["motionCore"] = version
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def render_scene(request: dict, dest: Path) -> dict:
    """Render one scene with the Node renderer into `dest`, atomically, logging each ffmpeg command it
    reports to run.log. Returns the renderer's `done` event (encoder, frames, throughput)."""
    try:
        node = node_path()
    except RuntimeError as error:
        raise SceneRenderError(str(error)) from error
    if node is None:
        raise SceneRenderError("Node.js 24+ is required to render animated scenes")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.stem}.partial{dest.suffix}")
    command = [str(node), str(RENDERER), "-", str(partial), tool_path("ffmpeg")]
    if config.PACKS_DIR.is_dir():
        command.extend(["--packs", str(config.PACKS_DIR)])
    proc = subprocess.run(command, input=json.dumps(request),
                          capture_output=True, encoding="utf-8", errors="replace", cwd=MOTION_CORE)
    done: dict = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "ffmpeg":
            compile_stage._log_ffmpeg(event["cmd"])
        elif event.get("event") == "done":
            done = event
    if proc.returncode != 0 or not done or not partial.exists():
        partial.unlink(missing_ok=True)
        raise SceneRenderError(f"scene render failed: {(proc.stderr or proc.stdout).strip()[-600:]}")
    os.replace(partial, dest)
    return done


def evict_scenes(cache: Path, *, keep: Collection[Path], cap: int) -> list[Path]:
    """Trim the scene cache to `cap` bytes, least recently used (oldest mtime) first, never removing a
    file in `keep`. Returns the removed files."""
    entries: list[tuple[int, int, Path]] = []
    for path in cache.glob("*"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_file():
            entries.append((stat.st_mtime_ns, stat.st_size, path))
    total = sum(size for _mtime, size, _path in entries)
    kept = {p.name for p in keep}
    removed: list[Path] = []
    for _mtime, size, path in sorted(entries):
        if total <= cap:
            break
        if path.name in kept:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        removed.append(path)
    return removed


def ensure_scenes(
    doc: dict,
    *,
    root: Path,
    scene_id: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_plan: Callable[[list[tuple[str, int]]], None] | None = None,
) -> tuple[list[SceneLayer], int]:
    """Render every animated scene of `doc` (or only `scene_id`) that is not cached under
    `root/outputs/scenes/` yet, then trim the cache (`evict_scenes`) around this request's renders.
    Returns the compositing layers in story order and how many scenes were rendered;
    `on_plan(scenes)` fires once before any render with each scene's `(sceneId, n)` in story order, n
    being the render it waits for (0: cached), and `on_progress(n, m)` fires before the n-th of m renders."""
    proj = project_mod.from_dict(doc)
    specs = [s for s in _specs(proj, transcript_words(proj, root)) if scene_id in (None, s["sceneId"])]
    if not specs:
        return [], 0
    version = motion_core_version()
    cast = cast_mod.video_cast(proj.workspaceId)
    cache = root / "outputs" / "scenes"
    layers: list[SceneLayer] = []
    missing: dict[Path, dict] = {}
    for spec in specs:
        request = render_request(spec, width=proj.width, height=proj.height, fps=proj.fps, cast=cast,
                                 workspace_id=proj.workspaceId)
        dest = cache / f"{cache_key(request, version)}{'.mov' if request['alpha'] else '.mp4'}"
        try:
            os.utime(dest)  # a hit is a use: eviction goes by mtime
        except FileNotFoundError:
            missing.setdefault(dest, request)
        layers.append(SceneLayer(str(dest), spec["startF"], spec["durF"], request["alpha"]))
    if on_plan is not None:
        order = {dest: n for n, dest in enumerate(missing, 1)}
        on_plan([(spec["sceneId"], order.get(Path(layer.path), 0)) for spec, layer in zip(specs, layers)])
    for n, (dest, request) in enumerate(missing.items(), 1):
        if on_progress is not None:
            on_progress(n, len(missing))
        render_scene(request, dest)
    evict_scenes(cache, keep=[Path(layer.path) for layer in layers], cap=config.SCENE_CACHE_MAX_BYTES)
    return layers, len(missing)
