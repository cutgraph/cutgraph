"""Preview/export staging parity on synthetic prose covered by the project licence. Independent single-scene layout, camera and screen cases, with screen images resolved through the video's workspace library before the brand kit. Regenerate with tests/fixtures/generate.py; the TypeScript consumer is an independent check."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from pipeline import library, project as project_mod, scene_render

FIXTURE = Path(__file__).parent / "fixtures" / "scene_specs_parity_staging.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_export_scene_specs_match_the_staging_parity_fixture() -> None:
    fx = _fixture()
    for case in fx["cases"]:
        assert len(case["project"]["scenes"]) == 1
        project_mod.validate(project_mod.from_dict(case["project"]))
        assert scene_render.scene_specs(case["project"], fx["words"]) == case["expected"]


def test_the_staging_fixture_covers_the_edge_cases() -> None:
    fx = _fixture()
    shots = [s["shot"] for case in fx["cases"] for s in case["expected"]]
    assert {s.get("layout") for s in shots} == {None, "screen", "title", "signoff"}
    assert {s.get("camera") for s in shots} >= {"push-in", "pull-out"}
    assert any(s.get("layout") and "mode" not in s for case in fx["cases"] for s in (x.get("shot", {}) for x in case["project"]["scenes"])), \
        "a staged footage scene, left out"
    assert any(value is None for case in fx["cases"] for value in case["images"].values()), "a screen asset that resolves to nothing"
    phones = sorted(1 + len(s.get("screen", {}).get("more", [])) for s in shots if s.get("layout") == "screen")
    assert phones == [1, 2, 3], "one, two and three phones"
    assert None in [i for case in fx["cases"] for more in case["moreImages"].values() for i in more], "a phone 2 or 3 that resolves to nothing"
    assert next(c for c in fx["cases"] if c["name"] == "staged-footage")["expected"] == []
    ids = [e["asset"]["id"] for e in fx["library"]]
    assert len(ids) != len(set(ids)), "one asset id in both libraries: the video's workspace wins"


@pytest.fixture
def libraries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fixture's libraries on disk: workspace docs, manifests and the image files themselves."""
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    fx = _fixture()
    for wid in dict.fromkeys(e["workspaceId"] for e in fx["library"]):
        folder = tmp_path / "workspaces" / wid
        (folder / "library").mkdir(parents=True)
        (folder / "workspace.json").write_text(json.dumps({"id": wid, "name": wid}), encoding="utf-8")
        assets = [e["asset"] for e in fx["library"] if e["workspaceId"] == wid]
        (folder / "library" / "assets.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")
        for a in assets:
            (folder / "library" / a["file"]).write_bytes(a["id"].encode())
    return tmp_path


def test_a_screen_image_resolves_into_the_render_request_as_the_preview_resolves_it(libraries: Path) -> None:
    fx = _fixture()
    ws = fx["cases"][0]["project"]["workspaceId"]
    assert library.get_workspace(ws) is not None
    for case, spec in ((case, spec) for case in fx["cases"] for spec in scene_render.scene_specs(case["project"], fx["words"])):
        request = scene_render.render_request(spec, width=1920, height=1080, fps=30, workspace_id=ws)
        if spec["shot"].get("layout") != "screen":
            assert "screen" not in request["shot"]
            continue
        screen, stored = request["shot"]["screen"], spec["shot"]["screen"]
        assert [screen["asset"], *(m["asset"] for m in screen.get("more", []))] == [stored["asset"], *stored.get("more", [])]
        wants = [case["images"][spec["sceneId"]], *case["moreImages"].get(spec["sceneId"], [])]
        for i, (phone, want) in enumerate(zip([screen, *screen.get("more", [])], wants, strict=True)):
            image = phone.get("image")
            if want is None:
                assert image is None, f"{spec['sceneId']} phone {i + 1}: no image, the generated app screen"
            else:
                assert Path(image) == (libraries / "workspaces" / want.replace("/", "/library/")).resolve(), f"{spec['sceneId']} phone {i + 1}"
                assert Path(image).is_file()


def test_the_cache_key_follows_the_image_content_not_the_state_home(libraries: Path) -> None:
    fx = _fixture()
    case = next(c for c in fx["cases"] if c["name"] == "single-screen")
    ws = case["project"]["workspaceId"]
    spec = scene_render.scene_specs(case["project"], fx["words"])[0]
    request = scene_render.render_request(spec, width=1920, height=1080, fps=30, workspace_id=ws)
    key = scene_render.cache_key(request, "v")
    moved = {**request, "shot": {**request["shot"], "screen": {**request["shot"]["screen"],
                                                               "image": "E:/elsewhere/" + Path(request["shot"]["screen"]["image"]).name}}}
    assert scene_render.cache_key(moved, "v") == key, "the same content-addressed file elsewhere"
    brand = {**request, "shot": {**request["shot"], "screen": {"asset": "0f1e2d3c4b5a6978", "image": "x/0f1e2d3c4b5a6978.jpg"}}}
    assert scene_render.cache_key(brand, "v") != key, "another image re-renders"
    unsorted = scene_render.render_request(spec, width=1920, height=1080, fps=30, workspace_id=None)
    assert Path(unsorted["shot"]["screen"]["image"]).parent.parent.name == "brand", "without a Workspace, only the shared library is searched"
    assert scene_render.cache_key(unsorted, "v") != key
    case = next(c for c in fx["cases"] if c["name"] == "missing-image")
    missing = scene_render.scene_specs(case["project"], fx["words"])[0]
    placeholder = scene_render.render_request(missing, width=1920, height=1080, fps=30, workspace_id=ws)
    assert placeholder["shot"]["screen"]["asset"] == missing["shot"]["screen"]["asset"]         and "image" not in placeholder["shot"]["screen"], "not an image: phone 1 draws the generated app screen"
