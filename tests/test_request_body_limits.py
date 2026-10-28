from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import tracemalloc
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

import config
from pipeline import library
from pipeline import project as project_mod
from studio import compute
from studio.app import app
from studio.routes import script_video, workspaces


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, relative in (
        ("STATE_HOME", "."), ("DATA_DIR", "data/source"),
        ("WORKSPACES_DIR", "workspaces"), ("PROJECTS_DIR", "outputs/projects"),
        ("RUNTIME_DIR", "runtime"), ("RUN_LOG", "run.log"),
    ):
        monkeypatch.setattr(config, name, tmp_path / relative)
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *args, **kwargs: None)
    # Chat answers 404 for an unknown video before reading the body, so the video must exist.
    project_mod.write_project(config.PROJECTS_DIR / "chat-video.json", project_mod.Project(name="chat-video"))
    return tmp_path


def test_multipart_limit_stops_consumption_with_bounded_memory(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = 4 * 1024**2
    monkeypatch.setattr(config, "MAX_LIBRARY_UPLOAD_BYTES", limit, raising=False)
    # Patch the old location as well so the regression isolates buffering, not cap configuration.
    from studio.routes import workspaces

    monkeypatch.setattr(workspaces, "MAX_LIBRARY_UPLOAD_BYTES", limit, raising=False)
    workspace = library.create_workspace("Media")
    consumed = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal consumed
        yield b'--media\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n\r\n'
        for _ in range(256):
            consumed += 1
            yield b"x" * (64 * 1024)
        yield b"\r\n--media--\r\n"

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post(
                f"/api/workspaces/{workspace['id']}/assets", content=body(),
                headers={"content-type": "multipart/form-data; boundary=media"},
            )

    tracemalloc.start()
    try:
        response = asyncio.run(scenario())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.status_code == 413
    assert response.json() == {"error": "upload exceeds the size limit", "code": 'payload_too_large'}
    assert consumed < 256
    assert peak < 2 * 1024**2
    assert not list(isolated_state.rglob("*.part"))
    assert not list(config.WORKSPACES_DIR.glob(".upload-*"))
    assert library.list_assets(workspace["id"]) == []


@pytest.mark.parametrize("narration", [False, True])
def test_raw_upload_limit_ignores_understated_length_and_cleans_partial(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, narration: bool,
) -> None:
    monkeypatch.setattr(config, "MAX_SOURCE_UPLOAD_BYTES", 64, raising=False)
    monkeypatch.setattr(config, "MAX_NARRATION_UPLOAD_BYTES", 64, raising=False)
    monkeypatch.setattr(script_video, "_script_doc", lambda project: {"narration": "estimated"})
    monkeypatch.setattr(script_video, "_store_take", lambda *args: pytest.fail("oversize take was stored"))
    consumed = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal consumed
        for _ in range(16):
            consumed += 1
            yield b"x" * 32

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            path = "/api/script-video/video/narration/take.wav" if narration else "/upload/clip.mp4"
            return await client.put(path, content=body(), headers={"content-length": "1"})

    response = asyncio.run(scenario())
    assert response.status_code == 413
    message = "the take is larger than 2 GB" if narration else "request body exceeds the size limit"
    assert response.json() == {"error": message, "code": 'payload_too_large'}
    assert consumed == 3
    assert not list(config.DATA_DIR.iterdir())


JSON_ROUTES = [
    ("POST", "/api/chat/chat-video"), ("POST", "/api/jobs"), ("POST", "/api/projects"),
    ("POST", "/api/reveal"), ("POST", "/api/script-video"), ("POST", "/api/stock/keys"),
    ("POST", "/api/verbs/ripple_cut/video"), ("POST", "/api/workspaces"),
    ("POST", "/api/cast/characters"), ("POST", "/api/project/video/thumbnail/render"),
    ("PUT", "/api/project/video"), ("PATCH", "/api/project/video"),
]


@pytest.mark.parametrize("method,path", JSON_ROUTES)
def test_json_routes_stop_at_limit(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, method: str, path: str,
) -> None:
    monkeypatch.setattr(config, "MAX_JSON_BODY_BYTES", 64, raising=False)
    monkeypatch.setattr(config, "MAX_PROJECT_BODY_BYTES", 64, raising=False)
    consumed = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal consumed
        for _ in range(16):
            consumed += 1
            yield b" " * 32

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.request(method, path, content=body(), headers={"content-type": "application/json"})

    response = asyncio.run(scenario())
    assert response.status_code == 413
    assert response.json() == {"error": "request body exceeds the size limit", "code": 'payload_too_large'}
    assert consumed == 3


@pytest.mark.parametrize("method,path", JSON_ROUTES)
def test_json_routes_preserve_invalid_json_response(
    isolated_state: Path, method: str, path: str,
) -> None:
    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.request(method, path, content=b"{", headers={"content-type": "application/json"})

    response = asyncio.run(scenario())
    assert response.status_code == 400
    error = "format must be one of ['16:9', '9:16']" if path.endswith("/thumbnail/render") else "invalid JSON"
    assert response.json() == {"error": error, "code": 'invalid_request'}


@pytest.mark.parametrize("method,path", JSON_ROUTES)
@pytest.mark.parametrize("content_type", [
    "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=media", "application/jsonp",
])
def test_json_routes_reject_declared_non_json_without_reading_body(
    isolated_state: Path, method: str, path: str, content_type: str,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        pytest.fail("non-JSON body was read")
        yield b"{}"

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.request(method, path, content=body(), headers={"content-type": content_type})

    response = asyncio.run(scenario())
    assert response.status_code == 415
    assert response.json() == {"error": "expected application/json", "code": "unsupported_media_type"}


@pytest.mark.parametrize("method,path", JSON_ROUTES)
def test_json_routes_reject_undeclared_body(isolated_state: Path, method: str, path: str) -> None:
    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.request(method, path, content=b'{"name": "Media"}')

    response = asyncio.run(scenario())
    assert response.status_code == 415
    assert response.json() == {"error": "expected application/json", "code": "unsupported_media_type"}
    assert all(workspace["name"] != "Media" for workspace in library.list_workspaces())


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8", "Application/JSON"])
def test_json_route_accepts_json_content_types(isolated_state: Path, content_type: str) -> None:
    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post("/api/workspaces", content=b'{"name": "Media"}', headers={"content-type": content_type})

    response = asyncio.run(scenario())
    assert response.status_code == 201
    assert response.json()["name"] == "Media"


def test_bodyless_json_route_reads_empty_object(isolated_state: Path) -> None:
    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post("/api/workspaces")

    response = asyncio.run(scenario())
    assert response.status_code == 400
    assert response.json() == {"error": "name is required", "code": "invalid_request"}


def test_multipart_asset_import_still_accepted(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = library.create_workspace("Media")
    imported: list[str] = []

    def import_uploads(workspace_id: str, files: list[tuple[str, Path]]) -> tuple[list[dict], list[str]]:
        imported.extend(name for name, _path in files)
        return [], []

    monkeypatch.setattr(workspaces, "_import_library_uploads", import_uploads)

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post(f"/api/workspaces/{workspace['id']}/assets", files={"files": ("clip.mp4", b"data")})

    response = asyncio.run(scenario())
    assert response.status_code == 201
    assert imported == ["clip.mp4"]


def test_source_upload_at_limit_still_succeeds(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_SOURCE_UPLOAD_BYTES", 64, raising=False)

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.put("/upload/clip.mp4", content=b"x" * 64)

    assert asyncio.run(scenario()).status_code == 200
    assert (config.DATA_DIR / "clip.mp4").read_bytes() == b"x" * 64


def test_project_body_cap_allows_large_document(isolated_state: Path) -> None:
    document = project_mod.Project(name="video", fps=30, tracks=[project_mod.Track(
        id="speech", kind="speech", clips=[project_mod.Clip(
            id=str(i), media=project_mod.Media(source="data/source/clip.mp4"),
            srcInF=0, srcOutF=30, tlStartF=i * 30,
        ) for i in range(10_000)],
    )])
    payload = json.dumps({"project": project_mod.to_dict(document), "baseRevision": 0}).encode()
    assert config.MAX_JSON_BODY_BYTES < len(payload) < config.MAX_PROJECT_BODY_BYTES

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.put("/api/project/video", content=payload, headers={"content-type": "application/json"})

    response = asyncio.run(scenario())
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    assert len(response.json()["project"]["tracks"][0]["clips"]) == 10_000


@pytest.mark.parametrize("method,path,content_type,limit_name", [
    ("PUT", "/upload/clip.mp4", "application/octet-stream", "MAX_SOURCE_UPLOAD_BYTES"),
    ("PUT", "/api/script-video/video/narration/take.wav", "application/octet-stream", "MAX_NARRATION_UPLOAD_BYTES"),
    ("POST", "/api/workspaces", "application/json", "MAX_JSON_BODY_BYTES"),
    ("POST", "/api/workspaces/{workspace}/assets", "multipart/form-data; boundary=media", "MAX_LIBRARY_UPLOAD_BYTES"),
])
def test_declared_oversize_is_rejected_without_consuming_body(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
    method: str, path: str, content_type: str, limit_name: str,
) -> None:
    monkeypatch.setattr(config, limit_name, 64)
    monkeypatch.setattr(script_video, "_script_doc", lambda project: {"narration": "estimated"})
    workspace = library.create_workspace("Media")

    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        if self.suffix == ".part":
            pytest.fail("declared oversize upload opened a file")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    async def body() -> AsyncIterator[bytes]:
        pytest.fail("declared oversize body was read")
        yield b"x"

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.request(
                method, path.format(workspace=workspace["id"]), content=body(),
                headers={"content-type": content_type, "content-length": "65"},
            )

    response = asyncio.run(scenario())
    assert response.status_code == 413
    message = {
        "MAX_LIBRARY_UPLOAD_BYTES": "upload exceeds the size limit",
        "MAX_NARRATION_UPLOAD_BYTES": "the take is larger than 2 GB",
    }.get(limit_name, "request body exceeds the size limit")
    assert response.json() == {"error": message, "code": 'payload_too_large'}
    assert not list(isolated_state.rglob("*.part"))
    assert not list(config.WORKSPACES_DIR.glob(".upload-*"))


def test_incomplete_multipart_cleans_temporary_files(isolated_state: Path) -> None:
    workspace = library.create_workspace("Media")

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post(
                f"/api/workspaces/{workspace['id']}/assets",
                content=b'--media\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n\r\ndata',
                headers={"content-type": "multipart/form-data; boundary=media"},
            )

    assert asyncio.run(scenario()).status_code == 400
    assert not list(isolated_state.rglob("*.part"))
    assert not list(config.WORKSPACES_DIR.glob(".upload-*"))
    assert library.list_assets(workspace["id"]) == []


@pytest.mark.parametrize("header,message", [
    (b"Content-Transfer-Encoding: base64", "unsupported Content-Transfer-Encoding"),
    (b"Malformed header", "invalid multipart headers"),
])
def test_invalid_multipart_headers_return_clear_errors_without_opening_files(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, header: bytes, message: str,
) -> None:
    workspace = library.create_workspace("Media")
    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        if self.suffix == ".part":
            pytest.fail("invalid multipart headers opened a file")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post(
                f"/api/workspaces/{workspace['id']}/assets",
                content=b'--media\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n'
                + header + b"\r\n\r\ndata\r\n--media--\r\n",
                headers={"content-type": "multipart/form-data; boundary=media"},
            )

    response = asyncio.run(scenario())
    assert response.status_code == 400
    assert response.json() == {"error": message, "code": 'invalid_request'}
    assert not list(config.WORKSPACES_DIR.glob(".upload-*"))


def test_concurrent_source_uploads_use_exclusive_distinct_temps(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Path] = []
    modes: list[str] = []
    original_open = Path.open

    def track_open(self: Path, mode: str = "r", *args, **kwargs):
        if self.suffix == ".part":
            opened.append(self)
            modes.append(mode)
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)

    async def scenario() -> list[httpx.Response]:
        both_started = asyncio.Event()
        started = 0

        async def body(value: bytes) -> AsyncIterator[bytes]:
            nonlocal started
            yield value * 32
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 5)
            yield value * 32

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return list(await asyncio.gather(
                client.put("/upload/clip.mp4", content=body(b"a")),
                client.put("/upload/clip.mp4", content=body(b"b")),
            ))

    responses = asyncio.run(scenario())
    assert all(response.status_code in (200, 409) for response in responses)
    assert any(response.status_code == 200 for response in responses)
    assert len(opened) == len(set(opened)) == 2
    assert modes == ["xb", "xb"]
    assert (config.DATA_DIR / "clip.mp4").read_bytes() in (b"a" * 64, b"b" * 64)
    assert not list(config.DATA_DIR.glob(".*.part"))


def test_cancelled_library_import_cleans_up_after_the_file_is_released(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = library.create_workspace("Media")
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def import_uploads(workspace_id: str, files: list[tuple[str, Path]]) -> tuple[list[dict], list[str]]:
        try:
            with files[0][1].open("rb"):
                started.set()
                assert release.wait(5)
            return [], []
        finally:
            finished.set()

    monkeypatch.setattr(workspaces, "_import_library_uploads", import_uploads)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            task = asyncio.create_task(client.post(
                f"/api/workspaces/{workspace['id']}/assets", files={"files": ("clip.mp4", b"data")},
            ))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert list(config.WORKSPACES_DIR.glob(".upload-*"))
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 5)
            async with asyncio.timeout(5):
                while list(config.WORKSPACES_DIR.glob(".upload-*")):
                    await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_startup_sweeps_only_stale_upload_temps(isolated_state: Path) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    stale_directory = config.WORKSPACES_DIR / ".upload-old"
    stale_directory.mkdir()
    (stale_directory / "1.part").write_bytes(b"partial")
    stale_file = config.DATA_DIR / ".clip-random.part"
    stale_file.write_bytes(b"partial")
    fresh_directory = config.WORKSPACES_DIR / ".upload-new"
    fresh_directory.mkdir()
    fresh_file = config.DATA_DIR / ".fresh.part"
    fresh_file.write_bytes(b"partial")
    media = config.DATA_DIR / "clip.mp4"
    media.write_bytes(b"media")
    workspace = config.WORKSPACES_DIR / "workspace"
    workspace.mkdir()
    old = time.time() - 3601
    for path in (stale_directory, stale_file, media, workspace):
        os.utime(path, (old, old))

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            assert not stale_directory.exists()
            assert not stale_file.exists()
            assert fresh_directory.is_dir()
            assert fresh_file.is_file()
            assert media.read_bytes() == b"media"
            assert workspace.is_dir()

    asyncio.run(scenario())
