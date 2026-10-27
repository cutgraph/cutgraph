"""Durable editor transcript lifecycle projection."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from pipeline import jobs as jobs_mod
from scripts import studio_app

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


@pytest.fixture()
def transcript_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    data = tmp_path / "data" / "source"
    projects = tmp_path / "outputs" / "projects"
    transcripts = tmp_path / "outputs" / "transcripts"
    data.mkdir(parents=True)
    projects.mkdir(parents=True)
    (data / "interview.mp4").write_bytes(b"video")
    (projects / "launch.json").write_text(json.dumps({
        "name": "launch", "revision": 0, "fps": 30, "width": 1920, "height": 1080, "schemaVersion": 1,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [{"id": "source", "media": {"source": "data/source/interview.mp4"}, "srcInF": 0, "srcOutF": 30, "tlStartF": 0}]}],
    }), encoding="utf-8")
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PROJECTS_DIR", projects)
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", transcripts)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "outputs" / "jobs")
    return "launch"


def test_transcript_lifecycle_distinguishes_queued_failed_and_ready(transcript_project: str) -> None:
    queued = jobs_mod.new_job("transcribe", transcript_project, {"sourceSlug": "interview"})

    async def fetch() -> dict:
        async with _client() as client:
            response = await client.get(f"/api/project/{transcript_project}/transcription")
            assert response.status_code == 200
            return response.json()

    initial = _run(fetch())
    assert initial["state"] == "queued"

    jobs_mod.set(queued, status="error", code="DEPENDENCY_UNAVAILABLE")
    failed = _run(fetch())
    assert failed["state"] == "failed"
    assert failed["job"]["canRetry"] is True
    assert "unavailable" in failed["message"].lower()

    config.TRANSCRIPTS_DIR.mkdir(parents=True)
    (config.TRANSCRIPTS_DIR / "interview.words.json").write_text('{"segments": []}', encoding="utf-8")
    ready = _run(fetch())
    assert ready["state"] == "ready"
