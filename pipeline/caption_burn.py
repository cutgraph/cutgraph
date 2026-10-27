"""ASS caption builders for project exports, with pill and karaoke looks."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

import config
from pipeline import caption_pages, channels
from pipeline.runtime_tools import font_path


def _ass_colour(c: str) -> str:
    """Normalize a caption colour to ASS V4+ `&HAABBGGRR`. config caption defaults are 6-hex `&HBBGGRR`
    (AA implied opaque); channels.json colours are already 8-hex `&HAABBGGRR`. Prepending `&H00` blindly
    would DOUBLE the alpha on a channel colour (a 10-hex token libass rejects), so pass an already-8-hex
    value through unchanged (idempotent) and only pad the 6-hex form."""
    h = c.replace("&H", "").replace("&h", "").upper()
    return "&H" + h if len(h) >= 8 else "&H00" + h


def _safe_stem(name: str) -> str:
    """A filtergraph/filename-safe stem for the on-disk ASS file: the `ass=` value is embedded in
    the filtergraph, so a doc-controlled name must never carry a comma/quote/path separator."""
    from pipeline.errors import ValidationError
    from pipeline.names import project_name, reserved_name, validate_existing_name

    if reserved_name(name):
        raise ValidationError("name cannot be a reserved device name")
    stem = "".join(ch for ch in name if ch.isascii() and (ch.isalnum() or ch in "-_")) or "captions"
    return project_name(stem) or validate_existing_name(stem)


def _escape_ass_text(text: str) -> str:
    """Neutralize line breaks and characters that would corrupt ASS event or override syntax.
    `{`/`}` open/close an override block and `\\` starts a tag.
    WhisperX words are plain speech, but the emphasis-override path builds real `{...}` tags,
    so any literal brace/backslash in the WORD is replaced with a visually-close safe glyph."""
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\\", "/").replace("{", "(").replace("}", ")")


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_header(width: int, height: int, style: dict) -> list[str]:
    """The [Script Info]/[V4+ Styles]/[Events] preamble for a resolved per-channel caption style.
    Colours + font + alignment + margins come from the style dict (channels.resolve_caption_style); the
    default style reproduces the legacy long-form header byte-for-byte."""
    primary = _ass_colour(style["activeColor"])     # filled/spoken = accent (ASS \k sweeps TO Primary)
    secondary = _ass_colour(style["baseColor"])     # not-yet-spoken = white (pre-sweep = Secondary)
    font = "Inter"  # Bold rides its own flag, not the family name
    bold = 1 if style.get("bold", True) else 0
    fontsize = round(config.CAPTION_SIZE * height / 1080.0)  # CAPTION_SIZE is px at 1080p; scale to frame
    outline = max(3, round(fontsize / style["outlineRatio"]))  # heavy halo so white reads on any background
    shadow = max(1, round(fontsize / 40))
    margin_v = round(height * style["marginVPct"])
    alignment = int(style["alignment"])
    return [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{fontsize},{primary},{secondary},"
        f"&H00000000,&H78000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},{alignment},80,80,{margin_v},1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]


def build_karaoke_ass(words: list[dict], width: int, height: int, *, style: dict,
                      emphasis_idx: "frozenset[int]" = frozenset()) -> str:
    """The ONE karaoke `\\k` ASS generator: serves BOTH long-form captions and 9:16 shorts off the
    same word clock. `words` is the clip-relative word list (export word timings). `style` is a resolved
    per-channel caption style. Words page `style["wordsPerPage"]` at a time; each word gets a `{\\kNN}`
    fill (centiseconds to the NEXT word's start, so a page's `\\k` values SUM to the page span). A word
    whose CLIP-RELATIVE index is in `emphasis_idx` is wrapped in a `{\\c<emphasis>&}word{\\c<active>&}`
    colour override AFTER its `\\k` tag: the trailing reset restores the PAGE's karaoke fill
    (PrimaryColour = activeColor), NOT baseColor — resetting to baseColor (the unsung SecondaryColour,
    white) would persist to the end of the single-Dialogue page line and white-out every following word's
    `\\k` fill. All word text is brace/backslash-escaped first (no doc corruption)."""
    channels.validate_caption_style(style)
    emph_c = _ass_colour(style["emphasisColor"])
    active_c = _ass_colour(style["activeColor"])   # the karaoke fill (PrimaryColour) to restore after emphasis
    events = []
    n = max(1, int(style["wordsPerPage"]))
    for i in range(0, len(words), n):
        line = words[i:i + n]
        ls, le = line[0]["start"], line[-1]["end"]
        parts = []
        for j, w in enumerate(line):
            nxt = line[j + 1]["start"] if j + 1 < len(line) else w["end"]
            k = max(1, int(round((nxt - w["start"]) * 100)))  # fill until the next word starts
            token = _escape_ass_text(w["word"])
            if (i + j) in emphasis_idx:
                token = f"{{\\c{emph_c}&}}{token}{{\\c{active_c}&}}"
            parts.append(f"{{\\k{k}}}{token} ")
        events.append(
            f"Dialogue: 0,{_ass_time(ls)},{_ass_time(le)},Default,,0,0,0,,{''.join(parts).strip()}"
        )
    return "\n".join(_ass_header(width, height, style) + events) + "\n"


def _ass_document(words: list[dict], width: int, height: int) -> str:
    """Build karaoke captions with the default channel style."""
    return build_karaoke_ass(words, width, height, style=channels.resolve_caption_style(None))


def write_karaoke_ass(cid: str, words: list[dict], width: int, height: int, *, style: dict,
                      emphasis_idx: "frozenset[int]" = frozenset(),
                      output_dir: "Path | None" = None,
                      runtime_root: "Path | None" = None) -> str:
    """Write the karaoke ASS to CAPTIONS_DIR/<safe cid>.ass and return the ROOT-relative POSIX path for
    the filtergraph `ass=` token (the `.relative_to(ROOT).as_posix()` trick dodges the `D:` drive-colon;
    the filename is sanitized so a doc-controlled id never reaches the unquoted filtergraph token)."""
    captions_dir = Path(output_dir) if output_dir is not None else config.CAPTIONS_DIR
    root = Path(runtime_root) if runtime_root is not None else config.STATE_HOME
    captions_dir.mkdir(parents=True, exist_ok=True)
    ass_path = captions_dir / f"{_safe_stem(cid)}.ass"
    ass_path.write_text(build_karaoke_ass(words, width, height, style=style, emphasis_idx=emphasis_idx),
                        encoding="utf-8")
    return ass_path.relative_to(root).as_posix()


# Inter Bold's vertical metrics (usWinAscent, usWinDescent, unitsPerEm). libass sizes a font so
# ascent + descent equals the ASS Fontsize, while the preview's CSS size is the em; and `\an2` anchors
# the bottom of that ascent/descent line box, one descent below the baseline.
_PILL_ASC, _PILL_DESC, _PILL_UPM = 1984, 494, 2048
_PILL_ACCENT = config.BRAND_ACCENT[1:].upper()
_PILL_TAGS = {
    "C": f"\\1c&H{_PILL_ACCENT[4:6]}{_PILL_ACCENT[2:4]}{_PILL_ACCENT[:2]}&\\1a&H00&",
    "P": "\\1c&HF7F6F4&\\1a&H00&",   # spoken: #f4f6f7
    "U": "\\1c&HF7F6F4&\\1a&H80&",   # upcoming: #f4f6f7 at 50%
}
_PILL_BOX = "\\1c&H0C0A08&\\1a&H47&"  # rgba(8,10,12,.72)


@lru_cache(maxsize=1)
def _pill_font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path("bold", role="sans")), _PILL_UPM)


def _pill_text_width(text: str, fs: float) -> float:
    """Unkerned Inter Bold advance, matching libass and the preview."""
    font = _pill_font()
    return sum(font.getlength(c) for c in text) * fs / _PILL_UPM


def _cs_time(cs: int) -> str:
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _round_rect(x: float, y: float, w: float, h: float, r: float) -> str:
    """An ASS vector path for a rounded rectangle (cubic quarter arcs)."""
    k = r * 0.4477  # r * (1 - 0.5523): how far a quarter-arc control point sits in from the corner

    def p(*xy: float) -> str:
        return " ".join(f"{v:.2f}" for v in xy)

    x1, y1 = x + w, y + h
    return (f"m {p(x + r, y)} l {p(x1 - r, y)} b {p(x1 - k, y, x1, y + k, x1, y + r)} "
            f"l {p(x1, y1 - r)} b {p(x1, y1 - k, x1 - k, y1, x1 - r, y1)} "
            f"l {p(x + r, y1)} b {p(x + k, y1, x, y1 - k, x, y1 - r)} "
            f"l {p(x, y + r)} b {p(x, y + k, x + k, y, x + r, y)}")


def build_pill_ass(pages: list[dict], width: int, height: int) -> str:
    """Pill captions as ASS: per page, a rounded rgba(8,10,12,.72) box on layer 0
    (0.6·fs padding each side, 1.6·fs tall, top 1.1·fs above the baseline, radius 0.35·fs), and on
    layer 1 one Dialogue per `caption_events` interval showing the whole line in bold Inter, each
    word coloured by its state. No outline, no shadow. `pages` come from `caption_pages`."""
    cap_y, fs = caption_pages.layout(width, height)
    size = fs * (_PILL_ASC + _PILL_DESC) / _PILL_UPM
    base_y = cap_y + fs * _PILL_DESC / _PILL_UPM
    lines = [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Pill,Inter,{size:.2f},&H00F7F6F4,&H00F7F6F4,&H00000000,&H00000000,"
        "1,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    texts = [[_escape_ass_text(w["w"]) for w in page["words"]] for page in pages]
    for page, words in zip(pages, texts):
        if page["off"] <= page["on"]:
            continue
        box_w = _pill_text_width(" ".join(words), fs) + 1.2 * fs
        box = _round_rect((width - box_w) / 2, cap_y - 1.1 * fs, box_w, 1.6 * fs, 0.35 * fs)
        lines.append(f"Dialogue: 0,{_cs_time(page['on'])},{_cs_time(page['off'])},Pill,,0,0,0,,"
                     f"{{\\an7\\pos(0,0)\\p1{_PILL_BOX}}}{box}")
    for ev in caption_pages.caption_events(pages):
        line = " ".join(f"{{{_PILL_TAGS[s]}}}{w}" for w, s in zip(texts[ev["page"]], ev["states"]))
        lines.append(f"Dialogue: 1,{_cs_time(ev['start'])},{_cs_time(ev['end'])},Pill,,0,0,0,,"
                     f"{{\\an2\\pos({width / 2:.2f},{base_y:.2f})}}{line}")
    return "\n".join(lines) + "\n"


def write_pill_ass(cid: str, pages: list[dict], width: int, height: int, *,
                   output_dir: "Path | None" = None, runtime_root: "Path | None" = None) -> str:
    """Write the pill ASS beside the karaoke one and return the ROOT-relative POSIX `ass=` path (the
    same sanitized-name, drive-colon-free contract as `write_karaoke_ass`)."""
    captions_dir = Path(output_dir) if output_dir is not None else config.CAPTIONS_DIR
    root = Path(runtime_root) if runtime_root is not None else config.STATE_HOME
    captions_dir.mkdir(parents=True, exist_ok=True)
    ass_path = captions_dir / f"{_safe_stem(cid)}.ass"
    ass_path.write_bytes(build_pill_ass(pages, width, height).encode("utf-8"))
    return ass_path.relative_to(root).as_posix()
