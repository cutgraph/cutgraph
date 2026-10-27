"""First-run editor project creation tests, including the rotation-aware canvas size:
a phone recording is often coded 1920x1080 with a rotation display matrix (or the
legacy `rotate` tag) and displays portrait — a new project must match that display orientation
instead of always defaulting to landscape.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from scripts import studio_app
from studio import compute, helpers
from studio.routes import workspaces

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


@pytest.fixture()
def project_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    data = tmp_path / "data" / "source"
    projects = tmp_path / "outputs" / "projects"
    data.mkdir(parents=True)
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PROJECTS_DIR", projects)
    return data, projects


def test_create_project_materializes_full_source_timeline(
    project_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, projects = project_dirs
    (data / "interview.mp4").write_bytes(b"video")
    monkeypatch.setattr(helpers, "_source_duration_frames", lambda source: 450)
    monkeypatch.setattr(
        compute,
        "_enqueue_transcribe",
        lambda slug, *, project=None: {"status": "queued"},
    )

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/projects", json={"project": "launch_cut", "source": "interview"})

    response = _run(scenario())
    assert response.status_code == 201
    body = response.json()
    assert body["project"]["name"] == "launch_cut"
    assert body["project"]["tracks"] == [{
        "id": "speech", "kind": "speech", "clips": [{
            "id": "source", "media": {"source": "data/source/interview.mp4", "proxy": None},
            "srcInF": 0, "srcOutF": 450, "tlStartF": 0, "gainDb": 0.0,
        }],
    }]
    assert body["transcription"] == {"status": "queued"}
    saved = json.loads((projects / "launch_cut.json").read_text(encoding="utf-8"))
    assert saved == body["project"]


def test_create_project_ignores_legacy_compute_preference(
    project_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, _projects = project_dirs
    (data / "interview.mp4").write_bytes(b"video")
    monkeypatch.setattr(helpers, "_source_duration_frames", lambda source: 450)
    captured: dict[str, str] = {}

    def enqueue(slug: str, *, project: str | None = None) -> dict:
        captured.update(slug=slug, project=project or "")
        return {"status": "queued"}

    monkeypatch.setattr(compute, "_enqueue_transcribe", enqueue)

    async def selected() -> httpx.Response:
        async with _client() as client:
            return await client.post(
                "/api/projects",
                json={
                    "project": "launch_cut",
                    "source": "interview",
                    "executionPreference": "legacy",
                },
            )

    response = _run(selected())
    assert response.status_code == 201
    assert captured == {
        "slug": "interview",
        "project": "launch_cut",
    }

    async def malformed() -> httpx.Response:
        async with _client() as client:
            return await client.post(
                "/api/projects",
                json={
                    "project": "other_cut",
                    "source": "interview",
                    "executionPreference": "legacy-worker",
                },
            )

    bad = _run(malformed())
    assert bad.status_code == 201
    assert captured == {"slug": "interview", "project": "other_cut"}


def test_create_project_stores_and_returns_title(
    project_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, projects = project_dirs
    (data / "interview.mp4").write_bytes(b"video")
    monkeypatch.setattr(helpers, "_source_duration_frames", lambda source: 450)
    monkeypatch.setattr(compute, "_enqueue_transcribe", lambda slug, *, project=None: {"status": "queued"})

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post(
                "/api/projects", json={"project": "launch_cut", "source": "interview", "title": "Launch day"},
            )

    response = _run(scenario())
    assert response.status_code == 201
    assert response.json()["project"]["title"] == "Launch day"
    saved = json.loads((projects / "launch_cut.json").read_text(encoding="utf-8"))
    assert saved["title"] == "Launch day"


def test_create_project_rejects_invalid_title(project_dirs: tuple[Path, Path]) -> None:
    data, _projects = project_dirs
    (data / "interview.mp4").write_bytes(b"video")

    async def scenario(title: object) -> httpx.Response:
        async with _client() as client:
            return await client.post(
                "/api/projects", json={"project": "launch_cut", "source": "interview", "title": title},
            )

    assert _run(scenario("   ")).status_code == 400
    assert _run(scenario("x" * 121)).status_code == 400
    assert _run(scenario(123)).status_code == 400


def test_create_project_rejects_missing_source(project_dirs: tuple[Path, Path]) -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/projects", json={"project": "new", "source": "missing"})

    response = _run(scenario())
    assert response.status_code == 404
    assert response.json() == {"error": "source not found", "code": 'not_found'}


def test_create_project_does_not_overwrite_existing(project_dirs: tuple[Path, Path]) -> None:
    data, projects = project_dirs
    (data / "source.mp4").write_bytes(b"video")
    projects.mkdir(parents=True)
    (projects / "new.json").write_text("{}", encoding="utf-8")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/projects", json={"project": "new", "source": "source"})

    response = _run(scenario())
    assert response.status_code == 409
    assert response.json() == {"error": "project already exists", "code": 'conflict'}


# ── _video_summary title preference ────────────────────────────────────────

@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        ({"title": "Human Title", "name": "slug_name"}, "Human Title"),
        ({"title": "", "name": "slug_name"}, "slug_name"),
        ({"title": None, "name": "slug_name"}, "slug_name"),
        ({"name": "slug_name"}, "slug_name"),
        ({}, "slug"),
    ],
)
def test_video_summary_prefers_title_then_name_then_slug(doc: dict, expected: str) -> None:
    assert workspaces._video_summary("slug", doc, 0.0)["title"] == expected


# ── rotation-aware project canvas ────────────────────────────────

def _stream_json(*, width: int, height: int, rotation: int | None = None,
                  rotate_tag: str | None = None) -> str:
    stream: dict = {"width": width, "height": height}
    if rotation is not None:
        stream["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": rotation}]
    if rotate_tag is not None:
        stream["tags"] = {"rotate": rotate_tag}
    return json.dumps({"streams": [stream]})


class _FakeProbe:
    """Stands in for `subprocess.run(["ffprobe", ...])`, returning canned `-show_streams` JSON —
    mirrors test_proxy.py's _Recorder convention for deterministic ffprobe-shape coverage."""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, self.stdout, "")


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        pytest.param(_stream_json(width=1920, height=1080), (1920, 1080), id="landscape-unrotated"),
        pytest.param(_stream_json(width=1080, height=1920), (1080, 1920), id="portrait-unrotated"),
        pytest.param(_stream_json(width=1080, height=1080), (1080, 1080), id="square-unrotated"),
        pytest.param(_stream_json(width=1920, height=1080, rotation=90), (1080, 1920),
                     id="landscape-coded-rotation-90-side-data"),
        pytest.param(_stream_json(width=1920, height=1080, rotation=-90), (1080, 1920),
                     id="landscape-coded-rotation-neg90-side-data"),
        pytest.param(_stream_json(width=1920, height=1080, rotation=270), (1080, 1920),
                     id="landscape-coded-rotation-270-side-data"),
        pytest.param(_stream_json(width=1920, height=1080, rotation=180), (1920, 1080),
                     id="rotation-180-does-not-swap"),
        pytest.param(_stream_json(width=1920, height=1080, rotate_tag="90"), (1080, 1920),
                     id="legacy-rotate-tag-90"),
        pytest.param(_stream_json(width=1080, height=1920, rotation=90), (1920, 1080),
                     id="portrait-coded-rotation-90-becomes-landscape-display"),
        pytest.param(json.dumps({"streams": []}), (1920, 1080), id="no-video-stream-falls-back"),
        pytest.param("not json", (1920, 1080), id="unparseable-probe-falls-back"),
    ],
)
def test_source_display_size_is_rotation_aware(
    monkeypatch: pytest.MonkeyPatch, stdout: str, expected: tuple[int, int],
) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeProbe(stdout))
    assert helpers._source_display_size(Path("clip.mp4")) == expected


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [(1920, 1080, (1920, 1080)), (1080, 1920, (1080, 1920)), (1080, 1080, (1080, 1080)),
     (640, 480, (1920, 1080)), (480, 640, (1080, 1920))],
)
def test_project_dimensions_for_source_maps_orientation_to_canonical_size(
    monkeypatch: pytest.MonkeyPatch, width: int, height: int, expected: tuple[int, int],
) -> None:
    monkeypatch.setattr(helpers, "_source_display_size", lambda source: (width, height))
    assert helpers._project_dimensions_for_source(Path("clip.mp4")) == expected


def test_source_display_size_unreadable_source_falls_back_to_landscape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="no such file")

    monkeypatch.setattr(subprocess, "run", boom)
    assert helpers._source_display_size(Path("missing.mp4")) == (1920, 1080)


def test_create_project_gives_a_rotated_source_a_portrait_canvas(
    project_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A phone recording coded landscape with a +90 rotation must produce a portrait project."""
    data, _projects = project_dirs
    (data / "phone.mp4").write_bytes(b"video")
    monkeypatch.setattr(helpers, "_source_duration_frames", lambda source: 210)
    monkeypatch.setattr(subprocess, "run", _FakeProbe(_stream_json(width=1920, height=1080, rotation=90)))
    monkeypatch.setattr(compute, "_enqueue_transcribe", lambda slug, *, project=None: {"status": "queued"})

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/projects", json={"project": "phone_clip", "source": "phone"})

    response = _run(scenario())
    assert response.status_code == 201
    assert (response.json()["project"]["width"], response.json()["project"]["height"]) == (1080, 1920)


def test_create_project_keeps_an_unrotated_source_landscape(
    project_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, _projects = project_dirs
    (data / "camera.mp4").write_bytes(b"video")
    monkeypatch.setattr(helpers, "_source_duration_frames", lambda source: 210)
    monkeypatch.setattr(subprocess, "run", _FakeProbe(_stream_json(width=1920, height=1080)))
    monkeypatch.setattr(compute, "_enqueue_transcribe", lambda slug, *, project=None: {"status": "queued"})

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/projects", json={"project": "camera_clip", "source": "camera"})

    response = _run(scenario())
    assert response.status_code == 201
    assert (response.json()["project"]["width"], response.json()["project"]["height"]) == (1920, 1080)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe are required to generate and probe synthetic media")
def test_source_display_size_reads_a_synthetic_rotated_clip(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=0.1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True, text=True,
    )
    help_text = subprocess.run(["ffmpeg", "-h", "full"], check=True, capture_output=True, text=True).stdout
    rotation_input = ["-display_rotation", "90"] if "-display_rotation" in help_text else []
    subprocess.run(
        ["ffmpeg", "-y", *rotation_input, "-i", str(source), "-c", "copy",
         "-metadata:s:v:0", "rotate=90", str(rotated)],
        check=True, capture_output=True, text=True,
    )
    assert helpers._source_display_size(rotated) == (180, 320)
    assert helpers._project_dimensions_for_source(rotated) == (1080, 1920)
