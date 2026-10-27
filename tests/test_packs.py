"""Synthetic character packs, cast integration and render cache/command parity."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import config
from pipeline import cast, packs, scene_render, thumbnail


def _rig(rig_id: str = "synthetic-rig") -> dict:
    return {
        "id": rig_id, "name": "Synthetic", "owner": "client", "palettes": ["plain"],
        "parts": {"eyes": ["calm", "wide"]}, "expressions": ["neutral"],
        "poses": ["rest"], "moves": ["idle"],
        "labels": {"titles": {"eyes": "Eyes"}, "options": {"eyes": {"calm": "Calm"}}},
        "head": {"rows": 40, "foot": 40, "span": 20, "cols": 22, "centre": 12},
    }


def _pack(dir: Path, name: str = "synthetic", rigs: list[dict] | None = None) -> Path:
    path = dir / name
    path.mkdir()
    (path / "pack.json").write_text(json.dumps({"characters": rigs if rigs is not None else [_rig()]}), encoding="utf-8")
    (path / "index.ts").write_text("export const characters = [];", encoding="utf-8")
    return path


def test_valid_pack_loads_and_missing_directories_are_empty(tmp_path: Path) -> None:
    _pack(tmp_path)
    assert packs.load(tmp_path) == [_rig()]
    assert packs.load(tmp_path / "absent") == []


@pytest.mark.parametrize("change", [
    {"id": "Bad_id"}, {"id": "rig\n"}, {"id": "lens"}, {"name": ""}, {"owner": "other"},
    {"palettes": []}, {"parts": {"eyes": []}}, {"expressions": "neutral"}, {"expressions": []},
    {"poses": None}, {"moves": [1]}, {"labels": {"titles": [], "options": {}}},
    {"head": {"rows": True, "foot": 40, "span": 20, "cols": 22, "centre": 12}},
])
def test_invalid_rigs_skip_whole_pack_with_one_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                                      change: dict) -> None:
    _pack(tmp_path, rigs=[_rig("first-rig"), {**_rig(), **change}])
    assert packs.load(tmp_path) == []
    assert len(caplog.records) == 1
    assert "Skipping character pack synthetic:" in caplog.text


@pytest.mark.parametrize("manifest", ["{", "null", '{"characters": [null]}', '{}'])
def test_malformed_manifests_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture, manifest: str) -> None:
    path = _pack(tmp_path)
    (path / "pack.json").write_text(manifest, encoding="utf-8")
    assert packs.load(tmp_path) == []
    assert len(caplog.records) == 1


def test_pack_ids_clashes_and_sorted_loading(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _pack(tmp_path, "b-later", [_rig("shared-rig"), _rig("later-only")])
    _pack(tmp_path, "a-earlier", [{**_rig("shared-rig"), "name": "Earlier"}])
    _pack(tmp_path, "Bad_id", [_rig("folder-rig")])
    _pack(tmp_path, "duplicates", [_rig("duplicate"), _rig("duplicate")])
    _pack(tmp_path, "valid", [_rig("valid-rig")])
    assert [rig["id"] for rig in packs.load(tmp_path)] == ["valid-rig"]
    assert len(caplog.records) == 4


def test_incomplete_pack_is_ignored_silently(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = _pack(tmp_path)
    (path / "index.ts").unlink()
    (path / "pack.json").write_text("{", encoding="utf-8")
    assert packs.load(tmp_path) == []
    assert packs.files(tmp_path) == [path / "pack.json"]
    assert not caplog.records


def test_files_cover_whole_directory_and_exclude_tests(tmp_path: Path) -> None:
    path = _pack(tmp_path)
    (path / "draw.ts").write_text("", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src" / "extra.ts").write_text("", encoding="utf-8")
    (path / "src" / "test").mkdir()
    (path / "src" / "test" / "draw.test.ts").write_text("", encoding="utf-8")
    (path / "notes.txt").write_text("", encoding="utf-8")
    _pack(tmp_path, "clashing", [_rig("lens")])
    invalid = _pack(tmp_path, "invalid")
    (invalid / "pack.json").write_text("{", encoding="utf-8")
    (invalid / "drawing.json").write_text("{}", encoding="utf-8")
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / "index.ts").write_text("", encoding="utf-8")
    assert [file.relative_to(tmp_path).as_posix() for file in packs.files(tmp_path)] == [
        "clashing/index.ts", "clashing/pack.json", "incomplete/index.ts", "invalid/drawing.json", "invalid/index.ts", "invalid/pack.json",
        "synthetic/draw.ts", "synthetic/index.ts", "synthetic/notes.txt", "synthetic/pack.json", "synthetic/src/extra.ts",
    ]


def test_shared_manifest_parity_cases(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "motion-core" / "test" / "pack-cases.json"
    for index, case in enumerate(json.loads(fixture.read_text(encoding="utf-8"))):
        directory = tmp_path / str(index)
        directory.mkdir()
        for pack in case["packs"]:
            path = _pack(directory, pack["id"], [])
            (path / "pack.json").write_text(json.dumps(pack["manifest"]), encoding="utf-8")
            if not pack.get("complete", True):
                (path / "index.ts").unlink()
        assert [rig["id"] for rig in packs.load(directory)] == case["accepted"], case["name"]


def test_probe_errors_skip_one_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   caplog: pytest.LogCaptureFixture) -> None:
    unreadable = _pack(tmp_path, "unreadable", [_rig("unreadable-rig")])
    valid = _pack(tmp_path, "valid")
    original = Path.is_file

    def probe(path: Path) -> bool:
        if path.parent == unreadable:
            raise PermissionError("synthetic probe failure")
        return original(path)

    monkeypatch.setattr(Path, "is_file", probe)
    assert packs.load(tmp_path) == [_rig()]
    assert cast.rigs_from_packs(tmp_path)["synthetic-rig"]
    assert packs.files(tmp_path) == sorted([valid / "index.ts", valid / "pack.json"])
    assert len(caplog.records) == 3
    assert "synthetic probe failure" in caplog.text


def test_cast_rebuilt_from_pack_metadata_exposes_tuple_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pack(tmp_path)
    monkeypatch.setattr(cast, "RIGS", cast.rigs_from_packs(tmp_path))
    assert cast.RIGS["synthetic-rig"] == {
        "owner": "client", "palettes": ("plain",), "parts": {"eyes": ("calm", "wide")},
        "expressions": ("neutral",), "poses": ("rest",), "moves": ("idle",),
    }
    assert cast.validate_spec({"eyes": "wide"}, "synthetic-rig") == {"eyes": "wide"}
    profile = cast._valid_character({"id": "profile", "rig": "synthetic-rig", "name": "Profile"})
    assert profile is not None and profile["approved"] is False
    assert set(cast.BUILTIN_RIGS) == {"lens"}


def test_pack_sources_metadata_and_paths_invalidate_both_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _pack(tmp_path)
    monkeypatch.setattr(config, "PACKS_DIR", tmp_path)
    scene_before, poster_before = scene_render.motion_core_version(), thumbnail.renderer_version()
    (path / "index.ts").write_text("export const characters = [ ];", encoding="utf-8")
    assert scene_render.motion_core_version() != scene_before
    assert thumbnail.renderer_version() != poster_before
    before = scene_render.motion_core_version()
    (path / "pack.json").write_text(json.dumps({"characters": [{**_rig(), "name": "Renamed"}]}), encoding="utf-8")
    assert scene_render.motion_core_version() != before
    before = scene_render.motion_core_version()
    path.rename(tmp_path / "renamed")
    assert scene_render.motion_core_version() != before


def test_shared_helper_without_manifest_invalidates_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _pack(tmp_path)
    (path / "index.ts").write_text("import { size } from '../shared/helper.ts'; export const characters = [];", encoding="utf-8")
    shared = tmp_path / "shared"
    shared.mkdir()
    helper = shared / "helper.ts"
    helper.write_text("export const size = 100;", encoding="utf-8")
    monkeypatch.setattr(config, "PACKS_DIR", tmp_path)
    scene_before, poster_before = scene_render.motion_core_version(), thumbnail.renderer_version()
    helper.write_text("export const size = 400;", encoding="utf-8")
    assert scene_render.motion_core_version() != scene_before
    assert thumbnail.renderer_version() != poster_before


def test_nested_motion_core_sources_invalidate_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    core = tmp_path / "motion-core"
    shutil.copytree(scene_render.MOTION_CORE / "src", core / "src")
    shutil.copytree(scene_render.MOTION_CORE / "scripts", core / "scripts")
    shutil.copytree(scene_render.MOTION_CORE / "bin", core / "bin")
    helper = core / "src" / "shared" / "drawing.json"
    helper.parent.mkdir()
    helper.write_text('{"size": 100}', encoding="utf-8")
    monkeypatch.setattr(scene_render, "MOTION_CORE", core)
    monkeypatch.setattr(scene_render, "RENDERER", core / "bin" / scene_render.RENDERER.name)
    before = scene_render.motion_core_version()
    helper.write_text('{"size": 400}', encoding="utf-8")
    assert scene_render.motion_core_version() != before


@pytest.mark.parametrize("kind", ["valid", "invalid", "clashing"])
def test_runtime_dependencies_of_complete_packs_invalidate_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                kind: str) -> None:
    path = _pack(tmp_path, rigs=[_rig("lens" if kind == "clashing" else "synthetic-rig")])
    if kind == "invalid":
        (path / "pack.json").write_text("{", encoding="utf-8")
    dependency = path / "drawing.json"
    dependency.write_text('{"size": 1}', encoding="utf-8")
    monkeypatch.setattr(config, "PACKS_DIR", tmp_path)
    scene_before, poster_before = scene_render.motion_core_version(), thumbnail.renderer_version()
    dependency.write_text('{"size": 2}', encoding="utf-8")
    assert scene_render.motion_core_version() != scene_before
    assert thumbnail.renderer_version() != poster_before
    before = scene_render.motion_core_version()
    (path / "test").mkdir()
    (path / "test" / "notes.txt").write_text("test only", encoding="utf-8")
    assert scene_render.motion_core_version() == before


@pytest.mark.parametrize("renderer, suffix", [(scene_render, ".mp4"), (thumbnail, ".png")])
@pytest.mark.parametrize("present", [True, False])
def test_render_hosts_pass_packs_only_when_directory_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           renderer, suffix: str, present: bool) -> None:
    pack_dir = tmp_path / "rigs"
    if present:
        pack_dir.mkdir()
    monkeypatch.setattr(config, "PACKS_DIR", pack_dir)
    monkeypatch.setattr(renderer, "node_path", lambda: Path("node"))
    if renderer is scene_render:
        monkeypatch.setattr(scene_render, "tool_path", lambda _name: "encoder")
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        commands.append(command)
        assert kwargs["input"] == "{}"
        Path(command[3]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, '{"event":"done"}\n', "")

    monkeypatch.setattr(renderer.subprocess, "run", run)
    dest = tmp_path / f"render{suffix}"
    render = scene_render.render_scene if renderer is scene_render else thumbnail.render_thumbnail
    assert render({}, dest) == {"event": "done"}
    assert dest.read_bytes() == b"rendered"
    if present:
        assert commands[0][-2:] == ["--packs", str(pack_dir)]
    else:
        assert "--packs" not in commands[0]
