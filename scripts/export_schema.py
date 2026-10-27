r"""Emit schema/project.schema.json — the JSON Schema for the project.json contract.

    .venv\Scripts\python scripts\export_schema.py

The schema is hand-authored as a dict literal here (NOT auto-derived): pipeline/project.py must
uses plain dataclasses without a schema-reflection dependency. The model is small; tests/test_schema_export.py is the drift + parity guard
that keeps this file honest against pipeline/project.py. json-schema-to-typescript reads the output
to generate ui/src/types/project.ts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

_ANCHOR = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["word", "time"]},
        "ref": {
            "type": ["object", "null"],
            "properties": {
                "media": {"type": "string"},
                "wordIdx": {"type": "integer"},
            },
            "required": ["media", "wordIdx"],
            "additionalProperties": False,
        },
        "offsetF": {"type": "integer"},
    },
    "required": ["mode"],
    "additionalProperties": False,
}

_MEDIA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "proxy": {"type": ["string", "null"]},
    },
    "required": ["source"],
    "additionalProperties": False,
}

_TRANSFORM = {
    "type": "object",
    "description": "Normalized project-space placement for a visual clip. Omit to use its legacy default.",
    "properties": {
        "x": {"type": "number", "minimum": 0, "maximum": 1},
        "y": {"type": "number", "minimum": 0, "maximum": 1},
        "scale": {"type": "number", "minimum": 0.1, "maximum": 4},
        "fit": {"type": "string", "enum": ["fill", "contain", "cover"]},
    },
    "required": ["x", "y", "scale", "fit"],
    "additionalProperties": False,
}

_CLIP = {
    "type": "object",
    "description": (
        "Cross-field rules are enforced by pipeline.project.validate(), NOT expressible here: "
        "srcOutF > srcInF; speech-track clips carry no anchor; within-track clips never overlap."
    ),
    "properties": {
        "id": {"type": "string"},
        "media": {"$ref": "#/$defs/Media"},
        "srcInF": {"type": "integer", "minimum": 0},
        "srcOutF": {"type": "integer", "minimum": 1},
        "tlStartF": {"type": "integer", "minimum": 0},
        "gainDb": {"type": "number"},
        "anchor": {"$ref": "#/$defs/Anchor"},
        "duckDb": {"type": ["number", "null"]},
        "seams": {"type": ["array", "null"], "items": {
            "type": "object",
            "properties": {"atF": {"type": "integer", "minimum": 0},
                           "fadeMs": {"type": "integer", "minimum": 1}},
            "required": ["atF", "fadeMs"], "additionalProperties": False}},
        "transform": {"$ref": "#/$defs/Transform"},
    },
    "required": ["id", "media", "srcInF", "srcOutF", "tlStartF"],
    "additionalProperties": False,
}

_TEXT_ITEM = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "text": {"type": "string"},
        "preset": {"type": "string"},
        "scale": {"type": "number", "minimum": 0.1, "maximum": 4},
        "pos": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "minimum": 0, "maximum": 1},
                "y": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
        "tlStartF": {"type": "integer", "minimum": 0},
        "durF": {"type": "integer", "minimum": 1},
        "anchor": {"$ref": "#/$defs/Anchor"},
    },
    "required": ["id", "text", "preset", "pos", "tlStartF", "durF"],
    "additionalProperties": False,
}

_GRAPHIC_PROPS = {
    "lower-third": {
        "type": "object", "properties": {
            "eyebrow": {"type": "string"}, "title": {"type": "string"}, "detail": {"type": "string"},
        }, "required": ["eyebrow", "title", "detail"], "additionalProperties": False,
    },
    "callout": {
        "type": "object", "properties": {
            "label": {"type": "string"}, "body": {"type": "string"},
            "accent": {"type": "string", "pattern": r"^#[0-9A-Fa-f]{6}$"},
        }, "required": ["label", "body", "accent"], "additionalProperties": False,
    },
    "evidence-card": {
        "type": "object", "properties": {
            "source": {"type": "string"}, "claim": {"type": "string"}, "detail": {"type": "string"},
        }, "required": ["source", "claim", "detail"], "additionalProperties": False,
    },
    "chapter-card": {
        "type": "object", "properties": {
            "chapter": {"type": "string"}, "title": {"type": "string"}, "subtitle": {"type": "string"},
        }, "required": ["chapter", "title", "subtitle"], "additionalProperties": False,
    },
    "simple-chart": {
        "type": "object", "properties": {
            "title": {"type": "string"},
            "labels": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "string"}},
            "values": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "number"}},
        }, "required": ["title", "labels", "values"], "additionalProperties": False,
    },
}

_GRAPHIC_COMMON_PROPERTIES = {
    "type": {"const": "graphic"},
    "id": {"type": "string"},
    "componentVersion": {"type": "integer", "const": 1},
    "pos": {
        "type": "object", "properties": {
            "x": {"type": "number", "minimum": 0, "maximum": 1},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
        }, "required": ["x", "y"], "additionalProperties": False,
    },
    "scale": {"type": "number", "minimum": 0.1, "maximum": 4},
    "tlStartF": {"type": "integer", "minimum": 0},
    "durF": {"type": "integer", "minimum": 1},
    "anchor": {"$ref": "#/$defs/Anchor"},
}


def _graphic_variant(component_id: str) -> dict:
    return {
        "type": "object",
        "description": "A trusted first-party graphic. Project data carries values only; renderers are selected from the built-in registry.",
        "properties": {
            **_GRAPHIC_COMMON_PROPERTIES,
            "componentId": {"const": component_id},
            "props": {"$ref": f"#/$defs/{component_id.replace('-', '').title()}Props"},
        },
        "required": ["type", "id", "componentId", "componentVersion", "props", "pos", "scale", "tlStartF", "durF"],
        "additionalProperties": False,
    }


_GRAPHIC_ITEM = {"oneOf": [_graphic_variant(component_id) for component_id in _GRAPHIC_PROPS]}

_TRACK = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string", "enum": ["speech", "video", "text", "audio"]},
        "clips": {"type": "array", "items": {"$ref": "#/$defs/Clip"}},
        "items": {"type": "array", "items": {"oneOf": [{"$ref": "#/$defs/TextItem"}, {"$ref": "#/$defs/GraphicItem"}]}},
    },
    "required": ["id", "kind"],
    "additionalProperties": False,
}

_SHOT = {
    "type": "object",
    "description": (
        "Optional direction for an animated scene; cueWord is a flat transcript wordIdx. mode says how "
        "the scene is pictured (absent = footage): its footage, a full-frame animation, or a character "
        "cameo over the footage. character is a cast profile id (pipeline.cast); absent, or one the "
        "video's workspace can't see, means the workspace's default character. costar puts a second "
        "character beside it in an animation (a duo), and interaction is how the two play off each "
        "other on the cue word (absent = none). eyebrow is the line over an animation's headline: absent "
        "means the video's workspace brand, an empty string hides it. bubble puts a thought, speech, a "
        "'!', a '?' or two simultaneous neutral '!!' marks (exclaims) over the character (the co-star and each ensemble "
        "member carry their own), popping in on the cue word; ensemble lines up to three more characters after the lead and the co-star in an "
        "animation, where a duo's interaction is not played. hideHeadline turns off the headline and "
        "eyebrow on the stage, a duo or an ensemble; a screen, title or sign-off layout ignores it. "
        "diagram selects a classic drawing or an independent grid, chain, radial, tree or list preset "
        "(motion-core/STAGING.md); each scene has its own geometry, labels and reveal timing."
    ),
    "properties": {
        "mode": {"enum": ["footage", "animation", "cameo"]},
        "character": {"type": "string"},
        "pose": {"type": "string"},
        "expression": {"type": "string"},
        "move": {"type": "string"},
        "backdrop": {"type": "string", "description": "A scene backdrop id; vision is a generic bright radial backdrop."},
        "diagram": {"type": "string", "examples": ["scatter", "blank", "brief", "route", "checks", "glow", "none", "grid", "chain", "radial", "tree", "list"]},
        "cueWord": {"type": "integer", "minimum": 0},
        "headline": {"type": "string"},
        "eyebrow": {"type": "string", "maxLength": 40},
        "hideHeadline": {"type": "boolean",
                          "description": "Animation only: true draws no headline or eyebrow on the "
                                         "stage, a duo or an ensemble (a screen, title or sign-off "
                                         "layout ignores it)."},
        "costar": {"$ref": "#/$defs/Costar"},
        "interaction": {"enum": ["none", "face", "fistbump", "highfive", "point"]},
        "bubble": {"$ref": "#/$defs/Bubble"},
        "ensemble": {"type": "array", "items": {"$ref": "#/$defs/Costar"}, "maxItems": 3},
        "camera": {"enum": ["push-in", "pull-out", "pan"],
                   "description": "An animation's camera move: a bounded centre-framed push at the scene's end, an independently framed pull at its "
                                  "start, or a pan that carries the stage in from the left as it opens."},
        "layout": {"enum": ["stage", "screen", "title", "signoff"],
                   "description": "How an animation's frame is composed (absent = stage, the character shot)."},
        "screen": {"$ref": "#/$defs/PhoneScreen"},
        "badges": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"$ref": "#/$defs/Badge"},
                   "description": "A title card's badges, dropping in above its headline; on a stage with an "
                                  "ensemble, one under each member."},
        "mark": {"type": "string", "pattern": "^[A-Za-z0-9&]{1,4}$",
                 "description": "A sign-off's optional pixel text (absent = no mark)."},
        "headlineAt": {"enum": ["costar"],
                       "description": "A duo's or an ensemble's headline over the co-star instead of its own "
                                      "place, fading in a quarter of the way through the scene."},
        "boardLabels": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string", "maxLength": 12},
                        "contains": {"type": "string", "pattern": "\\S"},
                        "description": "An independent diagram's labels in node order: grid by row, chain along its path, "
                                       "radial centre then clockwise from the top, tree by level, list top to bottom. "
                                       "One to six entries size the board; blanks draw empty boxes. Omitted labels draw three blank nodes."},
    },
    "additionalProperties": False,
}

_PHONE_SCREEN = {
    "type": "object",
    "description": (
        "A screen shot's phones: asset is phone 1's library image (absent screen = a generated placeholder "
        "app screen), more is phones 2 and 3, drawn beside it."
    ),
    "properties": {
        "asset": {"type": "string", "pattern": "^[a-f0-9]{16}$"},
        "more": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "pattern": "^[a-f0-9]{16}$"}},
    },
    "required": ["asset"],
    "additionalProperties": False,
}

_BADGE = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 24, "pattern": r"\S"},
        "tone": {"enum": ["ink", "paper", "accent"]},
    },
    "required": ["text", "tone"],
    "additionalProperties": False,
}

_COSTAR = {
    "type": "object",
    "description": (
        "A duo shot's second character, or an ensemble member: a cast profile id and its own look (ids as "
        "for the lead) and bubble."
    ),
    "properties": {
        "character": {"type": "string", "minLength": 1},
        "pose": {"type": "string"},
        "expression": {"type": "string"},
        "move": {"type": "string"},
        "bubble": {"$ref": "#/$defs/Bubble"},
    },
    "required": ["character"],
    "additionalProperties": False,
}

_THUMBNAIL = {
    "type": "object",
    "description": (
        "A video's thumbnail: a still drawn by motion-core (motion-core/POSTER.md) at 1280x720 and "
        "1080x1920. headline is the title (absent = the video's title); eyebrow a pill over it (absent or "
        "empty = none); character, pose and expression the lead in the foreground (absent character = the "
        "video's first animated scene's lead); ensemble the characters standing behind it (absent = the "
        "cast's others, up to three; empty = none); backdrop a scene backdrop id (absent = work)."
    ),
    "properties": {
        "headline": {"type": "string", "maxLength": 60},
        "eyebrow": {"type": "string", "maxLength": 40},
        "badges": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"$ref": "#/$defs/Badge"}},
        "character": {"type": "string"},
        "pose": {"type": "string"},
        "expression": {"type": "string"},
        "ensemble": {"type": "array", "items": {"$ref": "#/$defs/ThumbnailMember"}, "maxItems": 3},
        "backdrop": {"type": "string"},
    },
    "additionalProperties": False,
}

_THUMBNAIL_MEMBER = {
    "type": "object",
    "description": "A character standing behind a thumbnail's lead: a cast profile id and its look.",
    "properties": {
        "character": {"type": "string", "minLength": 1},
        "pose": {"type": "string"},
        "expression": {"type": "string"},
        "badge": {"$ref": "#/$defs/Badge"},
    },
    "required": ["character"],
    "additionalProperties": False,
}

_SCENE = {
    "type": "object",
    "description": (
        "A topical group of transcript words. List order is story order; a scene covers the flat "
        "wordIdx range [startWord, next-larger startWord). Cross-field rules are enforced by "
        "pipeline.project.validate(), NOT expressible here: scene ids and startWords are unique and "
        "the earliest scene starts at word 0."
    ),
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string", "minLength": 1, "maxLength": 120},
        "role": {"type": "string", "enum": [
            "Hook", "Problem", "Approach", "Build", "Example", "Check", "Payoff", "Wrap", "Beat",
        ]},
        "startWord": {"type": "integer", "minimum": 0},
        "shot": {"$ref": "#/$defs/Shot"},
    },
    "required": ["id", "title", "role", "startWord"],
    "additionalProperties": False,
}

_PROJECT_PROPERTIES = {
    "schemaVersion": {"type": "integer"},
    "name": {"type": "string"},
    "title": {"type": ["string", "null"], "minLength": 1, "maxLength": 120},
    "fps": {"type": "integer", "const": config.DEFAULT_FPS},
    "width": {"type": "integer", "minimum": 1},
    "height": {"type": "integer", "minimum": 1},
    "revision": {"type": "integer", "minimum": 0},
    "transcript": {"type": ["string", "null"]},
    "channel": {"type": ["string", "null"]},
    "workspaceId": {"type": ["string", "null"]},
    "narration": {"type": ["string", "null"], "enum": ["estimated", "recorded", None]},
    "thumbnail": {"$ref": "#/$defs/Thumbnail"},
    "crop": {
        "type": ["object", "null"],
        "properties": {
            "mode": {"type": "string", "enum": ["center", "reframe"]},
            "metaPath": {"type": "string"},
        },
        "required": ["mode"],
        "additionalProperties": False,
    },
    "captions": {
        "type": ["object", "null"],
        "properties": {
            "enabled": {"type": "boolean"},
            "style": {"type": ["object", "null"]},
            "emphasis": {"type": "array", "items": {"type": "integer", "minimum": 0}},
        },
        "required": ["enabled"],
        "additionalProperties": False,
    },
    "tracks": {"type": "array", "items": {"$ref": "#/$defs/Track"}},
    "scenes": {"type": "array", "items": {"$ref": "#/$defs/Scene"}},
}


def build_schema() -> dict:
    # Root IS the Project object (title "Project") rather than a top-level {"$ref": "#/$defs/Project"}:
    # json-schema-to-typescript v15 errors ("Refs should have been resolved by the resolver!") on a
    # bare top-level $ref beside $defs, and it names the top interface from `title`. The UI imports
    # `{ Project }`, so the top type must be `Project`. Sub-types stay in $defs.
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Project",
        "type": "object",
        "properties": _PROJECT_PROPERTIES,
        "required": ["schemaVersion", "name", "fps", "width", "height", "revision", "tracks"],
        "additionalProperties": False,
        "$defs": {
            "Track": _TRACK,
            "Clip": _CLIP,
            "TextItem": _TEXT_ITEM,
            "GraphicItem": _GRAPHIC_ITEM,
            "Anchor": _ANCHOR,
            "Media": _MEDIA,
            "Transform": _TRANSFORM,
            "Scene": _SCENE,
            "Shot": _SHOT,
            "Costar": _COSTAR,
            "Bubble": {"enum": ["think", "say", "exclaim", "question", "exclaims"]},
            "PhoneScreen": _PHONE_SCREEN,
            "Badge": _BADGE,
            "Thumbnail": _THUMBNAIL,
            "ThumbnailMember": _THUMBNAIL_MEMBER,
            **{
                f"{component_id.replace('-', '').title()}Props": props
                for component_id, props in _GRAPHIC_PROPS.items()
            },
        },
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    out = config.CODE_ROOT / "schema" / "project.schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so the "\n" in render() is written verbatim (LF), matching git's normalized blob
    # and keeping the drift guard stable across platforms.
    out.write_text(render(), encoding="utf-8", newline="")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
