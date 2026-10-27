"""Graphics contract, trusted registry, and final-render parity checks."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from pipeline.errors import ValidationError

import config
from pipeline import compile as compile_stage
from pipeline import graphics
from pipeline import project
from scripts.export_graphics_registry import render as render_graphics_registry
from scripts.export_graphics_geometry import render as render_graphics_geometry
from scripts import studio_app


def _item(component_id: str, props: dict | None = None) -> dict:
    return {
        "type": "graphic", "id": f"graphic-{component_id}", "componentId": component_id,
        "componentVersion": 1, "props": props or graphics.default_props(component_id),
        "pos": {"x": 0.5, "y": 0.7}, "scale": 1, "tlStartF": 0, "durF": 180,
    }


def _doc(item: dict) -> dict:
    return {"schemaVersion": 2, "name": "graphics-contract", "fps": 30, "width": 1920,
            "height": 1080, "revision": 1, "tracks": [{"id": "video", "kind": "video", "clips": [
                {"id": "clip", "media": {"source": "media/clip.mp4", "proxy": None}, "srcInF": 0,
                 "srcOutF": 180, "tlStartF": 0, "gainDb": 0.0}],}, {"id": "graphics", "kind": "text", "items": [item]}]}


@pytest.mark.parametrize("component_id", [d.id for d in graphics.DEFINITIONS])
def test_every_first_party_graphic_roundtrips_and_validates(component_id: str) -> None:
    raw = _doc(_item(component_id))
    model = project.from_dict(raw)
    project.validate(model)
    assert project.to_dict(model) == raw | {"transcript": None, "schemaVersion": project.CURRENT_SCHEMA_VERSION}


def test_graphics_registry_is_generated_from_one_authoritative_contract() -> None:
    source = json.loads((Path(__file__).resolve().parents[1] / "schema/graphics.registry.json").read_text())
    assert [item.id for item in graphics.DEFINITIONS] == [raw["id"] for raw in source["components"]]
    for definition_item, raw in zip(graphics.DEFINITIONS, source["components"]):
        assert definition_item.required == tuple(raw["requiredFields"])
        assert definition_item.prop_schema == raw["propSchema"]
        assert definition_item.thumbnail == raw["thumbnail"]
        assert definition_item.layers == tuple(raw["layout"]["layers"])
    generated = (Path(__file__).resolve().parents[1] / "ui/src/graphics/registry.data.ts").read_text()
    assert generated == render_graphics_registry()
    generated_geometry = (Path(__file__).resolve().parents[1] / "ui/test/geometry.golden.ts").read_text()
    assert generated_geometry == render_graphics_geometry()


@pytest.mark.parametrize("component_id", [d.id for d in graphics.DEFINITIONS])
def test_a_4k_canvas_gets_the_1080_design_at_twice_the_pixels(component_id: str) -> None:
    """A 4K export draws each graphic as its 1080 design scaled up: every pixel value doubles,
    and every text layer fits the same characters, so the 4K frame is the 1080 frame with more detail."""
    props, pos = graphics.default_props(component_id), {"x": 0.37, "y": 0.61}
    design = graphics.graphic_geometry(component_id, props, 1920, 1080, pos, 0.9)
    uhd = graphics.graphic_geometry(component_id, props, 3840, 2160, pos, 0.9)
    assert uhd["box"] == {key: 2 * value for key, value in design["box"].items()}
    assert (uhd["style"]["borderPx"], uhd["style"]["paddingPx"]) == (4, 32)
    assert [layer.get("maxChars") for layer in uhd["layers"]] == [layer.get("maxChars") for layer in design["layers"]]
    assert [(layer["x"], layer["y"], layer["width"], layer["height"], layer.get("fontPx")) for layer in uhd["layers"]] == [
        (2 * layer["x"], 2 * layer["y"], 2 * layer["width"], 2 * layer["height"], layer.get("fontPx") and 2 * layer["fontPx"])
        for layer in design["layers"]]
    assert graphics.graphic_motion(90, 2160)["slidePx"] == 2 * graphics.graphic_motion(90, 1080)["slidePx"]


def test_repository_synthetic_fixture_covers_every_component() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "graphics-synthetic.json"
    raw = json.loads(fixture_path.read_text())
    model = project.from_dict(raw)
    project.validate(model)
    schema = json.loads((Path(__file__).resolve().parents[1] / "schema/project.schema.json").read_text())
    jsonschema.validate(raw, schema)
    items = model.tracks[1].items
    assert {item.componentId for item in items if isinstance(item, project.GraphicItem)} == {
        definition_item.id for definition_item in graphics.DEFINITIONS
    }


def test_graphic_schema_rejects_unknown_component_and_props() -> None:
    schema = json.loads((Path(__file__).resolve().parents[1] / "schema/project.schema.json").read_text())
    unknown = _doc({**_item("callout"), "componentId": "unknown"})
    with pytest.raises((ValidationError, jsonschema.ValidationError)):
        project.from_dict(unknown)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unknown, schema)
    poison = _doc(_item("callout"))
    poison["tracks"][1]["items"][0]["props"]["execute"] = "javascript:alert(1)"
    with pytest.raises((ValidationError, jsonschema.ValidationError)):
        project.from_dict(poison)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(poison, schema)
    unsafe_color = _doc(_item("callout", {"label": "x", "body": "y", "accent": "red"}))
    with pytest.raises((ValidationError, jsonschema.ValidationError)):
        project.from_dict(unsafe_color)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unsafe_color, schema)
    unknown_type = _doc({**_item("callout"), "type": "future-graphic"})
    with pytest.raises(ValidationError):
        project.from_dict(unknown_type)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unknown_type, schema)


def test_legacy_text_item_is_unchanged_when_graphics_are_present() -> None:
    raw = _doc(_item("chapter-card"))
    raw["tracks"][1]["items"].append({"id": "legacy", "text": "Legacy", "preset": "hook",
                                       "pos": {"x": 0.5, "y": 0.2}, "tlStartF": 200, "durF": 30})
    model = project.from_dict(raw)
    assert isinstance(model.tracks[1].items[1], project.TextItem)
    project.validate(model)


@pytest.mark.parametrize("component_id", [d.id for d in graphics.DEFINITIONS])
def test_compile_uses_registry_renderer_for_each_component(component_id: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(compile_stage, "_probe_has_audio", lambda _path: False)
    monkeypatch.setattr(compile_stage, "_probe_transfer", lambda _path: "")
    plan = compile_stage.build_plan(_doc(_item(component_id)), project_dir=tmp_path)
    assert "drawtext=" in plan.filter_complex
    assert "drawbox=" in plan.filter_complex
    assert {layer["kind"] for layer in graphics.graphic_layout(component_id)["layers"]} >= {"panel"}
    assert f"graphic{component_id}" not in plan.filter_complex  # file names are enumerated, not doc IDs


def test_graphic_props_are_copied_and_not_mutated() -> None:
    props = graphics.default_props("simple-chart")
    original = copy.deepcopy(props)
    result = graphics.validate_props("simple-chart", props)
    result["labels"].append("D")
    assert props == original


def test_preview_and_final_style_descriptor_is_deterministic() -> None:
    assert graphics.definition("simple-chart").prop_schema == {
        "title": "text", "labels": "text-list", "values": "number-list",
    }
    assert graphics.definition("simple-chart").preview_renderer == "player:trusted-registry"
    assert graphics.graphic_style("callout", graphics.default_props("callout")) == {
        "foreground": "#4cc9f0", "border": "#4cc9f0", "background": "#121619",
    }
    with pytest.raises(ValidationError):
        graphics.validate_props("callout", {"label": "x", "body": "y", "accent": "red"})


@pytest.mark.parametrize("component_id", [d.id for d in graphics.DEFINITIONS])
def test_preview_geometry_and_final_filter_primitives_are_the_same(component_id: str,
                                                                    monkeypatch: pytest.MonkeyPatch,
                                                                    tmp_path: Path) -> None:
    """The final filter is an expansion of the exact pixel descriptor, not a second layout."""
    monkeypatch.setattr(compile_stage, "_probe_has_audio", lambda _path: False)
    monkeypatch.setattr(compile_stage, "_probe_transfer", lambda _path: "")
    raw = _doc({**_item(component_id), "pos": {"x": 0.5, "y": 0.5}, "scale": 0.7})
    model = project.from_dict(raw)
    item = model.tracks[1].items[0]
    assert isinstance(item, project.GraphicItem)
    descriptor = graphics.graphic_geometry(component_id, item.props, 1920, 1080, item.pos, item.scale)
    plan = compile_stage.build_plan(raw, project_dir=tmp_path)
    filters = plan.filter_complex
    box = descriptor["box"]
    assert f"drawbox=x={box['x']}:y={box['y']}:w={box['width']}:h={box['height']}" in filters
    for layer in descriptor["layers"]:
        if layer["kind"] == "panel":
            assert f"t={descriptor['style']['borderPx']}" in filters
            assert f"@{descriptor['style']['backgroundAlpha']}" in filters
        if layer["kind"] in {"rail", "bars"}:
            assert f"drawbox=x={layer['x']}:y={layer['y']}" in filters or layer["kind"] == "bars"
        if layer["kind"] == "text" and item.props[layer["field"]]:
            value = graphics.fit_graphic_text(str(item.props[layer["field"]]), layer["maxChars"])
            spacing = max(0, layer["lineHeightPx"] - layer["fontPx"])
            assert f"fontsize={layer['fontPx']}:line_spacing={spacing}" in filters
            assert f":x={layer['x']}:y={layer['y']}:" in filters
            assert len(value) <= layer["maxChars"]
        if layer["kind"] == "labels":
            spacing = max(0, layer["lineHeightPx"] - layer["fontPx"])
            assert f"fontsize={layer['fontPx']}:line_spacing={spacing}" in filters
            assert f":x={layer['x']}:y={layer['y']}:" in filters
            assert len(graphics.fit_graphic_text(" · ".join(item.props["labels"]), layer["maxChars"])) <= layer["maxChars"]
        if layer["kind"] == "bars":
            baseline = layer["baseline"]
            assert f"drawbox=x={baseline['x']}:y={baseline['y']}:w={baseline['width']}:h={baseline['height']}" in filters
        for bar in layer.get("bars", []):
            assert f"drawbox=x={bar['x']}:y={bar['y']}:w={bar['width']}:h={bar['height']}" in filters


@pytest.mark.parametrize("component_id", [d.id for d in graphics.DEFINITIONS])
def test_long_graphic_text_uses_deterministic_metric_and_ellipsis(component_id: str) -> None:
    props = graphics.default_props(component_id)
    for field in graphics.definition(component_id).required:
        if isinstance(props[field], str) and not (component_id == "callout" and field == "accent"):
            props[field] = "A deliberately long reviewable graphic string " * 4
    descriptor = graphics.graphic_geometry(component_id, props, 1920, 1080, {"x": 0.5, "y": 0.5}, 1)
    for layer in descriptor["layers"]:
        if "field" in layer and layer["kind"] == "text":
            clipped = graphics.fit_graphic_text(props[layer["field"]], layer["maxChars"])
            assert clipped.endswith("…")
            assert len(clipped) == layer["maxChars"]


@pytest.mark.parametrize("component_id", [d.id for d in graphics.DEFINITIONS])
def test_graphic_api_save_edit_and_stale_revision_conflict(component_id: str) -> None:
    """Exercise the real project endpoint, including the server-side baseRevision gate."""
    name = f"graphics-{component_id}"
    raw = _doc(_item(component_id))
    raw["name"] = name
    raw["tracks"][0]["clips"][0]["media"]["source"] = "media/clip.mp4"
    edited = copy.deepcopy(raw)
    props = edited["tracks"][1]["items"][0]["props"]
    if component_id == "simple-chart":
        props["title"] = "Edited chart"
    elif component_id == "callout":
        props["label"] = "Edited callout"
    else:
        field = next(field for field in graphics.definition(component_id).required if field != "accent")
        props[field] = "Edited graphic"

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=studio_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
            first = await client.put(f"/api/project/{name}", json={"project": raw, "baseRevision": 0})
            second = await client.put(f"/api/project/{name}", json={"project": edited, "baseRevision": 1})
            stale = await client.put(f"/api/project/{name}", json={"project": raw, "baseRevision": 1})
            current = await client.get(f"/api/project/{name}")
            return first, second, stale, current

    try:
        first, second, stale, current = asyncio.run(scenario())
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["revision"] == 2
        assert stale.status_code == 409
        assert stale.json() == {"currentRevision": 2, "error": "The request conflicts with the current state.", "code": "conflict"}
        assert current.status_code == 200
        assert current.json()["project"]["tracks"][1]["items"][0]["props"] == edited["tracks"][1]["items"][0]["props"]
    finally:
        (config.PROJECTS_DIR / f"{name}.json").unlink(missing_ok=True)


@pytest.mark.parametrize("component_id", [d.id for d in graphics.DEFINITIONS])
def test_graphic_lifecycle_save_reload_undo_redo_and_revision_conflict(component_id: str) -> None:
    raw = _doc(_item(component_id))
    original = project.from_dict(raw)
    project.validate(original)
    changed = copy.deepcopy(raw)
    changed["tracks"][1]["items"][0]["props"] = graphics.default_props(component_id)
    props = changed["tracks"][1]["items"][0]["props"]
    if component_id == "simple-chart":
        props["title"] = "Edited chart"
    else:
        field = graphics.definition(component_id).required[0]
        props[field] = f"{props[field]} edited" if props[field] else "Edited"
    edited = project.from_dict(changed)
    project.validate(edited)
    saved = json.loads(json.dumps(project.to_dict(edited)))
    reloaded = project.from_dict(saved)
    assert project.to_dict(reloaded) == saved
    # A history checkpoint restores the prior authoritative document and redo restores the edit.
    assert project.to_dict(original) != saved
    assert project.to_dict(edited) == project.to_dict(reloaded)
    # A stale revision cannot overwrite the current graphic document through project validation.
    stale = dict(saved, revision=saved["revision"] - 1)
    stale_model = project.from_dict(stale)
    project.validate(stale_model)
    assert stale_model.revision < reloaded.revision  # the API revision gate rejects this stale base
