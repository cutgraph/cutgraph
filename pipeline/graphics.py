"""Trusted first-party graphics registry and render descriptors.

The project document stores data only (a component id, version, and validated props).  This
module is deliberately declarative: no project value is interpreted as Python, a module name,
an HTML fragment, a URL, or a filesystem path.  Renderers consume only the five definitions below.
"""
from __future__ import annotations

import math
import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.errors import ValidationError
from pipeline import layout


@dataclass(frozen=True)
class GraphicDefinition:
    id: str
    version: int
    required: tuple[str, ...]
    prop_schema: dict[str, str]
    defaults: dict[str, Any]
    purpose: str
    thumbnail: str
    preview_renderer: str
    final_renderer: str
    style: dict[str, str]
    layout_width: float
    layout_height: float
    layers: tuple[dict[str, Any], ...]


_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "schema" / "graphics.registry.json"
_CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
if not (_CONTRACT.get("version") == 1):
    raise ValidationError("unsupported graphics registry contract version")
GEOMETRY = {key: value for key, value in _CONTRACT["geometry"].items()}
MOTION = {key: value for key, value in _CONTRACT["motion"].items()}
DEFINITIONS: tuple[GraphicDefinition, ...] = tuple(
    GraphicDefinition(
        id=raw["id"], version=raw["version"], required=tuple(raw["requiredFields"]),
        prop_schema=dict(raw["propSchema"]), defaults=copy.deepcopy(raw["defaults"]),
        purpose=raw["purpose"], thumbnail=raw["thumbnail"],
        preview_renderer=raw["previewRenderer"], final_renderer=raw["finalRenderer"],
        style=dict(raw["style"]), layout_width=raw["layout"]["width"], layout_height=raw["layout"]["height"],
        layers=tuple(copy.deepcopy(raw["layout"]["layers"])),
    ) for raw in _CONTRACT["components"]
)

REGISTRY: dict[str, GraphicDefinition] = {definition.id: definition for definition in DEFINITIONS}
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def definition(component_id: str) -> GraphicDefinition:
    try:
        return REGISTRY[component_id]
    except KeyError as exc:
        raise ValidationError(f"unknown graphic component: {component_id!r}") from exc


def _safe_text(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ValidationError(f"graphic {field} must be a string")
    # Props are rendered as text, never resolved as a resource.  Reject URL-like values anyway so
    # a future renderer cannot accidentally turn a data field into a network or path reference.
    lowered = value.lower()
    if not ("://" not in lowered and not lowered.startswith(("file:", "data:", "javascript:"))):
        raise ValidationError(f"graphic {field} cannot contain a URL or executable reference")


def validate_props(component_id: str, props: Any) -> dict[str, Any]:
    """Return a copy of valid serializable props; reject unknown keys and malformed values."""
    spec = definition(component_id)
    if not isinstance(props, dict):
        raise ValidationError(f"graphic {component_id} props must be an object")
    if not (set(props) == set(spec.required)):
        raise ValidationError(f"graphic {component_id} props must be exactly {spec.required}")
    result = copy.deepcopy(props)
    for field in spec.required:
        value = result[field]
        if component_id == "simple-chart" and field == "values":
            if not (isinstance(value, list) and 1 <= len(value) <= 12):
                raise ValidationError("chart values must be 1..12 numbers")
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value):
                raise ValidationError("chart values must be finite numbers")
        elif component_id == "simple-chart" and field == "labels":
            if not (isinstance(value, list) and len(value) == len(result["values"])):
                raise ValidationError("chart labels must match values")
            for label in value:
                _safe_text(label, "labels[]")
        else:
            _safe_text(value, field)
    if component_id == "callout":
        if not _HEX_COLOR.fullmatch(result["accent"]):
            raise ValidationError("callout accent must be a six-digit hex color")
    return result


def default_props(component_id: str) -> dict[str, Any]:
    """Return a fresh deterministic prop set for insertion."""
    return copy.deepcopy(definition(component_id).defaults)


def graphic_lines(component_id: str, props: dict[str, Any]) -> tuple[str, ...]:
    """Map validated props to the compact textual content shared by final rendering/tests."""
    p = validate_props(component_id, props)
    if component_id == "lower-third":
        return tuple(x for x in (p["eyebrow"], p["title"], p["detail"]) if x)
    if component_id == "callout":
        return (p["label"], p["body"])
    if component_id == "evidence-card":
        return (p["source"], p["claim"], p["detail"]) if p["detail"] else (p["source"], p["claim"])
    if component_id == "chapter-card":
        return tuple(x for x in (p["chapter"], p["title"], p["subtitle"]) if x)
    return (p["title"], "  ".join(f"{label}: {value:g}" for label, value in zip(p["labels"], p["values"])))


def graphic_style(component_id: str, props: dict[str, Any]) -> dict[str, str]:
    """Stable style tokens shared by browser preview and final-render descriptors."""
    p = validate_props(component_id, props)
    spec = definition(component_id)
    style = dict(spec.style)
    if (field := style.pop("foregroundProp", None)) is not None:
        style["foreground"] = p[field]
        style["border"] = p[field]
    return style


def graphic_layout(component_id: str) -> dict[str, Any]:
    """Return the declarative layout consumed by both preview and final renderers."""
    spec = definition(component_id)
    return {"width": spec.layout_width, "height": spec.layout_height,
            "layers": copy.deepcopy(spec.layers)}


def _round_px(value: float) -> int:
    """Round like JavaScript Math.round for the non-negative geometry values here."""
    return math.floor(value + 0.5)


_PX_KEYS = frozenset({"x", "y", "width", "height", "fontPx", "lineHeightPx", "barWidth", "gap",
                      "borderPx", "paddingPx", "cornerRadiusPx", "chartBaselinePx"})


def _scaled_px(value: Any, k: float) -> Any:
    """A geometry descriptor with every pixel value scaled by `k`; counts, ratios and colours as they are."""
    if isinstance(value, dict):
        return {key: _round_px(v * k) if key in _PX_KEYS else _scaled_px(v, k) for key, v in value.items()}
    if isinstance(value, list):
        return [_scaled_px(v, k) for v in value]
    return value


def graphic_geometry(component_id: str, props: dict[str, Any], canvas_width: int, canvas_height: int,
                     pos: dict[str, float], scale: float) -> dict[str, Any]:
    """Return the pixel descriptor consumed verbatim by the player and FFmpeg.

    The registry remains normalized for authoring, but every consumer receives concrete integer
    pixels.  This prevents browser flex/percentage rounding from drifting from the final render.
    A canvas larger than its 1080-class design (a 4K export) gets the design's descriptor scaled up,
    so its text fits and wraps exactly as it does at 1080.
    """
    if not (canvas_width > 0 and canvas_height > 0 and scale > 0):
        raise ValidationError()
    k = layout.design_scale(canvas_width, canvas_height)
    if k > 1:
        design = graphic_geometry(component_id, props, round(canvas_width / k), round(canvas_height / k), pos, scale)
        return _scaled_px(design, k)
    spec = definition(component_id)
    box_width = max(1, _round_px(spec.layout_width * canvas_width * scale))
    box_height = max(1, _round_px(spec.layout_height * canvas_height * scale))
    box_x = _round_px(float(pos["x"]) * canvas_width - box_width / 2)
    box_y = _round_px(float(pos["y"]) * canvas_height - box_height / 2)
    padding = int(GEOMETRY["paddingPx"])
    layers: list[dict[str, Any]] = []
    values = validate_props(component_id, props).get("values")
    max_value = max([abs(float(value)) for value in values] + [1.0]) if isinstance(values, list) else 1.0
    for layer in spec.layers:
        x = box_x + _round_px(float(layer["x"]) * box_width)
        y = box_y + _round_px(float(layer["y"]) * box_height)
        rel_x = _round_px(float(layer["x"]) * box_width)
        width = max(1, _round_px(float(layer.get("width", 1)) * box_width))
        if "width" not in layer:
            width = max(1, box_width - rel_x - padding)
        font_px = max(12, _round_px(float(layer.get("font", 0)) * box_height)) if "font" in layer else None
        height = max(1, _round_px(float(layer["height"]) * box_height)) if "height" in layer else max(1, _round_px((font_px or 12) * 1.2))
        item = {"kind": layer["kind"], "x": x, "y": y, "width": width, "height": height}
        if "field" in layer:
            item["field"] = layer["field"]
        if font_px is not None:
            item["fontPx"] = font_px
            item["lineHeightPx"] = max(1, _round_px(font_px * float(GEOMETRY["lineHeightRatio"])))
            item["maxChars"] = max(1, math.floor(item["width"] / max(1.0, font_px * float(GEOMETRY["charWidthRatio"]))))
            item["height"] = max(item["height"], item["lineHeightPx"])
        if layer["kind"] == "bars":
            count = len(values) if isinstance(values, list) else 0
            bar_width = max(1, width // max(count * 2, 1))
            gap = max(1, (width - bar_width * count) // max(count + 1, 1))
            bars = []
            for index, value in enumerate(values or []):
                bar_height = max(4, _round_px(abs(float(value)) / max_value * height))
                bars.append({"x": x + gap + index * (bar_width + gap), "y": y + height - bar_height,
                             "width": bar_width, "height": bar_height})
            item["barWidth"] = bar_width
            item["gap"] = gap
            item["baseline"] = {"x": x, "y": y + height, "width": width,
                                 "height": int(GEOMETRY["chartBaselinePx"])}
            item["bars"] = bars
        layers.append(item)
    style: dict[str, Any] = dict(graphic_style(component_id, props))
    style.update({"backgroundAlpha": float(GEOMETRY["backgroundAlpha"]),
                  "borderPx": int(GEOMETRY["borderPx"]), "paddingPx": padding,
                  "cornerRadiusPx": int(GEOMETRY["cornerRadiusPx"]),
                  "chartBaselinePx": int(GEOMETRY["chartBaselinePx"])})
    return {"box": {"x": box_x, "y": box_y, "width": box_width, "height": box_height},
            "style": style, "layers": layers}


def graphic_motion(dur_f: int, canvas_height: int) -> dict[str, int]:
    """Entrance/exit timing shared with the preview: a graphic fades in over `inF` frames while rising
    `slidePx` into place, then fades out over `outF` frames while sinking back. A span too short for
    both gives each half of it."""
    half = max(0, dur_f // 2)
    return {"inF": min(int(MOTION["inF"]), half), "outF": min(int(MOTION["outF"]), half),
            "slidePx": _round_px(float(MOTION["slide"]) * canvas_height)}


def fit_graphic_text(value: str, max_chars: int) -> str:
    """Apply the same one-line ellipsis policy used by preview and final render."""
    if len(value) <= max_chars:
        return value
    if max_chars <= 1:
        return "…"
    return value[:max_chars - 1] + "…"
