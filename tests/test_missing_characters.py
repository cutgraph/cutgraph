import asyncio
import json
from pathlib import Path

import httpx
import pytest

import config
from pipeline import cast, library
from scripts import studio_app


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    return tmp_path


def test_unavailable_profile_survives_edit_and_save(state: Path) -> None:
    workspace = library.create_workspace("Cast fixture")["id"]
    stored = {
        "id": "stored-character", "name": "Stored character", "rig": "retired-rig",
        "palette": "stored-palette", "art": ["unknown-art"], "approved": False,
        "expressions": [{"id": "stored-look", "spec": {"unknown-part": "stored-option"}, "extra": [1, None]}],
        "createdAt": "stored-time", "extension": {"nested": ["unchanged", 1.25, None]},
    }
    path = config.WORKSPACES_DIR / workspace / "cast.json"
    path.write_text(json.dumps({"default": stored["id"], "characters": [stored]}), encoding="utf-8")
    loaded = cast.video_cast(workspace)
    assert next(c for c in loaded["characters"] if c["id"] == stored["id"]) == {
        **stored, "workspaceId": workspace, "available": False,
    }
    original = json.dumps({k: v for k, v in stored.items() if k != "name"}).encode()
    cast.update_character(stored["id"], name="Renamed character")
    saved = json.loads(path.read_text(encoding="utf-8"))["characters"][0]
    assert saved == {**stored, "name": "Renamed character"}
    assert json.dumps({k: v for k, v in saved.items() if k != "name"}).encode() == original
    other = cast.create_character(workspace, "Other")
    cast.delete_character(other["id"])
    assert json.loads(path.read_text(encoding="utf-8"))["characters"][0] == saved


def test_opaque_top_level_data_survives_cast_writes(state: Path) -> None:
    path = config.WORKSPACES_DIR / "brand" / "cast.json"
    path.parent.mkdir(parents=True)
    expressions = b'[ {"id":"stored-look", "spec": {"unknown-part":"stored-option"}} ]'
    extension = b'{ "nested": [1.2500, null, "\\u0061"], "text": "}:," }'
    path.write_bytes(b'{"expressions":' + expressions + b', "extension":' + extension + b'}')
    assert cast.video_cast(None)["characters"][0]["expressions"] == []
    made = cast.create_character("brand", "Other")
    for action in (lambda: cast.update_character(made["id"], name="Renamed"),
                   lambda: cast.delete_character(made["id"])):
        assert expressions in path.read_bytes() and extension in path.read_bytes()
        action()
    assert expressions in path.read_bytes() and extension in path.read_bytes()


def test_incomplete_unavailable_profiles_allow_rename_and_create(state: Path) -> None:
    path = config.WORKSPACES_DIR / "brand" / "cast.json"
    path.parent.mkdir(parents=True)
    stored = {"id": "stored-character", "rig": "retired-rig"}
    path.write_text(json.dumps({"characters": [stored]}), encoding="utf-8")
    assert cast.video_cast(None)["characters"][1] == {**stored, "workspaceId": "brand", "available": False}
    cast.create_character("brand", "Other")
    cast.update_character(stored["id"], name="Renamed character")
    assert json.loads(path.read_text(encoding="utf-8"))["characters"][1] == {**stored, "name": "Renamed character"}


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_revision_gated_save_preserves_unavailable_character_references(state: Path, method: str) -> None:
    path = config.WORKSPACES_DIR / "brand" / "cast.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"characters": [{
        "id": "stored-character", "rig": "retired-rig", "name": "Stored character",
        "expressions": [{"id": "stored-look", "spec": {"unknown-part": "stored-option"}}],
    }]}), encoding="utf-8")
    look = {"character": "stored-character", "expression": "stored-look", "pose": "stored-pose"}
    doc = {
        "schemaVersion": 3, "name": "stored-video", "revision": 0, "fps": 30,
        "width": 320, "height": 180, "tracks": [],
        "scenes": [
            {"id": "stage", "title": "Stage", "role": "Beat", "startWord": 0,
             "shot": {"mode": "animation", **look, "costar": dict(look), "ensemble": [dict(look)]}},
            {"id": "cameo", "title": "Cameo", "role": "Beat", "startWord": 1,
             "shot": {"mode": "cameo", **look}},
        ],
    }

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            url = "/api/project/stored-video"
            created = await client.put(url, json={"project": doc, "baseRevision": 0})
            assert created.status_code == 200, created.text
            response = await client.get(url)
            assert response.status_code == 200, response.text
            loaded = response.json()["project"]
            before = json.dumps([s["shot"] for s in loaded["scenes"]]).encode()
            if method == "PUT":
                loaded["scenes"][0]["shot"]["headline"] = "Edited headline"
                payload = {"project": loaded, "baseRevision": loaded["revision"]}
            else:
                payload = {"ops": [{"op": "add", "path": "/scenes/0/shot/headline", "value": "Edited headline"}],
                           "baseRevision": loaded["revision"]}
            saved = await client.request(method, url, json=payload)
            assert saved.status_code == 200, saved.text
            stale = await client.request(method, url, json=payload)
            assert stale.status_code == 409
            stored = json.loads((config.PROJECTS_DIR / "stored-video.json").read_text(encoding="utf-8"))
            assert stored["revision"] == loaded["revision"] + 1
            assert stored["scenes"][0]["shot"].pop("headline") == "Edited headline"
            assert json.dumps([s["shot"] for s in stored["scenes"]]).encode() == before
            assert cast.resolve_shot(stored["scenes"][0]["shot"], cast.video_cast(None))[0]["character"] == "retired-rig"

    asyncio.run(exercise())


def test_malformed_stored_expressions_never_break_rendering() -> None:
    profile = {"id": "stored-character", "name": "Stored", "rig": "retired-rig", "workspaceId": "brand",
               "expressions": [None, "loose", {"name": "No id"}, {"id": "stored-look", "spec": {"storedPart": "x"}}]}
    look, custom = cast.resolve_shot({"mode": "animation", "character": "stored-character", "expression": "stored-look"},
                                     {"characters": [profile], "default": "stored-character"})
    assert look["character"] == "retired-rig" and custom is None
