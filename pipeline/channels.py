"""Channel profiles for editorial voice and caption styling.

`channels.json` in the state home overrides the bundled example: per-channel voice/audience/accent +
`captionStyle`. The planner reads a compact profile (voice/audience/accent) to match a channel's
tone; the caption engine reads `resolve_caption_style` to burn per-channel karaoke. An
unspecified channel resolves to the legacy config caption defaults, so long-form stays unchanged.

Stdlib + config only (no other pipeline imports) — dependency-light like project.py.
"""
from __future__ import annotations

import json
import math
import re

import config

# Caption-style defaults every consumer (`build_karaoke_ass`) reads. A channel's `captionStyle`
# is merged OVER this so unspecified fields keep the legacy behaviour. `look` picks the
# project-export caption look: the v3 "pill" by default, or the karaoke "outline" the other fields
# describe (the legacy caption_burn stage always burns the outline look).
_STYLE_DEFAULTS = {
    "look": "pill",
    "wordsPerPage": config.CAPTION_MAX_WORDS_LINE,
    "font": config.CAPTION_FONT,
    "bold": True,
    "baseColor": config.CAPTION_COLOR_DEFAULT,
    "activeColor": config.CAPTION_COLOR_ACTIVE,
    "emphasisColor": config.CAPTION_COLOR_ACTIVE,
    "marginVPct": 0.15,
    "alignment": 2,
    "outlineRatio": 14,
}


def _valid_style_value(key: str, value: object) -> bool:
    if isinstance(value, str) and ("\r" in value or "\n" in value):
        return False
    if key in ("baseColor", "activeColor", "emphasisColor"):
        return isinstance(value, str) and re.fullmatch(r"(?:&[Hh])?(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value) is not None
    if key == "look":
        return isinstance(value, str) and value in ("pill", "outline")
    if key == "font":
        return isinstance(value, str) and bool(value.strip())
    if key == "bold":
        return isinstance(value, bool)
    if key == "wordsPerPage":
        return type(value) is int and value > 0
    if key == "alignment":
        return type(value) is int and 1 <= value <= 9
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if key == "marginVPct":
        return 0 <= value <= 0.5
    return key == "outlineRatio" and value >= 1


def validate_caption_style(style: dict) -> None:
    """Reject unsafe or unsupported document and subtitle style overrides."""
    from pipeline.errors import ValidationError

    for key, value in style.items():
        if key not in _STYLE_DEFAULTS or not _valid_style_value(key, value):
            raise ValidationError(f"invalid caption style value: {key}")


def load() -> dict:
    """Read the state override when present, otherwise the bundled example."""
    path = config.CHANNELS_JSON if config.CHANNELS_JSON.exists() else config.CHANNELS_EXAMPLE_JSON
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict) or not isinstance(doc.get("channels"), dict):
        return {}
    profiles = {}
    for channel, raw in doc["channels"].items():
        if not isinstance(raw, dict):
            continue
        prof = dict(raw)
        style = prof.pop("captionStyle", None)
        if isinstance(style, dict):
            prof["captionStyle"] = {key: value for key, value in style.items()
                                    if key in _STYLE_DEFAULTS and _valid_style_value(key, value)}
        profiles[channel] = prof
    return {**doc, "channels": profiles}


def profile(channel: str | None) -> dict | None:
    """The full profile for `channel`, or None (unknown channel / no channels.json)."""
    if not channel:
        return None
    prof = load().get("channels", {}).get(channel)
    return prof if isinstance(prof, dict) else None


def resolve_caption_style(channel: str | None) -> dict:
    """The channel's `captionStyle` merged over the config caption defaults. Unknown/None channel ⇒
    the plain defaults (legacy long-form behaviour)."""
    style = dict(_STYLE_DEFAULTS)
    prof = profile(channel)
    if prof and isinstance(prof.get("captionStyle"), dict):
        style.update(prof["captionStyle"])
    return style
