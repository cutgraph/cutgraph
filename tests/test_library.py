"""pipeline.library (workspaces + shared asset library) and its studio_app routes.

Tiny real fixtures (ffmpeg lavfi) exercise the actual ffprobe/thumbnail path rather than mocking
it, matching tests/test_proxy.py's convention. HTTP-level tests use an
httpx.AsyncClient + STATE_HOME-monkeypatch pattern.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from pipeline import cast as cast_mod
from pipeline import library
from pipeline import project as project_mod
from scripts import studio_app
from studio.routes import workspaces

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


@pytest.fixture()
def studio_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "outputs" / "projects")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_video(dest: Path, *, duration: float = 1.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=64x64:rate=10:duration={duration}",
         "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=8000", "-t", str(duration),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)],
        check=True, capture_output=True, text=True,
    )


def _make_image(dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:size=32x32", "-frames:v", "1", str(dest)],
        check=True, capture_output=True, text=True,
    )


def _make_audio(dest: Path, *, duration: float = 1.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=8000", "-t", str(duration),
         str(dest)],
        check=True, capture_output=True, text=True,
    )


def _write_project(slug: str, *, workspace_id: str | None = None, media_source: str = "data/source/clip.mp4") -> None:
    doc = {
        "schemaVersion": project_mod.CURRENT_SCHEMA_VERSION, "name": slug, "fps": config.DEFAULT_FPS,
        "width": 1920, "height": 1080, "revision": 1, "transcript": None,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [{
            "id": "c1", "media": {"source": media_source},
            "srcInF": 0, "srcOutF": 90, "tlStartF": 0,
        }]}],
    }
    if workspace_id is not None:
        doc["workspaceId"] = workspace_id
    path = config.PROJECTS_DIR / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


# ── pipeline.library: workspaces ─────────────────────────────────────────────────

def test_create_list_rename_workspace(studio_state: Path) -> None:
    created = library.create_workspace("Kitchen equipment")
    assert created["name"] == "Kitchen equipment"
    assert created["id"].startswith("kitchen-equipment-")
    assert created["createdAt"] == created["updatedAt"]

    listed = library.list_workspaces()
    ids = {w["id"] for w in listed}
    assert created["id"] in ids
    assert library.BUILTIN_WORKSPACE_ID in ids  # brand always exists

    renamed = library.rename_workspace(created["id"], "Kitchen utensils")
    assert renamed["name"] == "Kitchen utensils"
    assert renamed["updatedAt"] >= created["updatedAt"]
    fetched = library.get_workspace(created["id"])
    assert fetched is not None and fetched["name"] == "Kitchen utensils"


def test_create_workspace_rejects_empty_name(studio_state: Path) -> None:
    with pytest.raises(ValueError):
        library.create_workspace("   ")


def test_rename_unknown_workspace_raises(studio_state: Path) -> None:
    with pytest.raises(FileNotFoundError):
        library.rename_workspace("does-not-exist", "x")


def test_brand_workspace_is_always_resolvable(studio_state: Path) -> None:
    workspace = library.get_workspace(library.BUILTIN_WORKSPACE_ID)
    assert workspace is not None
    assert workspace["id"] == "brand"


def test_concurrent_first_listings_create_one_brand_workspace(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Home and the workspace switcher list workspaces at once on a fresh state home."""
    threads = 16
    for attempt in range(5):
        monkeypatch.setattr(config, "WORKSPACES_DIR", studio_state / f"workspaces-{attempt}")
        barrier = threading.Barrier(threads)

        def list_after_barrier(_: int) -> list[dict]:
            barrier.wait()
            return library.list_workspaces()

        with ThreadPoolExecutor(threads) as pool:
            listings = list(pool.map(list_after_barrier, range(threads)))
        assert all([w["id"] for w in listed] == ["brand"] for listed in listings)
        assert len({listed[0]["createdAt"] for listed in listings}) == 1, "one Brand workspace, created once"
        assert [p.name for p in config.WORKSPACES_DIR.iterdir()] == ["brand"]
        assert [p.name for p in (config.WORKSPACES_DIR / "brand").iterdir()] == ["workspace.json"], "no temp file left"


def _refuse_reads(monkeypatch: pytest.MonkeyPatch, path: Path, times: int) -> list[int]:
    """Make `path` refuse `times` reads the way Windows does while a replace or a scanner holds it."""
    refused = [0]
    read_text = Path.read_text

    def flaky(self: Path, *args, **kwargs) -> str:
        if self == path and refused[0] < times:
            refused[0] += 1
            raise PermissionError(13, "The process cannot access the file", str(self))
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky)
    return refused


def test_a_briefly_locked_brand_workspace_is_read_not_recreated(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library.set_brand(library.BUILTIN_WORKSPACE_ID, {"name": "Northwind", "accent": "#123456"})
    saved = json.loads(library._workspace_path(library.BUILTIN_WORKSPACE_ID).read_text(encoding="utf-8"))
    refused = _refuse_reads(monkeypatch, library._workspace_path(library.BUILTIN_WORKSPACE_ID), 3)

    assert library.ensure_brand_workspace() == saved
    assert refused[0] == 3
    assert json.loads(library._workspace_path(library.BUILTIN_WORKSPACE_ID).read_text(encoding="utf-8")) == saved


def test_a_brand_workspace_that_stays_locked_fails_instead_of_being_overwritten(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    library.set_brand(library.BUILTIN_WORKSPACE_ID, {"name": "Northwind", "accent": "#123456"})
    path = library._workspace_path(library.BUILTIN_WORKSPACE_ID)
    saved = path.read_bytes()
    monkeypatch.setattr(library, "_REPLACE_RETRY_DELAYS_S", (0.0, 0.0))
    _refuse_reads(monkeypatch, path, 10)

    with pytest.raises(PermissionError):
        library.ensure_brand_workspace()
    assert path.read_bytes() == saved


def test_a_briefly_locked_asset_manifest_is_read_not_emptied(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library.ensure_brand_workspace()
    library._write_manifest(library.BUILTIN_WORKSPACE_ID, [{"id": "a1", "kind": "image"}])
    _refuse_reads(monkeypatch, library._assets_manifest_path(library.BUILTIN_WORKSPACE_ID), 2)

    assert library._read_manifest(library.BUILTIN_WORKSPACE_ID) == [{"id": "a1", "kind": "image"}]


def test_concurrent_atomic_writes_to_one_path_both_land(studio_state: Path) -> None:
    path = studio_state / "doc" / "doc.json"
    for attempt in range(50):
        barrier = threading.Barrier(2)

        def write(writer: int) -> None:
            barrier.wait()
            library._atomic_write_json(path, {"writer": writer, "attempt": attempt})

        with ThreadPoolExecutor(2) as pool:
            list(pool.map(write, range(2)))
        assert json.loads(path.read_text(encoding="utf-8")) in ({"writer": 0, "attempt": attempt},
                                                                  {"writer": 1, "attempt": attempt})
    assert [p.name for p in path.parent.iterdir()] == ["doc.json"], "no temp file left"


def test_atomic_write_waits_out_a_reader_holding_the_file(studio_state: Path) -> None:
    """Windows refuses os.replace onto a file another thread has open; the write retries past it."""
    path = studio_state / "doc.json"
    library._atomic_write_json(path, {"v": 1})
    with ThreadPoolExecutor(1) as pool, path.open(encoding="utf-8"):
        pending = pool.submit(library._atomic_write_json, path, {"v": 2})
        time.sleep(0.02)
    pending.result()
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}


# ── pipeline.library: the workspace brand ─────────────────────────────────

def test_a_workspace_brand_defaults_then_sets_and_resets(studio_state: Path) -> None:
    ours = {"name": config.BRAND_EYEBROW, "accent": config.BRAND_ACCENT}
    scene_ts = (Path(__file__).resolve().parents[1] / "motion-core" / "src" / "scene.ts").read_text(encoding="utf-8")
    assert (f"export const EYEBROW = '{ours['name']}';" in scene_ts and f"export const ACCENT = '{ours['accent']}';" in scene_ts
            and (library.BRAND_EYEBROW, library.BRAND_ACCENT) == (ours["name"], ours["accent"])), "motion-core's default, mirrored"
    styles = (config.CODE_ROOT / "ui" / "src" / "v3" / "styles.css").read_text(encoding="utf-8")
    assert f"--yellow: {ours['accent']};" in styles, "the default matches the UI accent token"
    for workspace in (None, "unsorted", "no-such-workspace", "../escape", library.BUILTIN_WORKSPACE_ID):
        assert library.workspace_brand(workspace) == ours, workspace
    assert not (studio_state / "workspaces").exists(), "reading a brand writes nothing"
    garden = library.create_workspace("  Kitchen   Table ")["id"]
    assert library.workspace_brand(garden) == ours, "workspace names do not implicitly set a brand"
    library.rename_workspace(garden, "Garden")
    assert library.workspace_brand(garden) == ours

    doc = library.set_brand(garden, {"name": "  Team   Notes ", "accent": "#2BB3A3"})
    assert doc["brand"] == {"name": "Team Notes", "accent": "#2bb3a3"} and doc["name"] == "Garden"
    assert library.workspace_brand(garden) == {"name": "Team Notes", "accent": "#2bb3a3"}
    library.rename_workspace(garden, "Museum Visit")
    assert library.workspace_brand(garden)["name"] == "Team Notes", "a set name stays through a rename"
    library.set_brand(garden, {"name": ""})
    assert library.workspace_brand(garden) == {"name": "", "accent": "#2bb3a3"}
    doc = library.set_brand(garden, {"accent": None})
    assert "brand" not in doc and library.workspace_brand(garden) == ours

    library.set_brand(library.BUILTIN_WORKSPACE_ID, {"accent": "#79d5df"})
    assert library.workspace_brand(None) == {"name": "", "accent": "#79d5df"}, "unsorted videos carry the kit's"
    for patch, message in (({"name": "X" * 41}, "longer than 40"), ({"accent": "red"}, "#rrggbb"),
                           ({"accent": "#2bb3a3aa"}, "#rrggbb"), ({"name": 3}, "must be a string"), ({"font": "x"}, "unknown fields")):
        with pytest.raises(library.LibraryError, match=message):
            library.set_brand(garden, patch)
    with pytest.raises(FileNotFoundError):
        library.set_brand("does-not-exist", {"name": "X"})


def test_http_workspace_brand_reads_and_patches(studio_state: Path) -> None:
    garden = library.create_workspace("Garden")["id"]

    async def scenario() -> list[httpx.Response]:
        async with _client() as client:
            return [
                await client.get(f"/api/workspaces/{garden}"),
                await client.patch(f"/api/workspaces/{garden}", json={"brand": {"name": "MUSEUM VISIT", "accent": "#2bb3a3"}}),
                await client.get(f"/api/cast?workspace={garden}"),
                await client.patch(f"/api/workspaces/{garden}", json={"name": "Museum Visit", "brand": {"name": None}}),
                await client.patch(f"/api/workspaces/{garden}", json={"brand": {"accent": "gold"}}),
                await client.patch(f"/api/workspaces/{garden}", json={"brand": "GARDEN"}),
                await client.patch(f"/api/workspaces/{garden}", json={}),
                await client.patch("/api/workspaces/does-not-exist", json={"brand": {"name": "X"}}),
                await client.get("/api/workspaces/unsorted"),
            ]

    before, set_, cast, renamed, bad_accent, bad_brand, empty, unknown, unsorted = _run(scenario())
    assert before.json()["workspace"]["brand"] == {"name": "", "accent": config.BRAND_ACCENT}
    assert set_.status_code == 200 and set_.json()["brand"] == {"name": "MUSEUM VISIT", "accent": "#2bb3a3"}
    assert cast.json()["brand"] == {"name": "MUSEUM VISIT", "accent": "#2bb3a3"}, "the video cast carries it"
    assert renamed.json()["name"] == "Museum Visit"
    assert renamed.json()["brand"] == {"name": "", "accent": "#2bb3a3"}, "reset to the config default"
    assert [r.status_code for r in (bad_accent, bad_brand, empty, unknown)] == [400, 400, 400, 404]
    assert unsorted.json()["workspace"]["brand"] == {"name": "", "accent": config.BRAND_ACCENT}


# ── pipeline.library: traversal refusal ──────────────────────────────────────────

@pytest.mark.parametrize("bad_id", ["../etc", "..", "a/b", "a\\b", "", "a.b", "a b", "a" * 201])
def test_get_workspace_rejects_unsafe_ids(studio_state: Path, bad_id: str) -> None:
    with pytest.raises(ValueError):
        library.get_workspace(bad_id)


def test_workspace_dir_never_escapes_workspaces_dir(studio_state: Path) -> None:
    with pytest.raises(ValueError):
        library._workspace_dir("../../windows")


@pytest.mark.parametrize("bad_id", ["not-hex", "abcd", "A1B2C3D4E5F6A7B8", ""])
def test_asset_id_validation_rejects_non_hex16(studio_state: Path, bad_id: str) -> None:
    workspace = library.create_workspace("W")
    with pytest.raises(ValueError):
        library.get_asset(workspace["id"], bad_id)


# ── pipeline.library: import, dedupe, tags, delete, thumbs ───────────────────────

def test_import_video_asset_probes_and_thumbnails(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Broll")
    src = tmp_path / "upload" / "clip.mp4"
    src.parent.mkdir(parents=True)
    _make_video(src, duration=1.0)

    asset, deduped = library.import_asset(workspace["id"], src, display_name="clip.mp4")
    assert deduped is False
    assert asset["kind"] == "video"
    assert asset["id"] == asset["sha256"][:16]
    assert asset["width"] == 64 and asset["height"] == 64
    assert asset["durationS"] > 0
    assert asset["tags"] == []
    assert not src.exists()  # consumed (moved into the library)

    file_path = library.asset_file_path(workspace["id"], asset["id"])
    assert file_path is not None and file_path.is_file()
    thumb_path = library.asset_thumb_path(workspace["id"], asset["id"])
    assert thumb_path is not None and thumb_path.is_file() and thumb_path.stat().st_size > 0


def test_import_image_asset_probes_dimensions_and_thumbnails(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Photos")
    src = tmp_path / "upload" / "photo.png"
    src.parent.mkdir(parents=True)
    _make_image(src)

    asset, deduped = library.import_asset(workspace["id"], src, display_name="photo.png")
    assert deduped is False
    assert asset["kind"] == "image"
    assert asset["width"] == 32 and asset["height"] == 32
    assert "durationS" not in asset

    thumb_path = library.asset_thumb_path(workspace["id"], asset["id"])
    assert thumb_path is not None and thumb_path.is_file()


def test_import_audio_asset_has_no_thumb(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Music")
    src = tmp_path / "upload" / "bed.wav"
    src.parent.mkdir(parents=True)
    _make_audio(src, duration=1.0)

    asset, _ = library.import_asset(workspace["id"], src, display_name="bed.wav")
    assert asset["kind"] == "audio"
    assert asset["durationS"] > 0
    assert "width" not in asset and "height" not in asset
    assert library.asset_thumb_path(workspace["id"], asset["id"]) is None


def test_import_dedupes_identical_bytes(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Broll")
    src1 = tmp_path / "a.png"
    _make_image(src1)
    payload = src1.read_bytes()
    src2 = tmp_path / "b.png"
    src2.write_bytes(payload)

    first, deduped1 = library.import_asset(workspace["id"], src1, display_name="a.png")
    second, deduped2 = library.import_asset(workspace["id"], src2, display_name="b.png")
    assert deduped1 is False
    assert deduped2 is True
    assert first["id"] == second["id"]
    assert len(library.list_assets(workspace["id"])) == 1


def test_import_rejects_unsupported_extension(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Broll")
    src = tmp_path / "malware.exe"
    src.write_bytes(b"no")
    with pytest.raises(ValueError):
        library.import_asset(workspace["id"], src, display_name="malware.exe")
    assert not src.exists()  # rejected uploads are still cleaned up


def test_import_into_unknown_workspace_raises(studio_state: Path, tmp_path: Path) -> None:
    src = tmp_path / "photo.png"
    _make_image(src)
    with pytest.raises(FileNotFoundError):
        library.import_asset("does-not-exist", src, display_name="photo.png")


def test_update_asset_name_and_tags(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Broll")
    src = tmp_path / "photo.png"
    _make_image(src)
    asset, _ = library.import_asset(workspace["id"], src, display_name="photo.png")

    updated = library.update_asset(workspace["id"], asset["id"], tags=["garden", "garden", " seedling "])
    assert updated["tags"] == ["garden", "seedling"]

    renamed = library.update_asset(workspace["id"], asset["id"], name="Better Name.png")
    assert renamed["name"] == "Better Name.png"
    assert renamed["tags"] == ["garden", "seedling"]  # untouched by a name-only update


def test_update_unknown_asset_raises(studio_state: Path) -> None:
    workspace = library.create_workspace("Broll")
    with pytest.raises(FileNotFoundError):
        library.update_asset(workspace["id"], "0" * 16, name="x")


def test_delete_asset_removes_file_and_thumb(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Broll")
    src = tmp_path / "photo.png"
    _make_image(src)
    asset, _ = library.import_asset(workspace["id"], src, display_name="photo.png")
    file_path = library.asset_file_path(workspace["id"], asset["id"])
    assert file_path is not None
    thumb_path = library._thumbs_dir(workspace["id"]) / f"{asset['id']}.jpg"
    assert file_path.is_file() and thumb_path.is_file()

    assert library.delete_asset(workspace["id"], asset["id"]) is True
    assert not file_path.exists()
    assert not thumb_path.exists()
    assert library.get_asset(workspace["id"], asset["id"]) is None
    assert library.delete_asset(workspace["id"], asset["id"]) is False  # already gone


def test_list_assets_filters_by_kind(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Mixed")
    image_src, video_src = tmp_path / "a.png", tmp_path / "b.mp4"
    _make_image(image_src)
    _make_video(video_src, duration=1.0)
    library.import_asset(workspace["id"], image_src, display_name="a.png")
    library.import_asset(workspace["id"], video_src, display_name="b.mp4")

    assert len(library.list_assets(workspace["id"])) == 2
    assert [a["kind"] for a in library.list_assets(workspace["id"], kind="image")] == ["image"]
    assert [a["kind"] for a in library.list_assets(workspace["id"], kind="video")] == ["video"]


# ── HTTP: workspace + asset endpoints ────────────────────────────────────────────

def test_http_create_list_get_rename_workspace(studio_state: Path) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "Kitchen equipment"})
            listed = await client.get("/api/workspaces")
            detail = await client.get(f"/api/workspaces/{created.json()['id']}")
            renamed = await client.patch(f"/api/workspaces/{created.json()['id']}", json={"name": "Kitchen utensils"})
            return created, listed, detail, renamed

    created, listed, detail, renamed = _run(scenario())
    assert created.status_code == 201
    body = created.json()
    assert {"id", "name", "createdAt", "updatedAt"} <= set(body)

    assert listed.status_code == 200
    rows = listed.json()
    by_id = {row["id"]: row for row in rows}
    assert body["id"] in by_id
    assert by_id[body["id"]]["videoCount"] == 0
    assert by_id[body["id"]]["assetCount"] == 0
    assert "brand" in by_id
    assert "unsorted" in by_id

    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["workspace"]["id"] == body["id"]
    assert detail_body["videos"] == []
    assert detail_body["assets"] == []

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Kitchen utensils"


def test_http_create_workspace_requires_name(studio_state: Path) -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/workspaces", json={})

    response = _run(scenario())
    assert response.status_code == 400


def test_http_get_and_patch_unknown_workspace_404(studio_state: Path) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            get = await client.get("/api/workspaces/does-not-exist")
            patch = await client.patch("/api/workspaces/does-not-exist", json={"name": "x"})
            return get, patch

    get, patch = _run(scenario())
    assert get.status_code == 404
    assert patch.status_code == 404


def test_http_import_assets_multipart_dedupes_and_lists(studio_state: Path, tmp_path: Path) -> None:
    image_path = tmp_path / "photo.png"
    _make_image(image_path)
    payload = image_path.read_bytes()
    video_path = tmp_path / "clip.mp4"
    _make_video(video_path, duration=1.0)

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "Broll"})
            workspace_id = created.json()["id"]
            uploaded = await client.post(
                f"/api/workspaces/{workspace_id}/assets",
                files=[
                    ("files", ("photo.png", payload, "image/png")),
                    ("files", ("photo-again.png", payload, "image/png")),
                    ("files", ("clip.mp4", video_path.read_bytes(), "video/mp4")),
                ],
            )
            listed = await client.get(f"/api/workspaces/{workspace_id}/assets")
            return created, uploaded, listed

    created, uploaded, listed = _run(scenario())
    assert created.status_code == 201
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert len(body["assets"]) == 3
    assert len(body["deduped"]) == 1
    assert body["assets"][0]["id"] == body["assets"][1]["id"]  # the two identical PNGs
    assert body["deduped"][0] == body["assets"][1]["id"]

    assert listed.status_code == 200
    assert len(listed.json()["assets"]) == 2  # one image (deduped) + one video


def test_http_asset_file_and_thumb_and_patch_delete(studio_state: Path, tmp_path: Path) -> None:
    image_path = tmp_path / "photo.png"
    _make_image(image_path)
    payload = image_path.read_bytes()

    async def scenario() -> tuple[httpx.Response, ...]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "Broll"})
            workspace_id = created.json()["id"]
            uploaded = await client.post(
                f"/api/workspaces/{workspace_id}/assets",
                files=[("files", ("photo.png", payload, "image/png"))],
            )
            asset_id = uploaded.json()["assets"][0]["id"]
            file_resp = await client.get(f"/api/workspaces/{workspace_id}/assets/{asset_id}/file")
            thumb_resp = await client.get(f"/api/workspaces/{workspace_id}/assets/{asset_id}/thumb")
            patched = await client.patch(
                f"/api/workspaces/{workspace_id}/assets/{asset_id}",
                json={"tags": ["garden"]},
            )
            deleted = await client.delete(f"/api/workspaces/{workspace_id}/assets/{asset_id}")
            missing = await client.get(f"/api/workspaces/{workspace_id}/assets/{asset_id}/file")
            return file_resp, thumb_resp, patched, deleted, missing

    file_resp, thumb_resp, patched, deleted, missing = _run(scenario())
    assert file_resp.status_code == 200
    assert file_resp.content == payload
    assert thumb_resp.status_code == 200
    assert thumb_resp.content
    assert patched.status_code == 200
    assert patched.json()["tags"] == ["garden"]
    assert deleted.status_code == 200
    assert missing.status_code == 404


def test_http_import_unsupported_file_rejected(studio_state: Path) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "Broll"})
            uploaded = await client.post(
                f"/api/workspaces/{created.json()['id']}/assets",
                files=[("files", ("malware.exe", b"no", "application/octet-stream"))],
            )
            return created, uploaded

    created, uploaded = _run(scenario())
    assert created.status_code == 201
    assert uploaded.status_code == 400


def test_http_import_into_unknown_workspace_404(studio_state: Path, tmp_path: Path) -> None:
    image_path = tmp_path / "photo.png"
    _make_image(image_path)

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post(
                "/api/workspaces/does-not-exist/assets",
                files=[("files", ("photo.png", image_path.read_bytes(), "image/png"))],
            )

    assert _run(scenario()).status_code == 404


# ── video assignment + unsorted listing ──────────────────────────────────────────

def test_http_assign_video_and_unsorted_listing(studio_state: Path) -> None:
    _write_project("clip-one")
    _write_project("clip-two")

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "garden"})
            workspace_id = created.json()["id"]
            before = await client.get("/api/workspaces/unsorted")
            assigned = await client.put(f"/api/workspaces/{workspace_id}/videos/clip-one")
            after_workspace = await client.get(f"/api/workspaces/{workspace_id}")
            after_unsorted = await client.get("/api/workspaces/unsorted")
            return created, before, assigned, after_workspace, after_unsorted

    created, before, assigned, after_workspace, after_unsorted = _run(scenario())
    workspace_id = created.json()["id"]
    assert before.status_code == 200
    assert {v["slug"] for v in before.json()["videos"]} == {"clip-one", "clip-two"}

    assert assigned.status_code == 200
    assert assigned.json()["project"]["workspaceId"] == workspace_id

    assert after_workspace.status_code == 200
    assert [v["slug"] for v in after_workspace.json()["videos"]] == ["clip-one"]

    assert after_unsorted.status_code == 200
    assert [v["slug"] for v in after_unsorted.json()["videos"]] == ["clip-two"]


def test_http_assign_video_back_to_unsorted(studio_state: Path) -> None:
    _write_project("clip-one")

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "garden"})
            workspace_id = created.json()["id"]
            await client.put(f"/api/workspaces/{workspace_id}/videos/clip-one")
            back = await client.put(f"/api/workspaces/{library.UNSORTED_WORKSPACE_ID}/videos/clip-one")
            unsorted_after = await client.get("/api/workspaces/unsorted")
            return created, back, unsorted_after

    created, back, unsorted_after = _run(scenario())
    assert back.status_code == 200
    assert back.json()["project"].get("workspaceId") is None  # omitted, not null (to_dict emits-if-set)
    assert [v["slug"] for v in unsorted_after.json()["videos"]] == ["clip-one"]


def test_export_render_documents_are_not_listed_as_videos(studio_state: Path) -> None:
    """An export at another format saves `<video>__<W>x<H>`: it must not show as a second video
    in its workspace, Home's Recent row or the counts. A short (`<parent>__<slug>`) is a real video, and
    a `__9x16` name with no video of its own is listed as whatever it is."""
    _write_project("clip-one", workspace_id="garden-abc123")
    _write_project("clip-one__9x16", workspace_id="garden-abc123")
    _write_project("clip-one__1x1", workspace_id="garden-abc123")
    _write_project("clip-one__hook-1", workspace_id="garden-abc123")
    _write_project("lonely__9x16", workspace_id="garden-abc123")

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            return await client.get("/api/workspaces/unsorted"), await client.get("/api/videos/recent?limit=8")

    groups = workspaces._workspace_video_groups()
    assert sorted(v["slug"] for v in groups["garden-abc123"]) == ["clip-one", "clip-one__hook-1", "lonely__9x16"]
    _, recent = _run(scenario())
    assert sorted(v["slug"] for v in recent.json()) == ["clip-one", "clip-one__hook-1", "lonely__9x16"]


def test_http_assign_video_unknown_workspace_404(studio_state: Path) -> None:
    _write_project("clip-one")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.put("/api/workspaces/does-not-exist/videos/clip-one")

    assert _run(scenario()).status_code == 404


def test_http_assign_unknown_video_404(studio_state: Path) -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "garden"})
            return await client.put(f"/api/workspaces/{created.json()['id']}/videos/does-not-exist")

    assert _run(scenario()).status_code == 404


# ── project.py: workspaceId survives a real PUT round-trip ───────────────────────

def test_workspace_id_survives_project_put_roundtrip(studio_state: Path) -> None:
    """A workspaceId written by PUT survives document parsing and subsequent reads/saves."""
    doc = {
        "schemaVersion": project_mod.CURRENT_SCHEMA_VERSION, "name": "clip-one", "fps": config.DEFAULT_FPS,
        "width": 1920, "height": 1080, "revision": 0, "transcript": None, "tracks": [],
    }

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            put1 = await client.put("/api/project/clip-one", json={"project": doc, "baseRevision": 0})
            saved = put1.json()["project"]
            saved["workspaceId"] = "kitchen-equipment-abc123"
            put2 = await client.put(
                "/api/project/clip-one", json={"project": saved, "baseRevision": put1.json()["revision"]},
            )
            got = await client.get("/api/project/clip-one")
            return put2, got

    put2, got = _run(scenario())
    assert put2.status_code == 200
    assert put2.json()["project"]["workspaceId"] == "kitchen-equipment-abc123"
    assert got.status_code == 200
    assert got.json()["project"]["workspaceId"] == "kitchen-equipment-abc123"

    on_disk = json.loads((config.PROJECTS_DIR / "clip-one.json").read_text(encoding="utf-8"))
    assert on_disk["workspaceId"] == "kitchen-equipment-abc123"


# ── video thumbnails, Home's Recent row, asset-in-use before delete ────

def test_video_summary_carries_a_thumb_url(studio_state: Path) -> None:
    """`/media/thumb/<stem>` is keyed by the
    primary speech clip's source filename stem, whatever the source's own origin."""
    _write_project("clip-one", media_source="data/source/talk-cam-a.mov")
    _write_project("clip-two", media_source="workspaces/garden/library/deadbeefcafebabe.mp4")
    (config.PROJECTS_DIR / "clip-three.json").write_text(json.dumps({
        "schemaVersion": project_mod.CURRENT_SCHEMA_VERSION, "name": "clip-three", "fps": config.DEFAULT_FPS,
        "width": 1920, "height": 1080, "revision": 1, "transcript": None, "tracks": [],
    }), encoding="utf-8")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/workspaces/unsorted")

    videos = {v["slug"]: v for v in _run(scenario()).json()["videos"]}
    assert videos["clip-one"]["thumbUrl"] == "/media/thumb/talk-cam-a"
    assert videos["clip-two"]["thumbUrl"] == "/media/thumb/deadbeefcafebabe"
    assert "thumbUrl" not in videos["clip-three"]  # no speech track at all


def test_script_video_summary_is_flagged_instead_of_a_black_thumb(studio_state: Path) -> None:
    """A script video's speech media is its black, silent source, so its summary names it a script
    video (the client draws its first animated scene) and carries no thumb."""
    _write_project("footage", media_source="data/source/talk.mp4")
    for slug, narration in (("estimated", "estimated"), ("recorded", "recorded")):
        _write_project(slug, media_source=f"data/source/{slug}.mp4")
        path = config.PROJECTS_DIR / f"{slug}.json"
        path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "narration": narration}), encoding="utf-8")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/videos/recent?limit=8")

    videos = {v["slug"]: v for v in _run(scenario()).json()}
    assert videos["footage"]["thumbUrl"] == "/media/thumb/talk" and "script" not in videos["footage"]
    for slug in ("estimated", "recorded"):
        assert videos[slug]["script"] is True and "thumbUrl" not in videos[slug]


def test_recent_videos_spans_every_workspace_newest_first(studio_state: Path) -> None:
    _write_project("oldest")
    os.utime(config.PROJECTS_DIR / "oldest.json", (time.time() - 300, time.time() - 300))
    _write_project("middle")
    os.utime(config.PROJECTS_DIR / "middle.json", (time.time() - 200, time.time() - 200))
    _write_project("newest")
    os.utime(config.PROJECTS_DIR / "newest.json", (time.time() - 100, time.time() - 100))

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "garden"})
            workspace_id = created.json()["id"]
            await client.put(f"/api/workspaces/{workspace_id}/videos/newest")
            recent = await client.get("/api/videos/recent?limit=2")
            return created, recent

    created, recent = _run(scenario())
    workspace_id = created.json()["id"]
    assert recent.status_code == 200
    body = recent.json()
    assert len(body) == 2  # limit respected
    assert [v["slug"] for v in body] == ["newest", "middle"]
    assert body[0]["workspaceId"] == workspace_id
    assert body[0]["workspaceName"] == "garden"
    assert body[1]["workspaceId"] == "unsorted"
    assert body[1]["workspaceName"] == "Unsorted"


def test_asset_usage_counts_videos_across_workspaces(studio_state: Path, tmp_path: Path) -> None:
    """A brand asset can be pulled into any workspace's video (the Library's Brand tab is shared),
    so usage is scanned across every project, not only the asset's own workspace."""
    image_path = tmp_path / "logo.png"
    _make_image(image_path)

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with _client() as client:
            uploaded = await client.post(
                "/api/workspaces/brand/assets", files=[("files", ("logo.png", image_path.read_bytes(), "image/png"))],
            )
            asset = uploaded.json()["assets"][0]
            created = await client.post("/api/workspaces", json={"name": "garden"})
            unused = await client.get(f"/api/workspaces/brand/assets/{asset['id']}/usage")
            return uploaded, created, unused

    uploaded, created, unused = _run(scenario())
    asset = uploaded.json()["assets"][0]
    assert unused.status_code == 200
    assert unused.json() == {"videos": 0}

    _write_project("uses-logo", workspace_id=created.json()["id"],
                    media_source=f"workspaces/brand/library/{asset['file']}")
    _write_project("also-unsorted", media_source=f"workspaces/brand/library/{asset['file']}")
    _write_project("unrelated", media_source="data/source/other.mp4")

    async def recheck() -> httpx.Response:
        async with _client() as client:
            return await client.get(f"/api/workspaces/brand/assets/{asset['id']}/usage")

    used = _run(recheck())
    assert used.status_code == 200
    assert used.json() == {"videos": 2}


def test_asset_usage_unknown_workspace_or_asset_404(studio_state: Path) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "garden"})
            missing_ws = await client.get("/api/workspaces/does-not-exist/assets/abcabcabcabcabca/usage")
            missing_asset = await client.get(f"/api/workspaces/{created.json()['id']}/assets/abcabcabcabcabca/usage")
            return missing_ws, missing_asset

    missing_ws, missing_asset = _run(scenario())
    assert missing_ws.status_code == 404
    assert missing_asset.status_code == 404


# ── archive and delete ────────────────────────────────────────────────────

def test_archive_and_restore_persist_without_counting_as_a_use(studio_state: Path) -> None:
    garden = library.create_workspace("garden")
    archived = library.set_archived(garden["id"], True)
    assert archived["archived"] is True and archived["updatedAt"] == garden["updatedAt"], "hiding it is not an edit"
    on_disk = json.loads((studio_state / "workspaces" / garden["id"] / "workspace.json").read_text(encoding="utf-8"))
    assert on_disk["archived"] is True
    restored = library.set_archived(garden["id"], False)
    assert "archived" not in restored and "archived" not in (library.get_workspace(garden["id"]) or {}), "only true is stored"
    for built_in in (library.BUILTIN_WORKSPACE_ID, library.UNSORTED_WORKSPACE_ID):
        with pytest.raises(library.LibraryError, match="built in"):
            library.set_archived(built_in, True)
    with pytest.raises(FileNotFoundError):
        library.set_archived("does-not-exist", True)


def test_delete_workspace_removes_only_an_empty_client_workspace(studio_state: Path, tmp_path: Path) -> None:
    empty = library.create_workspace("Empty")["id"]
    library.delete_workspace(empty, videos=0, characters=0)
    assert not (studio_state / "workspaces" / empty).exists()
    assert empty not in {w["id"] for w in library.list_workspaces()}

    stocked = library.create_workspace("Stocked")["id"]
    _make_image(tmp_path / "plate.png")
    library.import_asset(stocked, tmp_path / "plate.png", display_name="plate.png")
    with pytest.raises(library.DeleteRefused) as refused:
        library.delete_workspace(stocked, videos=2, characters=1)
    assert refused.value.error == "workspace is not empty"
    assert refused.value.reason == ("“Stocked” still has 2 videos, 1 asset and 1 character. Move or delete them first, "
                                    "or archive the workspace instead.")
    assert library.get_workspace(stocked) is not None and len(library.list_assets(stocked)) == 1, "nothing was removed"

    busy = library.create_workspace("Busy")["id"]
    with pytest.raises(library.DeleteRefused, match="still has 1 video. Move"):
        library.delete_workspace(busy, videos=1, characters=0)
    for built_in, name in ((library.BUILTIN_WORKSPACE_ID, "Brand"), (library.UNSORTED_WORKSPACE_ID, "Unsorted")):
        with pytest.raises(library.DeleteRefused, match=f"^{name} is built in and can't be deleted"):
            library.delete_workspace(built_in, videos=0, characters=0)
    with pytest.raises(FileNotFoundError):
        library.delete_workspace("does-not-exist", videos=0, characters=0)
    with pytest.raises(library.LibraryError):
        library.delete_workspace("../escape", videos=0, characters=0)


def test_http_list_carries_archived_and_the_last_use(studio_state: Path) -> None:
    garden = library.create_workspace("garden")["id"]
    cafe = library.create_workspace("Cafe")["id"]
    _write_project("herb-care", workspace_id=garden)
    _write_project("loose")
    hour_ago, soon = time.time() - 3600, time.time() + 60
    os.utime(config.PROJECTS_DIR / "loose.json", (hour_ago, hour_ago))
    os.utime(config.PROJECTS_DIR / "herb-care.json", (soon, soon))

    async def scenario() -> list[httpx.Response]:
        async with _client() as client:
            return [
                await client.get("/api/workspaces"),
                await client.patch(f"/api/workspaces/{cafe}", json={"archived": True}),
                await client.get("/api/workspaces"),
                await client.patch(f"/api/workspaces/{cafe}", json={"archived": False}),
                await client.get("/api/workspaces"),
            ]

    before, archive, archived_list, restore, after = _run(scenario())
    rows = {row["id"]: row for row in before.json()}
    herb_care = rows[garden]["lastUsedAt"]
    assert {r["archived"] for r in rows.values()} == {False}, "every row says whether it is archived"
    assert herb_care > rows[cafe]["lastUsedAt"], "a video saved in garden after Cafe was made makes garden the more recent"
    assert rows["unsorted"]["lastUsedAt"] < herb_care and rows["unsorted"]["videoCount"] == 1
    assert rows[garden]["videoCount"] == 1 and rows[cafe]["videoCount"] == 0
    assert archive.status_code == 200 and archive.json()["archived"] is True
    archived_rows = {row["id"]: row for row in archived_list.json()}
    assert archived_rows[cafe]["archived"] is True
    assert archived_rows[cafe]["lastUsedAt"] == rows[cafe]["lastUsedAt"], "archiving moves nothing in Recently used"
    assert restore.json()["archived"] is False
    assert {row["id"]: row["archived"] for row in after.json()}[cafe] is False


def test_http_patch_archived_is_a_boolean_on_a_client_workspace(studio_state: Path) -> None:
    garden = library.create_workspace("garden")["id"]

    async def scenario() -> list[httpx.Response]:
        async with _client() as client:
            return [
                await client.patch(f"/api/workspaces/{garden}", json={"archived": "yes"}),
                await client.patch("/api/workspaces/brand", json={"archived": True}),
                await client.patch("/api/workspaces/unsorted", json={"archived": True}),
                await client.patch("/api/workspaces/does-not-exist", json={"archived": True}),
                await client.patch(f"/api/workspaces/{garden}", json={"name": "Kitchen kit", "archived": True}),
            ]

    not_bool, brand, unsorted, unknown, both = _run(scenario())
    assert [r.status_code for r in (not_bool, brand, unsorted, unknown)] == [400, 400, 400, 404]
    assert brand.json()["error"] == "Brand is built in and can't be archived"
    assert both.status_code == 200 and (both.json()["name"], both.json()["archived"]) == ("Kitchen kit", True)


def test_http_delete_workspace_only_when_empty(studio_state: Path, tmp_path: Path) -> None:
    empty, films, photos, cast = (library.create_workspace(n)["id"] for n in ("Empty", "Films", "Photos", "Cast"))
    _write_project("herb-care", workspace_id=films)
    _make_image(tmp_path / "plate.png")
    library.import_asset(photos, tmp_path / "plate.png", display_name="plate.png")
    cast_mod.create_character(cast, "Guest", "lens")

    async def scenario() -> list[httpx.Response]:
        async with _client() as client:
            return [
                await client.delete(f"/api/workspaces/{empty}"),
                await client.delete(f"/api/workspaces/{films}"),
                await client.delete(f"/api/workspaces/{photos}"),
                await client.delete(f"/api/workspaces/{cast}"),
                await client.delete("/api/workspaces/brand"),
                await client.delete("/api/workspaces/unsorted"),
                await client.delete(f"/api/workspaces/{empty}"),
                await client.delete("/api/workspaces/Not_An_Id"),
                await client.get("/api/workspaces"),
            ]

    deleted, video, asset, character, brand, unsorted, again, bad, listed = _run(scenario())
    assert deleted.status_code == 200 and deleted.json() == {"ok": True}
    refused = {"video": video, "asset": asset, "character": character, "brand": brand, "unsorted": unsorted}
    assert {k: r.status_code for k, r in refused.items()} == dict.fromkeys(refused, 409)
    assert video.json() == {"error": "workspace is not empty",
                            "reason": "“Films” still has 1 video. Move or delete them first, or archive the workspace instead.",
                            "code": "conflict"}
    assert "still has 1 asset." in asset.json()["reason"] and "still has 1 character." in character.json()["reason"]
    assert brand.json() == {"error": "built-in workspace", "reason": "Brand is built in and can't be deleted.", "code": 'conflict'}
    assert unsorted.json()["reason"] == "Unsorted is built in and can't be deleted."
    assert (again.status_code, bad.status_code) == (404, 404)
    ids = {row["id"] for row in listed.json()}
    assert empty not in ids and {films, photos, cast, "brand", "unsorted"} <= ids
