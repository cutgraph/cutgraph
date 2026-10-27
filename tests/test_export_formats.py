"""Derived export-format render docs.

The Export sheet builds a `<parent>__9x16` / `__1x1` / `__16x9` render doc client-side (own width/
height/crop/captions, everything else copied from the working project) and persists it through the
same revision-gated `/api/project/{p}` route as the working document. The compound name keeps
the derived document separate while allowing the existing persistence and render routes to serve it.
These tests prove that path end to end: the compound doc round-trips through PUT/GET, and a
render_final job on it reaches the compiler with ITS OWN dimensions/crop/captions, not the parent's.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from pipeline import contracts
from pipeline import jobs as jobs_mod
from scripts import studio_app
from studio import compute

T = TypeVar("T")
_PREFIX = "zzexport"


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _do(fn: Callable[[], Awaitable[T]]) -> T:
    return _run(fn())


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


def _write_project(name: str, *, width: int = 1920, height: int = 1080) -> None:
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"schemaVersion": 1, "name": name, "fps": 30, "width": width, "height": height,
           "revision": 1, "transcript": None, "tracks": []}
    (config.PROJECTS_DIR / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(config, "PREVIEW_DIR", tmp_path / "preview")
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)

    def _wipe() -> None:
        if config.PROJECTS_DIR.exists():
            for f in config.PROJECTS_DIR.glob(f"{_PREFIX}*"):
                f.unlink(missing_ok=True)

    _wipe()
    jobs_mod._registry.clear()
    yield
    jobs_mod._registry.clear()
    _wipe()


def test_derived_doc_round_trips_through_project_put() -> None:
    parent = f"{_PREFIX}_parent"
    compound = f"{parent}__9x16"

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            put = await client.put(f"/api/project/{compound}", json={
                "baseRevision": 0,
                "project": {
                    "schemaVersion": 1, "name": compound, "fps": 30, "width": 1080, "height": 1920,
                    "revision": 0, "transcript": None,
                    "crop": {"mode": "center"}, "captions": {"enabled": True}, "tracks": [],
                },
            })
            got = await client.get(f"/api/project/{compound}")
            return put, got

    put, got = _do(scenario)
    assert put.status_code == 200
    body = put.json()
    assert body["revision"] == 1
    assert body["project"]["name"] == compound
    assert body["project"]["width"] == 1080 and body["project"]["height"] == 1920
    assert body["project"]["crop"] == {"mode": "center"}
    assert body["project"]["captions"] == {"enabled": True}
    assert got.json()["project"] == body["project"]


def test_derived_doc_accepts_a_reframe_crop_reference() -> None:
    compound = f"{_PREFIX}_reframe__9x16"

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.put(f"/api/project/{compound}", json={
                "baseRevision": 0,
                "project": {
                    "schemaVersion": 1, "name": compound, "fps": 30, "width": 1080, "height": 1920,
                    "revision": 0, "transcript": None,
                    "crop": {"mode": "reframe", "metaPath": "outputs/reframe/demo-talk.reframe.json"},
                    "captions": {"enabled": False}, "tracks": [],
                },
            })

    response = _do(scenario)
    assert response.status_code == 200
    assert response.json()["project"]["crop"] == {
        "mode": "reframe", "metaPath": "outputs/reframe/demo-talk.reframe.json",
    }


def test_render_final_on_a_derived_doc_uses_its_own_dimensions(monkeypatch) -> None:
    parent = f"{_PREFIX}_render_parent"
    compound = f"{parent}__9x16"
    _write_project(parent, width=1920, height=1080)
    seen: dict = {}

    def _stub(doc, dest, *, project_dir=config.STATE_HOME, output_root=None):
        seen["doc"] = doc
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00\x00\x00\x18ftypisom")
        return contracts.StageResult(status="ok", stage="compile", clip_id=compound, output_path=str(dest))

    monkeypatch.setattr(compute.compile_stage, "compile_project", _stub)

    async def scenario() -> tuple[dict, dict, dict]:
        async with _client() as client:
            put = await client.put(f"/api/project/{compound}", json={
                "baseRevision": 0,
                "project": {
                    "schemaVersion": 1, "name": compound, "fps": 30, "width": 1080, "height": 1920,
                    "revision": 0, "transcript": None,
                    "crop": {"mode": "center"}, "captions": {"enabled": True}, "tracks": [],
                },
            })
            assert put.status_code == 200
            created = (await client.post("/api/jobs", json={"kind": "render_final", "project": compound})).json()
            jid = created["jobId"]
            approved = (await client.post(f"/api/jobs/{jid}/approve")).json()
            # The single-slot worker thread is disabled in tests (test_jobs_route.py's own pattern);
            # mirror _run_detached_job's own "claim running" transition, then invoke the same render
            # function it would have dispatched, directly and synchronously, to observe exactly what
            # doc the compiler receives for this derived project.
            jobs_mod.compare_and_set_status(jid, expected={"queued"}, status="running")
            result = compute._run_render_compute(jid, jobs_mod.get(jid) or {}, preview=False)
            return created, approved, result

    created, approved, result = _do(scenario)
    assert created["status"] == "needs-approval"
    assert approved["status"] == "queued"
    assert result.get("errors") == []
    assert seen["doc"]["width"] == 1080
    assert seen["doc"]["height"] == 1920
    assert seen["doc"]["crop"] == {"mode": "center"}
    assert seen["doc"]["captions"] == {"enabled": True}
    assert seen["doc"]["name"] == compound
