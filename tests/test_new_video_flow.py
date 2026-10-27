"""Footage-to-video backend behavior:

- `/api/projects` must not block the event loop on ffprobe/write work (gate).
- `/api/workspaces/{id}/assets/{id}/use-as-source` turns a library video asset into an
  `/api/projects` source slug without re-uploading its bytes.
- The scrub-proxy readiness signal (`GET /api/project/{p}/proxy` + the SSE `job` event
  `_spawn_proxy` fires once the encode lands) so the UI never has to poll blindly.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from scripts import studio_app
from studio import compute, helpers
from studio import state as process_state

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


@pytest.fixture()
def studio_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data" / "source"
    data.mkdir(parents=True)
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "outputs" / "projects")
    monkeypatch.setattr(config, "PROXIES_DIR", tmp_path / "outputs" / "proxies")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    # A stray waiter/thread registered by an earlier test must never leak into this one.
    monkeypatch.setattr(process_state, "_PROXY_WAITERS", {})
    return tmp_path


def _make_video(dest: Path, *, duration: float = 1.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=64x64:rate=10:duration={duration}",
         "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=8000", "-t", str(duration),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)],
        check=True, capture_output=True, text=True,
    )


# ── /api/projects must not block the event loop ──────────────────────────────

def test_create_project_does_not_block_the_event_loop(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (config.DATA_DIR / "interview.mp4").write_bytes(b"video")
    monkeypatch.setattr(compute, "_enqueue_transcribe", lambda *a, **k: {"status": "queued"})
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *a, **k: None)

    def slow_duration(_source: Path) -> int:
        time.sleep(0.4)
        return 450

    monkeypatch.setattr(helpers, "_source_duration_frames", slow_duration)

    async def scenario() -> tuple[httpx.Response, httpx.Response, float]:
        async with _client() as client:
            create = asyncio.ensure_future(
                client.post("/api/projects", json={"project": "launch_cut", "source": "interview"})
            )
            await asyncio.sleep(0.05)  # let `create` reach the to_thread call before racing it
            start = time.monotonic()
            fast = await client.get("/api/workspaces")
            fast_elapsed = time.monotonic() - start
            create_response = await create
            return create_response, fast, fast_elapsed

    create_response, fast, fast_elapsed = _run(scenario())
    assert create_response.status_code == 201
    assert fast.status_code == 200
    # A concurrent request must complete well inside the 0.4s the create's ffprobe stand-in
    # sleeps for — on the old code (the blocking work ran inline in the async handler) this
    # request could not even be dispatched until the sleep finished.
    assert fast_elapsed < 0.3


def test_create_project_still_rejects_duplicate_project(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (config.DATA_DIR / "interview.mp4").write_bytes(b"video")
    monkeypatch.setattr(helpers, "_source_duration_frames", lambda source: 450)
    monkeypatch.setattr(compute, "_enqueue_transcribe", lambda *a, **k: {"status": "queued"})
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *a, **k: None)

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            first = await client.post("/api/projects", json={"project": "launch_cut", "source": "interview"})
            second = await client.post("/api/projects", json={"project": "launch_cut", "source": "interview"})
            return first, second

    first, second = _run(scenario())
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {"error": "project already exists", "code": 'conflict'}


# ── library asset -> /api/projects source, without re-uploading ──────────────────

async def _import_video_asset(client: httpx.AsyncClient, video_path: Path) -> tuple[str, str]:
    created = await client.post("/api/workspaces", json={"name": "garden"})
    workspace_id = created.json()["id"]
    uploaded = await client.post(
        f"/api/workspaces/{workspace_id}/assets",
        files=[("files", ("seedling.mp4", video_path.read_bytes(), "video/mp4"))],
    )
    return workspace_id, uploaded.json()["assets"][0]["id"]


def test_use_library_asset_as_source_hardlinks_into_data_dir(
    studio_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *a, **k: None)
    video_path = tmp_path / "seedling.mp4"
    _make_video(video_path, duration=0.5)

    async def scenario() -> tuple[str, str, httpx.Response, httpx.Response]:
        async with _client() as client:
            workspace_id, asset_id = await _import_video_asset(client, video_path)
            linked = await client.post(f"/api/workspaces/{workspace_id}/assets/{asset_id}/use-as-source")
            created = await client.post(
                "/api/projects", json={"project": "seedling_video", "source": linked.json()["slug"]},
            )
            return workspace_id, asset_id, linked, created

    workspace_id, asset_id, linked, created = _run(scenario())
    assert linked.status_code == 200
    body = linked.json()
    assert body["ok"] is True
    assert body["slug"] == asset_id
    assert body["name"] == f"{asset_id}.mp4"
    assert (config.DATA_DIR / body["name"]).is_file()
    # /api/projects can build a real project straight off the linked slug — no re-upload needed.
    assert created.status_code == 201
    assert created.json()["project"]["tracks"][0]["clips"][0]["media"]["source"] == f"data/source/{asset_id}.mp4"

    # Picking the same library asset again is a cheap no-op join, not a second copy.
    async def relink() -> httpx.Response:
        async with _client() as client:
            return await client.post(f"/api/workspaces/{workspace_id}/assets/{asset_id}/use-as-source")

    again = _run(relink())
    assert again.status_code == 200
    assert again.json()["slug"] == asset_id


def test_use_library_asset_as_source_rejects_non_video(studio_state: Path, tmp_path: Path) -> None:
    image_path = tmp_path / "photo.png"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:size=32x32", "-frames:v", "1", str(image_path)],
        check=True, capture_output=True, text=True,
    )

    async def scenario() -> httpx.Response:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "garden"})
            workspace_id = created.json()["id"]
            uploaded = await client.post(
                f"/api/workspaces/{workspace_id}/assets",
                files=[("files", ("photo.png", image_path.read_bytes(), "image/png"))],
            )
            asset_id = uploaded.json()["assets"][0]["id"]
            return await client.post(f"/api/workspaces/{workspace_id}/assets/{asset_id}/use-as-source")

    response = _run(scenario())
    assert response.status_code == 400
    assert response.json() == {"error": "asset is not a video", "code": 'invalid_request'}


def test_use_library_asset_as_source_unknown_asset_404(studio_state: Path) -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "garden"})
            workspace_id = created.json()["id"]
            return await client.post(f"/api/workspaces/{workspace_id}/assets/{'0' * 16}/use-as-source")

    assert _run(scenario()).status_code == 404


def test_use_library_asset_as_source_unknown_workspace_404(studio_state: Path) -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post(f"/api/workspaces/does-not-exist/assets/{'0' * 16}/use-as-source")

    assert _run(scenario()).status_code == 404


# ── proxy-ready signal: status field + SSE job event ──────────────────────────────

def _write_project(slug: str, source_name: str = "clip.mp4") -> None:
    doc = {
        "schemaVersion": 1, "name": slug, "fps": config.DEFAULT_FPS,
        "width": 1920, "height": 1080, "revision": 1, "transcript": None,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [{
            "id": "c1", "media": {"source": f"data/source/{source_name}"},
            "srcInF": 0, "srcOutF": 90, "tlStartF": 0,
        }]}],
    }
    path = config.PROJECTS_DIR / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_proxy_lifecycle_none_before_any_encode(studio_state: Path) -> None:
    _write_project("clip")
    (config.DATA_DIR / "clip.mp4").write_bytes(b"video")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/project/clip/proxy")

    response = _run(scenario())
    assert response.status_code == 200
    assert response.json() == {"state": "none", "proxy": None}


def test_proxy_lifecycle_ready_once_persisted_on_the_doc(studio_state: Path) -> None:
    doc = {
        "schemaVersion": 1, "name": "clip", "fps": config.DEFAULT_FPS,
        "width": 1920, "height": 1080, "revision": 1, "transcript": None,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [{
            "id": "c1", "media": {"source": "data/source/clip.mp4", "proxy": "outputs/proxies/clip_540.mp4"},
            "srcInF": 0, "srcOutF": 90, "tlStartF": 0,
        }]}],
    }
    path = config.PROJECTS_DIR / "clip.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/project/clip/proxy")

    response = _run(scenario())
    assert response.json() == {"state": "ready", "proxy": "outputs/proxies/clip_540.mp4"}


def test_proxy_lifecycle_ready_from_an_existing_encode_on_disk(studio_state: Path) -> None:
    _write_project("clip")
    (config.DATA_DIR / "clip.mp4").write_bytes(b"video")
    proxy_path = config.PROXIES_DIR / "clip_540.mp4"
    proxy_path.parent.mkdir(parents=True, exist_ok=True)
    proxy_path.write_bytes(b"proxy")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/project/clip/proxy")

    response = _run(scenario())
    assert response.json() == {"state": "ready", "proxy": "outputs/proxies/clip_540.mp4"}


def test_proxy_lifecycle_pending_while_the_encode_still_writes_its_output(studio_state: Path) -> None:
    _write_project("clip")
    source = config.DATA_DIR / "clip.mp4"
    source.write_bytes(b"video")
    partial = config.PROXIES_DIR / "clip_540.mp4"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"half an mp4")
    process_state._PROXY_WAITERS[str(source)] = {"clip"}

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/project/clip/proxy")

    assert _run(scenario()).json() == {"state": "pending", "proxy": None}
    process_state._PROXY_WAITERS.pop(str(source))
    assert _run(scenario()).json() == {"state": "ready", "proxy": "outputs/proxies/clip_540.mp4"}


def test_spawn_proxy_notifies_the_project_over_sse_on_completion(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_project("clip")
    source = config.DATA_DIR / "clip.mp4"
    source.write_bytes(b"video")
    dest = config.PROXIES_DIR / "clip_540.mp4"

    def fake_make_proxy(src: Path, *, dest: Path | None = None) -> object:
        target = dest or config.PROXIES_DIR / f"{src.stem}_540.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"proxy")
        return schema_result_ok(src.stem, str(target))

    monkeypatch.setattr(compute.proxy_stage, "make_proxy", fake_make_proxy)

    async def scenario() -> tuple[dict, httpx.Response]:
        async with _client() as client:
            compute._capture_job_event_loop()
            q = process_state.hub.subscribe("clip")
            await asyncio.to_thread(compute._spawn_proxy, source, project="clip")
            event = await asyncio.wait_for(q.get(), timeout=5)
            process_state.hub.unsubscribe("clip", q)
            lifecycle = await client.get("/api/project/clip/proxy")
            return event, lifecycle

    event, lifecycle = _run(scenario())
    assert event == {"type": "job", "kind": "proxy", "status": "ready", "proxy": "outputs/proxies/clip_540.mp4"}
    assert dest.is_file()
    assert lifecycle.json() == {"state": "ready", "proxy": "outputs/proxies/clip_540.mp4"}


def schema_result_ok(clip_id: str, output_path: str) -> object:
    from pipeline import contracts
    return contracts.StageResult(status="ok", stage="proxy", clip_id=clip_id, output_path=output_path)


def test_spawn_proxy_dedupes_concurrent_calls_for_the_same_source(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_project("clip")
    source = config.DATA_DIR / "clip.mp4"
    source.write_bytes(b"video")
    calls: list[Path] = []

    def fake_make_proxy(src: Path, *, dest: Path | None = None) -> object:
        calls.append(src)
        target = dest or config.PROXIES_DIR / f"{src.stem}_540.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"proxy")
        time.sleep(0.15)
        return schema_result_ok(src.stem, str(target))

    monkeypatch.setattr(compute.proxy_stage, "make_proxy", fake_make_proxy)
    compute._spawn_proxy(source, project="clip")
    compute._spawn_proxy(source, project="clip")  # must join the same in-flight encode, not re-run
    time.sleep(0.4)
    assert len(calls) == 1
