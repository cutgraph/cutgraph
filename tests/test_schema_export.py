"""Drift + parity guards for schema/project.schema.json vs scripts/export_schema.py and project.py.

Drift: the checked-in schema must byte-equal the generator output.
Parity: every dataclass field in pipeline/project.py must appear as a schema property.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from pipeline import project

_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = _ROOT / "schema" / "project.schema.json"


def _load_export_schema() -> ModuleType:
    # scripts/ is not an importable package, so load the CLI module by path (it puts the repo
    # root on sys.path itself for its `import config`).
    spec = importlib.util.spec_from_file_location("export_schema", _ROOT / "scripts" / "export_schema.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


export_schema = _load_export_schema()


def test_checked_in_schema_matches_generator():
    committed = SCHEMA_PATH.read_text(encoding="utf-8")
    assert export_schema.render() == committed, "schema/project.schema.json is stale — re-run export_schema.py"


def _all_property_keys(node, keys: set) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "properties" and isinstance(v, dict):
                keys.update(v.keys())
            _all_property_keys(v, keys)
    elif isinstance(node, list):
        for item in node:
            _all_property_keys(item, keys)


def test_every_dataclass_field_present_in_schema():
    schema = json.loads(export_schema.render())
    keys: set = set()
    _all_property_keys(schema, keys)
    for dc in (
        project.Media,
        project.Anchor,
        project.Clip,
        project.TextItem,
        project.Track,
        project.Scene,
        project.Project,
    ):
        for f in dataclasses.fields(dc):
            assert f.name in keys, f"{dc.__name__}.{f.name} missing from schema properties"


def test_schema_defs_cover_all_model_types():
    schema = json.loads(export_schema.render())
    # Project is the root object (title "Project"); the composite sub-types live in $defs.
    assert schema["title"] == "Project"
    defs = set(schema["$defs"].keys())
    assert {"Media", "Anchor", "Clip", "TextItem", "Track", "Transform"} <= defs


def test_schema_encodes_validate_single_field_bounds():
    # The schema must not be weaker than validate() on single-field rules: fps pinned to the
    # config default, frame fields floored, pos boxed to the unit square.
    schema = json.loads(export_schema.render())
    import config

    assert schema["properties"]["fps"]["const"] == config.DEFAULT_FPS
    clip = schema["$defs"]["Clip"]["properties"]
    assert clip["srcInF"]["minimum"] == 0
    assert clip["tlStartF"]["minimum"] == 0
    assert clip["srcOutF"]["minimum"] == 1
    text = schema["$defs"]["TextItem"]["properties"]
    assert text["durF"]["minimum"] == 1
    pos = text["pos"]["properties"]
    assert pos["x"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert pos["y"] == {"type": "number", "minimum": 0, "maximum": 1}
    transform = schema["$defs"]["Transform"]["properties"]
    assert transform["x"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert transform["y"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert transform["scale"] == {"type": "number", "minimum": 0.1, "maximum": 4}
