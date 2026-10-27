"""Project compiler: project.json → one clocked FFmpeg graph → final MP4.

The browser and renderer consume the same project document. Later visual tracks overlay earlier
ones; transforms share the preview's normalized defaults; stills loop for their authored duration;
text enters at its document layer; registry graphics fade and slide in/out on the same motion
contract as the preview; and timeline gaps remain black instead of collapsing.

Audible speech, video, and audio clips are positioned as independent stems. Gain is per clip and
``duckDb`` is deterministic attenuation during speech overlap, mirrored by the player volume
callback. Silent media contribute no stem, while a project-duration stereo silence bed guarantees a
playable 48 kHz AAC output. The shared H.264 ladder selects an available encoder. Final media always
resolves source references, never proxies.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import config
from pipeline import contracts
from pipeline import caption_burn
from pipeline import caption_pages
from pipeline import channels
from pipeline import edits
from pipeline import layout
from pipeline import graphics
from pipeline import project as project_mod
from pipeline import reframe
from pipeline import timebase
from pipeline.runtime_tools import font_filter_path
from pipeline.runtime_tools import tool_path
from pipeline.errors import ValidationError
from pipeline.encoder_ladder import EncoderDiscoveryError, NoH264Encoder, encoder_args, log_ffmpeg as _log_ffmpeg, run_h264, uses_portable_profiles

STAGE = "compile"


def _log_note(message: str) -> None:
    """A comment line in the run log, next to the commands it explains."""
    with open(config.RUN_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"# {message}\n")


def _err(clip_id: str, reason: str, details: str | None = None) -> contracts.StageResult:
    return contracts.StageResult(status="error", stage=STAGE, clip_id=clip_id, reason=reason, details=details)


# HLG/PQ → SDR BT.709 tonemap (shared verbatim with proxy._TONEMAP). Always
# ends format=yuv420p: h264_nvenc encodes 8-bit only and the compile path always resolves SOURCE,
# which may be 10-bit HLG phone footage. Requires an ffmpeg build with zimg.
_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
_HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _probe_transfer(path: Path) -> str:
    """The source's color_transfer (lowercased), or '' if ffprobe can't read it. One call per unique
    input source; HDR transfers get the tonemap chain. Monkeypatched in tests (no ffmpeg)."""
    try:
        out = subprocess.run(
            [tool_path("ffprobe"), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer",
             "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError:
        return ""
    return out.stdout.strip().lower()


def _probe_has_audio(path: Path) -> bool:
    """Whether ``path`` exposes an audio stream.

    A video imported into a visual track is allowed to be silent (and still images never have an
    audio pad).  Probe before constructing ``[n:a]`` references so one silent stock clip cannot
    invalidate the whole filter graph.  An unreadable source returns False here; ffmpeg will still
    surface the useful video-input error when that source is needed for the visual composition.
    """
    if path.suffix.lower() in _IMAGE_SUFFIXES:
        return False
    try:
        out = subprocess.run(
            [tool_path("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index",
             "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(out.stdout.strip())


# ── pure filtergraph helpers ──────────────────────────────────────────────────────

def _sec(frame: int, fps: int) -> str:
    """Frame → seconds, formatted for a trim boundary (6dp keeps single-frame accuracy at 30 fps)."""
    return f"{timebase.frame_to_sec(frame, fps):.6f}"


def _seam_afade(seams: list[dict] | None, dur_s: float) -> str:
    """A head seam (atF<=0) fades audio IN over fadeMs at the
    clip start; a tail seam fades OUT ending at the clip end. Comma-joined; '' when no seams."""
    parts: list[str] = []
    for s in seams or []:
        d = int(s["fadeMs"]) / 1000.0
        if int(s["atF"]) <= 0:
            parts.append(f"afade=t=in:st=0:d={d:.3f}")
        else:
            parts.append(f"afade=t=out:st={max(0.0, dur_s - d):.3f}:d={d:.3f}")
    return ",".join(parts)


def _db(value: float) -> str:
    """Format a validated dB gain as a fixed-precision literal (defense-in-depth: never interpolate
    a raw doc value into the filtergraph — validate() already bounds it to [-60, 12], this coerces to
    float and formats so a stray metacharacter can't ride into `volume=`)."""
    return f"{float(value):.3f}"


def _is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _IMAGE_SUFFIXES


def _input_args(path: str, fps: int) -> list[str]:
    """Input argv with demuxer-appropriate looping for authored still/image durations."""
    suffix = Path(path).suffix.lower()
    if suffix == ".gif":
        return ["-stream_loop", "-1", "-i", path]
    if suffix in _IMAGE_SUFFIXES:
        return ["-loop", "1", "-framerate", str(fps), "-i", path]
    return ["-i", path]


def _resolved_transform(proj: project_mod.Project, clip: project_mod.Clip, is_image: bool,
                        is_speech: bool) -> dict:
    """Mirror the browser's ``resolvedTransform`` defaults exactly.

    An omitted transform remains a reset/default: images contain, cropped projects cover, speech
    footage contains and other video covers, so a source whose shape differs from the frame (a
    rotated phone recording) is never stretched. Values are already schema-validated.
    """
    if clip.transform is not None:
        return clip.transform
    return {
        "x": 0.5,
        "y": 0.5,
        "scale": 1.0,
        "fit": "contain" if is_image else "cover" if proj.crop else "contain" if is_speech else "cover",
    }


def _visual_box_filters(transform: dict, width: int, height: int) -> tuple[list[str], str, str]:
    """Return scale/crop filters and overlay coordinates for one normalized visual transform."""
    box_w = max(1, round(width * float(transform["scale"])))
    box_h = max(1, round(height * float(transform["scale"])))
    fit = transform["fit"]
    if fit == "fill":
        filters = [f"scale={box_w}:{box_h}"]
    elif fit == "contain":
        filters = [f"scale={box_w}:{box_h}:force_original_aspect_ratio=decrease"]
    else:  # cover: scale past the box, then center-crop exactly as CSS object-position: 50% 50%.
        filters = [f"scale={box_w}:{box_h}:force_original_aspect_ratio=increase",
                   f"crop={box_w}:{box_h}:(iw-ow)/2:(ih-oh)/2"]
    x = width * float(transform["x"])
    y = height * float(transform["y"])
    return filters, f"{x:.3f}-w/2", f"{y:.3f}-h/2"


def _merged_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge frame intervals so static ducking emits one gain filter per real overlap."""
    out: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _safe_stem(name: str) -> str:
    """A filtergraph-safe stem for an overlay textfile name. The textfile= value is embedded UNQUOTED,
    so the on-disk path must carry no comma/semicolon/quote; strip everything but the slug charset."""
    from pipeline.names import project_name, reserved_name, validate_existing_name

    if reserved_name(name):
        raise ValidationError("name cannot be a reserved device name")
    stem = "".join(ch for ch in name if ch.isascii() and (ch.isalnum() or ch in "-_")) or "proj"
    return project_name(stem) or validate_existing_name(stem)


def _split_labels(kind: str, idx: int, count: int) -> tuple[list[str], list[str]]:
    """Deduped inputs (index map by source) mean one input pad may feed several clips, but an ffmpeg
    filter pad is consumable ONCE — so a source used N>1 times is split/asplit into N labels first.
    Returns (prelude_chains, labels); count<=1 uses the bare [idx:kind] pad with no split node."""
    if count <= 1:
        return [], [f"[{idx}:{kind}]"]
    labels = [f"[{kind}{idx}_{j}]" for j in range(count)]
    op = "split" if kind == "v" else "asplit"
    return [f"[{idx}:{kind}]{op}={count}" + "".join(labels)], labels


def _drawtext(item: project_mod.TextItem, width: int, height: int, fps: int, name: str, idx: int,
              overlays_dir: Path, runtime_root: Path) -> str:
    """One drawtext filter for a text item, placed via the shared layout math so the burned
    caption sits where the player preview drew it. Windows escaping: the fontfile drive colon is escaped AND single-quoted; textfile is a project-relative POSIX path
    (sidesteps drive-colon escaping); the comma-bearing enable/x/y expressions are single-quoted so
    the filtergraph splitter doesn't break on them. The textfile is named by an ENUMERATED index
    (text0.txt, text1.txt …), NEVER by item.id — a doc-controlled id must never reach the on-disk path
    or the unquoted textfile= token."""
    box = layout.resolve_layout({"preset": item.preset, "pos": item.pos, "scale": item.scale}, width, height)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    textfile = overlays_dir / f"{_safe_stem(name)}_text{idx}.txt"
    textfile.write_text(item.text, encoding="utf-8")
    text_rel = textfile.relative_to(runtime_root).as_posix()
    x, y, fs = box["x_px"], box["y_px"], box["font_px"]
    k = layout.design_scale(width, height)
    boxborderw = max(round(6 * k), fs // 4)
    t0 = timebase.frame_to_sec(item.tlStartF, fps)
    t1 = timebase.frame_to_sec(item.tlStartF + item.durF, fps)
    return (
        f"drawtext=expansion=none:textfile={text_rel}:"
        f"fontfile={font_filter_path('bold')}:"
        f"fontsize={fs}:fontcolor=white:borderw={round(2 * k)}:bordercolor=black:"
        f"box=1:boxcolor=black@0.45:boxborderw={boxborderw}:"
        f"x='({x})-text_w/2':y='({y})-text_h/2':"
        f"enable='between(t,{t0:.3f},{t1:.3f})'"
    )


def _graphic_layer(item: project_mod.GraphicItem, width: int, height: int, fps: int, name: str, idx: int,
                   overlays_dir: Path, runtime_root: Path, lead_f: int, span_f: int) -> str:
    """Draw the declarative first-party layer contract (the same absolute pixel geometry the player
    preview uses) onto a transparent full-canvas layer, fade it in/out with the registry motion, and
    place it on the timeline clock. `span_f` is the item's authored duration and `lead_f` how much of
    it a preview window trimmed off the head, so a windowed segment keeps the full render's timing."""
    geometry = graphics.graphic_geometry(item.componentId, item.props, width, height, item.pos, item.scale)
    style = geometry["style"]
    overlays_dir.mkdir(parents=True, exist_ok=True)
    filters = [f"color=c=black@0:s={width}x{height}:r={fps}:d={_sec(span_f, fps)}", "format=rgba"]
    props = item.props
    box = geometry["box"]

    def text(layer_idx: int, layer: dict, value: str, weight: str) -> str:
        textfile = overlays_dir / f"{_safe_stem(name)}_graphic{idx}_{layer_idx}.txt"
        textfile.write_text(graphics.fit_graphic_text(value, layer["maxChars"]), encoding="utf-8")
        text_rel = textfile.relative_to(runtime_root).as_posix()
        fontsize = layer["fontPx"]
        line_spacing = max(0, layer["lineHeightPx"] - fontsize)
        return (f"drawtext=expansion=none:textfile={text_rel}:fontfile={font_filter_path(weight)}:fontsize={fontsize}:"
                f"line_spacing={line_spacing}:x={layer['x']}:y={layer['y']}:fontcolor=0x{style['foreground'][1:]}")

    for layer_idx, layer in enumerate(geometry["layers"]):
        kind = layer["kind"]
        lx, ly, lw, lh = layer["x"], layer["y"], layer["width"], layer["height"]
        if kind == "panel":
            filters.append(f"drawbox=x={box['x']}:y={box['y']}:w={box['width']}:h={box['height']}:color=0x{style['background'][1:]}@{style['backgroundAlpha']}:t=fill:replace=1")
            filters.append(f"drawbox=x={box['x']}:y={box['y']}:w={box['width']}:h={box['height']}:color=0x{style['border'][1:]}@1:t={style['borderPx']}:replace=1")
        elif kind == "rail":
            filters.append(f"drawbox=x={lx}:y={ly}:w={lw}:h={lh}:color=0x{style['border'][1:]}@1:t=fill:replace=1")
        elif kind == "text":
            value = props[layer["field"]]
            if value:
                filters.append(text(layer_idx, layer, str(value), "bold"))
        elif kind == "bars":
            baseline = layer["baseline"]
            filters.append(f"drawbox=x={baseline['x']}:y={baseline['y']}:w={baseline['width']}:h={baseline['height']}:color=0x{style['foreground'][1:]}@1:t=fill:replace=1")
            for bar in layer.get("bars", []):
                filters.append(f"drawbox=x={bar['x']}:y={bar['y']}:w={bar['width']}:h={bar['height']}:color=0x{style['foreground'][1:]}@1:t=fill:replace=1")
        elif kind == "labels":
            filters.append(text(layer_idx, layer, " · ".join(str(label) for label in props["labels"]), "regular"))
    motion = graphics.graphic_motion(span_f, height)
    # Time-based fades give frame k of the span alpha k/inF and (span-k)/outF exactly, the same
    # factors graphicMotionAt() applies in the preview.
    if motion["inF"]:
        filters.append(f"fade=t=in:st=0:d={_sec(motion['inF'], fps)}:alpha=1")
    if motion["outF"]:
        filters.append(f"fade=t=out:st={_sec(span_f - motion['outF'], fps)}:d={_sec(motion['outF'], fps)}:alpha=1")
    # The layer's 1/fps timebase would truncate a 6-dp start (0.333333 s -> frame 9), so round it.
    filters += [f"trim=start_frame={lead_f}:end_frame={lead_f + item.durF}",
                f"setpts=round(PTS-STARTPTS+{_sec(item.tlStartF, fps)}/TB)"]
    return ",".join(filters)


def _graphic_overlay(item: project_mod.GraphicItem, height: int, fps: int, lead_f: int, span_f: int) -> str:
    """Overlay a graphic layer with the registry slide: it rises `slidePx` into place over the entrance
    (ease-out) and sinks back over the exit (ease-in), keyed on the authored span's own clock."""
    motion = graphics.graphic_motion(span_f, height)
    start_f = item.tlStartF - lead_f
    terms = []
    if motion["inF"]:
        terms.append(f"pow(1-clip((t-{_sec(start_f, fps)})/{_sec(motion['inF'], fps)},0,1),2)")
    if motion["outF"]:
        exit_f = start_f + span_f - motion["outF"]
        terms.append(f"pow(clip((t-{_sec(exit_f, fps)})/{_sec(motion['outF'], fps)},0,1),2)")
    y = f"{motion['slidePx']}*({'+'.join(terms)})" if terms and motion["slidePx"] else "0"
    return f"overlay=x=0:y='{y}':eof_action=pass:repeatlast=0"


def _center_crop_filter(width: int, height: int) -> str:
    """A static center crop to the target aspect: keep the largest
    centered rect of the target ratio, then `scale` fills the 9:16 canvas. Pure expression — always
    available without reframe metadata. `ow`/`oh` are ffmpeg's crop output dims."""
    return (f"crop=w='min(iw,ih*{width}/{height})':h='min(ih,iw*{height}/{width})':"
            "x='(iw-ow)/2':y='(ih-oh)/2'")


def _reframe_crop_filter(proj_crop: dict, src_in: int, src_out: int, width: int, height: int,
                         source_path: str, project_dir: Path) -> "str | None":
    """Resolve a `crop.mode == "reframe"` descriptor to a CONCRETE `crop=cw:ch:cx:cy` (source px) for
    this segment's span, or None to fall back to the center crop: the reframe.json is
    absent/invalid, or no per-frame rect covers [src_in, src_out). `metaPath` (project-relative) points
    at a specific sidecar; otherwise the source stem's REFRAME_DIR sidecar is used."""
    meta_path = proj_crop.get("metaPath")
    meta = (
        reframe.load_metadata(Path(project_dir) / meta_path)
        if meta_path
        else reframe.load_metadata(
            Path(project_dir) / "outputs" / "reframe" / f"{Path(source_path).stem}.reframe.json"
        )
    )
    if meta is None:
        return None
    rect = reframe.span_crop_rect([asdict(fc) for fc in meta.frame_crops],
                                  src_in, src_out, width, height)
    if rect is None:
        return None
    return f"crop={rect['crop_w']}:{rect['crop_h']}:{rect['crop_x']}:{rect['crop_y']}"


def _caption_words(speech: list[project_mod.Clip], fps: int, emphasis: list[int],
                   project_dir: "Path | None" = None) -> "tuple[list[dict], frozenset[int]]":
    """Window the shared word clock to the render span for the karaoke burn. Each speech clip maps
    a SOURCE span to a TIMELINE position, so a word at source-second `ws` in clip `c` lands at timeline
    `tlStart + (ws - srcIn)` rebases source words across the whole speech
    spine and shifted by each clip's timeline start (so a windowed/rippled build stays aligned). Returns
    the clip-relative word list plus the CLIP-RELATIVE positions of the emphasised flat wordIdx.
    Emphasis indices refer to the source's flattened segments→words order."""
    emph_flat = {int(i) for i in (emphasis or [])}
    words: list[dict] = []
    emph_pos: set[int] = set()
    for c in speech:
        transcript_dir = (
            Path(project_dir) / "outputs" / "transcripts"
            if project_dir is not None else config.TRANSCRIPTS_DIR
        )
        wjson = transcript_dir / f"{Path(c.media.source).stem}.words.json"
        if not wjson.exists():
            continue
        try:
            raw = json.loads(wjson.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        src_in = timebase.frame_to_sec(c.srcInF, fps)
        src_out = timebase.frame_to_sec(c.srcOutF, fps)
        tl0 = timebase.frame_to_sec(c.tlStartF, fps)
        flat = 0
        for seg in raw.get("segments", []):
            for w in seg.get("words", []):
                ws, we = w.get("start"), w.get("end")
                if ws is not None and we is not None and ws >= src_in - 1e-6 and we <= src_out + 1e-6:
                    if flat in emph_flat:
                        emph_pos.add(len(words))
                    words.append({"word": (w.get("word") or "").strip(),
                                  "start": ws - src_in + tl0, "end": we - src_in + tl0})
                flat += 1
    return words, frozenset(emph_pos)


@dataclass(frozen=True)
class Loudness:
    """The mixed audio as loudnorm measures it, which the linear second pass turns into one gain."""
    integrated: float   # LUFS
    true_peak: float    # dBTP
    lra: float          # LU
    threshold: float    # LUFS
    offset: float       # LU, loudnorm's suggested target offset


# loudnorm refuses to start when a measured_* or offset value lies outside these bounds, so a
# measurement outside them (digital silence reports -inf) is treated as no measurement at all.
_LOUDNORM_BOUNDS = {
    "input_i": (-99.0, 0.0), "input_tp": (-99.0, 99.0), "input_lra": (0.0, 99.0),
    "input_thresh": (-99.0, 0.0), "target_offset": (-99.0, 99.0),
}
_LOUDNORM_MAX_LRA = 50.0
_LOUDNORM_JSON = re.compile(r'\{[^{}]*"input_i"[^{}]*\}')
_NORMALIZATION_TYPE = re.compile(r"Normalization Type:\s*(\w+)")


def _loudnorm_targets(lra: float) -> str:
    return f"I={config.LOUDNORM_I:g}:TP={config.LOUDNORM_TP:g}:LRA={lra:g}"


def loudnorm_measure_filter() -> str:
    """Analysis stage: loudnorm reports what it measured as a JSON block on stderr."""
    return f"loudnorm={_loudnorm_targets(config.LOUDNORM_LRA)}:print_format=json"


def loudnorm_filter(measured: Loudness | None = None) -> str:
    """The mix's closing loudness stage.

    Fed a measurement it runs in linear mode: one constant gain, so the mix keeps its shape.
    Without one it is loudnorm's single-pass dynamic mode, an AGC that re-levels the whole mix.
    """
    if measured is None:
        return f"loudnorm={_loudnorm_targets(config.LOUDNORM_LRA)}"
    # ffmpeg only stays linear while the measured range fits the LRA target (the target has no other
    # effect in linear mode), so a mix with wide dynamics raises the target to its own range.
    lra = min(_LOUDNORM_MAX_LRA, max(config.LOUDNORM_LRA, round(measured.lra, 2)))
    return (f"loudnorm={_loudnorm_targets(lra)}:measured_I={measured.integrated:.2f}"
            f":measured_LRA={measured.lra:.2f}:measured_TP={measured.true_peak:.2f}"
            f":measured_thresh={measured.threshold:.2f}:offset={measured.offset:.2f}"
            ":linear=true:print_format=summary")


def parse_loudness(stderr: str) -> Loudness | None:
    """The measurement in ffmpeg's stderr: the last loudnorm JSON block among its log lines. None
    when there is none, it is malformed, or a value is one loudnorm would not accept back."""
    blocks = _LOUDNORM_JSON.findall(stderr)
    if not blocks:
        return None
    try:
        raw = json.loads(blocks[-1])
        values = {key: float(raw[key]) for key in _LOUDNORM_BOUNDS}
    except (ValueError, KeyError, TypeError):
        return None
    if not all(low <= values[key] <= high for key, (low, high) in _LOUDNORM_BOUNDS.items()):
        return None
    return Loudness(integrated=values["input_i"], true_peak=values["input_tp"], lra=values["input_lra"],
                    threshold=values["input_thresh"], offset=values["target_offset"])


@dataclass
class CompilePlan:
    """One export as ffmpeg inputs plus a filtergraph in three parts: video chains, audio chains up
    to the stems and silence bed, and the closing mix. The audio parts stand alone, so the mix can
    be measured without rendering any video, and ``loudness`` decides how the mix is normalised."""
    inputs: list[str]                              # flat ["-i", src0, "-i", src1, ...] (deduped)
    video_graph: str
    audio_graph: str
    audio_mix: str = ""                            # amix over the bed and stems, unlabelled; "" when no stem
    loudness: Loudness | None = None               # None: single-pass dynamic loudnorm
    maps: list[str] = field(default_factory=lambda: ["-map", "[vout]", "-map", "[aout]"])

    @property
    def filter_complex(self) -> str:
        if self.audio_mix:
            closing = f"{self.audio_mix},{loudnorm_filter(self.loudness)},aresample=48000[aout]"
        else:
            closing = "[asilence]aresample=48000[aout]"
        return ";".join([self.video_graph, self.audio_graph, closing])


@dataclass(frozen=True)
class SceneLayer:
    """A rendered animated scene (pipeline.scene_render) shown over timeline frames [startF, startF+durF).
    An opaque layer (animation) covers the frame; an alpha layer (cameo) keeps its transparency."""
    path: str
    startF: int
    durF: int
    alpha: bool


def _scene_overlays(chains: list[str], current_v: str, scenes: Sequence[SceneLayer], first_input: int,
                    fps: int) -> str:
    """Overlay each rendered scene at its span. Its clock is shifted to the scene start (rounded, like a
    graphic layer's) and `enable` is bounded half a frame either side so float rounding can neither drop
    the first frame nor leak past the last. Returns the new head of the video chain."""
    for j, scene in enumerate(scenes):
        label = f"[scene{j}]"
        pix = "yuva420p" if scene.alpha else "yuv420p"
        chains.append(f"[{first_input + j}:v]format={pix},"
                      f"setpts=round(PTS-STARTPTS+{_sec(scene.startF, fps)}/TB){label}")
        t0 = (scene.startF - 0.5) / fps
        t1 = (scene.startF + scene.durF - 0.5) / fps
        next_v = f"[vbase{len(chains)}]"
        chains.append(f"{current_v}{label}overlay=x=0:y=0:enable='between(t,{t0:.6f},{t1:.6f})':"
                      f"eof_action=pass:repeatlast=0{next_v}")
        current_v = next_v
    return current_v


def _window_clip(clip: project_mod.Clip, start_f: int, end_f: int) -> "project_mod.Clip | None":
    """Clamp a clip's timeline span to [start_f, end_f) and rebase tlStartF to the window origin.
    A clip's timeline frame f maps to source `srcInF + (f - tlStartF)`, so clamping the timeline
    overlap re-derives the source in/out; a non-overlapping clip returns None (dropped)."""
    span = clip.srcOutF - clip.srcInF
    tl0, tl1 = clip.tlStartF, clip.tlStartF + span
    ov0, ov1 = max(tl0, start_f), min(tl1, end_f)
    if ov1 <= ov0:
        return None
    return replace(
        clip,
        srcInF=clip.srcInF + (ov0 - tl0),
        srcOutF=clip.srcInF + (ov1 - tl0),
        tlStartF=ov0 - start_f,
    )


def _window_item(item: project_mod.TextItem | project_mod.GraphicItem, start_f: int, end_f: int) -> "project_mod.TextItem | project_mod.GraphicItem | None":
    t0, t1 = item.tlStartF, item.tlStartF + item.durF
    ov0, ov1 = max(t0, start_f), min(t1, end_f)
    if ov1 <= ov0:
        return None
    return replace(item, tlStartF=ov0 - start_f, durF=ov1 - ov0)


def _apply_window(proj: project_mod.Project, window: tuple[int, int]) -> project_mod.Project:
    """Rebuild every track keeping only the portion overlapping the window, rebased so the segment
    starts at t=0. Reuses the whole downstream filtergraph unchanged — a judge/choose preview is just
    the full compiler over a bounded slice of the timeline (WYSIWYG)."""
    start_f, end_f = window
    tracks: list[project_mod.Track] = []
    for t in proj.tracks:
        clips = [w for c in t.clips if (w := _window_clip(c, start_f, end_f)) is not None]
        items = [w for i in t.items if (w := _window_item(i, start_f, end_f)) is not None]
        tracks.append(replace(t, clips=clips, items=items))
    return replace(proj, tracks=tracks)


def build_plan(doc: dict, *, project_dir: "Path | None" = None,
               window: "tuple[int, int] | None" = None,
               scenes: Sequence[SceneLayer] = ()) -> CompilePlan:
    """Build one clocked FFmpeg composition matching the player preview.

    Visual tracks are overlaid in document order on a black project-duration canvas. Every clip keeps
    its absolute timeline position, optional transform, and source trim; still images are looped by
    the input builder. Speech, audible video, and audio-track clips become independent gain-staged
    stems. ``duckDb`` applies deterministic attenuation only while that non-speech stem overlaps a
    speech clip. A full-duration silent stem provides explicit fallbacks for gaps and silent media,
    and the final mix is normalized once at 48 kHz: linear loudnorm when the plan carries a
    ``Loudness`` measurement (`measure_loudness`), single-pass dynamic loudnorm otherwise.

    The project duration is `edits.timeline_end_f`, the preview's rule: a word-anchored cue never
    lengthens the video, so every element is first trimmed to that end.

    Rendered animated `scenes` (full-timeline spans, so never with a `window`) go right after the last
    speech/video track: above footage and B-roll, below text, graphics and captions, as the preview
    stacks them.
    """
    isolated = project_dir is not None
    runtime_root = Path(project_dir) if project_dir is not None else config.STATE_HOME
    overlays_dir = runtime_root / "outputs" / "overlays" if isolated else config.OVERLAYS_DIR
    captions_dir = runtime_root / "outputs" / "captions" if isolated else config.CAPTIONS_DIR
    proj = project_mod.from_dict(doc)
    project_mod.validate(proj)
    window_duration_f: int | None = None
    authored_graphics = {item.id: item for track in proj.tracks for item in track.items
                         if isinstance(item, project_mod.GraphicItem)}
    proj = _apply_window(proj, (0, edits.timeline_end_f(proj)))
    if window is not None:
        window_duration_f = window[1] - window[0]
        proj = _apply_window(proj, window)
    width, height, fps = proj.width, proj.height, proj.fps
    visual_clips = [(track.kind, clip) for track in proj.tracks
                    if track.kind in {"speech", "video"} for clip in track.clips]
    text_items = [item for track in proj.tracks if track.kind == "text" for item in track.items]
    audio_clips = [clip for track in proj.tracks if track.kind == "audio" for clip in track.clips]
    if not visual_clips and not text_items and not audio_clips and window_duration_f is None:
        raise ValueError("project has no timeline content to compile")

    def end_f(clip: project_mod.Clip) -> int:
        return clip.tlStartF + clip.srcOutF - clip.srcInF

    render_candidates = ([end_f(clip) for _kind, clip in visual_clips]
                         + [item.tlStartF + item.durF for item in text_items]
                         + [end_f(clip) for clip in audio_clips])
    render_end_f = window_duration_f if window_duration_f is not None else max(render_candidates)
    duration_s = _sec(render_end_f, fps)

    inputs_paths: list[str] = []
    index_by_source: dict[str, int] = {}
    idx_of: dict[int, int] = {}
    all_media_clips = [clip for _kind, clip in visual_clips] + audio_clips
    for clip in all_media_clips:
        source = str(project_mod.resolve_media(runtime_root, clip.media, prefer_proxy=False))
        if source not in index_by_source:
            index_by_source[source] = len(inputs_paths)
            inputs_paths.append(source)
        idx_of[id(clip)] = index_by_source[source]

    has_audio = {idx: _probe_has_audio(Path(path)) for idx, path in enumerate(inputs_paths)}
    audio_stems: list[tuple[str, project_mod.Clip, int]] = []
    for kind, clip in visual_clips:
        idx = idx_of[id(clip)]
        if has_audio[idx]:
            audio_stems.append((kind, clip, idx))
    for track in proj.tracks:
        if track.kind == "audio":
            for clip in track.clips:
                idx = idx_of[id(clip)]
                if has_audio[idx]:
                    audio_stems.append(("audio", clip, idx))

    vcount: dict[int, int] = {}
    for _kind, clip in visual_clips:
        idx = idx_of[id(clip)]
        vcount[idx] = vcount.get(idx, 0) + 1
    acount: dict[int, int] = {}
    for _kind, _clip, idx in audio_stems:
        acount[idx] = acount.get(idx, 0) + 1
    vprelude: list[str] = []
    audio_chains: list[str] = []
    vq: dict[int, deque[str]] = {}
    aq: dict[int, deque[str]] = {}
    for idx in range(len(inputs_paths)):
        vch, vlabels = _split_labels("v", idx, vcount.get(idx, 0))
        ach, alabels = _split_labels("a", idx, acount.get(idx, 0))
        vprelude += vch
        audio_chains += ach
        vq[idx] = deque(vlabels)
        aq[idx] = deque(alabels)

    hdr_by_idx = {idx: (not _is_image(path) and _probe_transfer(Path(path)) in _HDR_TRANSFERS)
                  for idx, path in enumerate(inputs_paths)}
    chains: list[str] = [*vprelude,
                         f"color=c=black:s={width}x{height}:r={fps}:d={duration_s}[vbase0]"]
    current_v = "[vbase0]"
    text_index = 0
    last_visual = max((i for i, t in enumerate(proj.tracks) if t.kind in {"speech", "video"}), default=-1)
    # Track iteration, rather than a flattened timeline sort, is the visual z-order contract:
    # later visual tracks paint above earlier ones, exactly like ProjectComposition.
    for track_idx, track in enumerate(proj.tracks):
        if track.kind in {"speech", "video"}:
            for clip in track.clips:
                idx = idx_of[id(clip)]
                source_path = inputs_paths[idx]
                vparts = [
                    f"{vq[idx].popleft()}trim=start={_sec(clip.srcInF, fps)}:end={_sec(clip.srcOutF, fps)}",
                    f"setpts=PTS-STARTPTS+{_sec(clip.tlStartF, fps)}/TB",
                ]
                if hdr_by_idx[idx]:
                    vparts.append(_TONEMAP)
                if proj.crop is not None and clip.transform is None and not _is_image(source_path):
                    crop_filter = None
                    if proj.crop.get("mode") == "reframe":
                        crop_filter = _reframe_crop_filter(proj.crop, clip.srcInF, clip.srcOutF,
                                                           width, height, source_path, runtime_root)
                    vparts.append(crop_filter or _center_crop_filter(width, height))
                placement, x, y = _visual_box_filters(
                    _resolved_transform(proj, clip, _is_image(source_path), track.kind == "speech"), width, height
                )
                vparts += [*placement, "setsar=1", f"fps={fps}"]
                clip_label = f"[vclip{clip.id}]"
                chains.append(",".join(vparts) + clip_label)
                next_v = f"[vbase{len(chains)}]"
                chains.append(f"{current_v}{clip_label}overlay=x='{x}':y='{y}':eof_action=pass:repeatlast=0{next_v}")
                current_v = next_v
        elif track.kind == "text":
            for item in track.items:
                if isinstance(item, project_mod.GraphicItem):
                    authored = authored_graphics.get(item.id, item)
                    lead_f = item.tlStartF + (window[0] if window else 0) - authored.tlStartF
                    layer = f"[gfx{text_index}]"
                    chains.append(_graphic_layer(item, width, height, fps, proj.name, text_index, overlays_dir,
                                                 runtime_root, lead_f, authored.durF) + layer)
                    next_v = f"[vbase{len(chains)}]"
                    chains.append(f"{current_v}{layer}{_graphic_overlay(item, height, fps, lead_f, authored.durF)}{next_v}")
                else:
                    next_v = f"[vbase{len(chains)}]"
                    chains.append(f"{current_v}{_drawtext(item, width, height, fps, proj.name, text_index, overlays_dir, runtime_root)}{next_v}")
                current_v = next_v
                text_index += 1
        if track_idx == last_visual:
            current_v = _scene_overlays(chains, current_v, scenes, len(inputs_paths), fps)

    caption_filter = ""
    speech_clips = [clip for kind, clip in visual_clips if kind == "speech"]
    if proj.captions is not None and proj.captions.get("enabled"):
        style = channels.resolve_caption_style(proj.channel)
        override = proj.captions.get("style")
        if isinstance(override, dict):
            style = {**style, **override}
        if style.get("look") == "outline":
            cwords, emphasis_idx = _caption_words(
                speech_clips, fps, proj.captions.get("emphasis") or [],
                runtime_root if isolated else None,
            )
            if cwords:
                ass_rel = caption_burn.write_karaoke_ass(
                    proj.name,
                    cwords,
                    width,
                    height,
                    style=style,
                    emphasis_idx=emphasis_idx,
                    output_dir=captions_dir,
                    runtime_root=runtime_root,
                )
                caption_filter = f"ass={ass_rel}:fontsdir={font_filter_path('bold', role='sans', directory=True)}"
        else:
            transcripts_dir = runtime_root / "outputs" / "transcripts" if isolated else config.TRANSCRIPTS_DIR
            pages = caption_pages.caption_pages(
                proj, caption_pages.transcript_words(proj, transcripts_dir), width, height)
            if pages:
                ass_rel = caption_burn.write_pill_ass(proj.name, pages, width, height,
                                                      output_dir=captions_dir, runtime_root=runtime_root)
                caption_filter = f"ass={ass_rel}:fontsdir={font_filter_path('bold', role='sans', directory=True)}"
    chains.append(f"{current_v}{caption_filter or 'null'}[vout]")

    # Speech-track placement is the shared semantic sidechain clock. Use its authored spans even if
    # a particular source probes silent, matching previewVolume() and keeping fallback deterministic.
    dialogue = _merged_intervals([(clip.tlStartF, end_f(clip)) for clip in speech_clips])
    audio_labels: list[str] = []
    for stem_idx, (kind, clip, input_idx) in enumerate(audio_stems):
        duration = timebase.frame_to_sec(clip.srcOutF - clip.srcInF, fps)
        parts = [
            f"{aq[input_idx].popleft()}atrim=start={_sec(clip.srcInF, fps)}:end={_sec(clip.srcOutF, fps)}",
            "asetpts=PTS-STARTPTS",
        ]
        if clip.gainDb:
            parts.append(f"volume={_db(clip.gainDb)}dB")
        seam = _seam_afade(clip.seams, duration)
        if seam:
            parts.append(seam)
        if kind != "speech" and clip.duckDb is not None:
            overlaps = _merged_intervals([
                (max(clip.tlStartF, start), min(end_f(clip), end)) for start, end in dialogue
            ])
            reduction = _db(float(clip.duckDb))
            for start, end in overlaps:
                local_start = start - clip.tlStartF
                local_end = end - clip.tlStartF
                parts.append(
                    f"volume={reduction}dB:enable='between(t,{_sec(local_start, fps)},"
                    f"{_sec(local_end, fps)})'"
                )
        # amix ignores timestamps, so the stem's offset has to be real leading silence.
        parts += ["aformat=sample_rates=48000:channel_layouts=stereo", "aresample=async=1",
                  f"asetpts=PTS+{_sec(clip.tlStartF, fps)}/TB", "aresample=async=1:first_pts=0"]
        label = f"[astem{stem_idx}]"
        audio_chains.append(",".join(parts) + label)
        audio_labels.append(label)

    silence = "[asilence]"
    audio_chains.append(f"anullsrc=channel_layout=stereo:sample_rate=48000:d={duration_s}{silence}")
    audio_mix = (f"{silence}{''.join(audio_labels)}amix=inputs={len(audio_labels) + 1}:duration=first:normalize=0"
                 if audio_labels else "")

    inputs: list[str] = []
    for path in inputs_paths:
        inputs += _input_args(path, fps)
    for scene in scenes:
        inputs += ["-i", scene.path]
    return CompilePlan(inputs=inputs, video_graph=";".join(chains), audio_graph=";".join(audio_chains),
                       audio_mix=audio_mix)


def build_encode_cmd(plan: CompilePlan, dest: Path, *, encoder: str = "h264_nvenc", portable: bool = False) -> list[str]:
    """The final-render profile with the same pixel, audio and container contract on every rung."""
    vcodec = encoder_args("export", encoder, {"crf": str(config.ENCODE_CRF), "preset": config.ENCODE_PRESET})
    return [
        tool_path("ffmpeg"), "-y",
        *plan.inputs,
        "-filter_complex", plan.filter_complex,
        *plan.maps,
        *vcodec,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", *(["-ar", "48000"] if portable else []),
        "-movflags", "+faststart",
        str(dest),
    ]


def build_measure_cmd(plan: CompilePlan) -> list[str]:
    """The loudness measurement: the plan's audio alone, mixed exactly as the export mixes it, through
    loudnorm's analysis to a null sink. No video chain is in the graph, so nothing is rendered."""
    graph = f"{plan.audio_graph};{plan.audio_mix},{loudnorm_measure_filter()}[amix]"
    return [tool_path("ffmpeg"), "-hide_banner", "-nostats", *plan.inputs,
            "-filter_complex", graph, "-map", "[amix]", "-f", "null", "-"]


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else "no output"


def measure_loudness(plan: CompilePlan, *, cwd: Path) -> Loudness | None:
    """Run the measurement pass over the plan's mixed audio. None when there is nothing to measure or
    the measurement fails; the reason goes to run.log and the plan keeps its single-pass dynamic stage."""
    if not plan.audio_mix:
        return None
    cmd = build_measure_cmd(plan)
    _log_ffmpeg(cmd)
    try:
        done = subprocess.run(cmd, check=True, capture_output=True, encoding="utf-8", errors="replace", cwd=cwd)
    except (OSError, subprocess.CalledProcessError) as e:
        reason = _last_line(getattr(e, "stderr", None) or str(e))
    else:
        measured = parse_loudness(done.stderr or "")
        if measured is not None:
            return measured
        reason = "no usable measurement in the output (silent mix?)"
    _log_note(f"loudness measurement failed ({reason}); falling back to single-pass dynamic loudnorm")
    return None


def _log_normalization(stderr: str | None) -> None:
    """Record whether the second pass ran linear. ffmpeg quietly reverts to dynamic when one constant
    gain cannot meet the true-peak target, and the summary on stderr is the only place it says so."""
    found = _NORMALIZATION_TYPE.findall(stderr or "")
    if not found:
        return
    if found[-1].lower() == "linear":
        _log_note("loudnorm second pass ran linear: one constant gain")
    else:
        _log_note("loudnorm second pass ran dynamic: ffmpeg reverted because one constant gain "
                  "cannot meet the true-peak target")


def _run_encode(plan: CompilePlan, dest: Path, name: str, *, project_dir: Path) -> contracts.StageResult:
    """Use the ladder for full exports and previews; only device/init failures permit a retry."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg = tool_path("ffmpeg")
        portable = uses_portable_profiles(ffmpeg)
        done, encoder = run_h264(
            ffmpeg, "export", lambda name: build_encode_cmd(plan, dest, encoder=name, portable=portable), cwd=project_dir,
        )
    except (EncoderDiscoveryError, NoH264Encoder) as e:
        return _err(name, str(e))
    except subprocess.CalledProcessError as e:
        return _err(name, "ffmpeg compile failed", e.stderr)
    _log_normalization(done.stderr)

    return contracts.StageResult(
        status="ok", stage=STAGE, clip_id=name, output_path=str(dest),
        metrics={"path": "compile", "inputs": plan.inputs.count("-i"), "encoder": encoder},
    )


def compile_project(
    doc: dict,
    dest: Path,
    *,
    project_dir: "Path | None" = None,
    output_root: "Path | None" = None,
    scenes: Sequence[SceneLayer] = (),
) -> contracts.StageResult:
    """Build the plan, measure its mixed audio, and encode with the shared H.264 ladder.
    The measurement lets the encode normalise loudness with one constant gain; if it fails the
    encode keeps the single-pass dynamic stage.

    Path safety: dest must stay under FINAL_DIR (ffmpeg writes -y). Every ffmpeg command is logged to
    run.log before it runs. A non-init encode error surfaces unchanged (no needless fallback)."""
    dest = Path(dest)
    name = str(doc.get("name") or dest.stem)
    final_root = (Path(output_root) if output_root is not None else config.FINAL_DIR).resolve()
    if final_root not in dest.resolve().parents:
        return _err(name, f"refusing out-of-tree compile destination: {dest}")

    try:
        plan = build_plan(doc, project_dir=project_dir, scenes=scenes)
    except (ValueError, KeyError, ValidationError) as e:
        return _err(name, "failed to build compile plan", str(e))

    root = Path(project_dir) if project_dir is not None else config.STATE_HOME
    plan = replace(plan, loudness=measure_loudness(plan, cwd=root))
    return _run_encode(plan, dest, name, project_dir=root)


def compile_preview_segment(
    doc: dict,
    start_f: int,
    end_f: int,
    dest: Path,
    *,
    project_dir: "Path | None" = None,
    output_root: "Path | None" = None,
) -> contracts.StageResult:
    """Windowed compile for a judge/choose preview: the SAME filtergraph over the timeline slice
    [start_f, end_f), rebased to t=0 and NVENC-encoded. A few-second 1080p window is fast enough to run
    inline (no detached job). Path safety mirrors compile_project but permits PREVIEW_DIR (not just
    FINAL_DIR) so a taste-decision preview never lands in the final namespace."""
    dest = Path(dest)
    name = str(doc.get("name") or dest.stem)
    allowed = (
        (Path(output_root).resolve(),)
        if output_root is not None
        else (config.PREVIEW_DIR.resolve(), config.FINAL_DIR.resolve())
    )
    if not any(root in dest.resolve().parents for root in allowed):
        return _err(name, f"refusing out-of-tree preview destination: {dest}")
    if end_f <= start_f:
        return _err(name, f"empty preview window: [{start_f}, {end_f})")

    try:
        plan = build_plan(doc, project_dir=project_dir, window=(start_f, end_f))
    except (ValueError, KeyError, ValidationError) as e:
        return _err(name, "failed to build preview plan", str(e))

    return _run_encode(
        plan, dest, name,
        project_dir=Path(project_dir) if project_dir is not None else config.STATE_HOME,
    )
