"""Compute job routes on the FastAPI studio (scripts/studio_app.py).

In-process via httpx.ASGITransport, no GPU: compile is stubbed and the single-slot worker is disabled
so approval just proves the ENQUEUE. Covers render_preview_segment (inline → preview path), the
render_final human gate (needs-approval → approve → queued+enqueued), GET /api/jobs/{id}, reframe as
a persisted compute job, and the unavailable source_broll kind.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from pipeline import contracts
from pipeline import jobs as jobs_mod
from scripts import studio_app
from studio import compute, helpers
from studio import state as studio_state

T = TypeVar("T")
_PREFIX = "zzjob"


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _do(fn: Callable[[], Awaitable[T]]) -> T:
    return _run(fn())


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


def _write_project(name: str) -> None:
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"schemaVersion": 1, "name": name, "fps": 30, "width": 1920, "height": 1080,
           "revision": 1, "transcript": None, "tracks": []}
    (config.PROJECTS_DIR / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize("code,message", [
    ("CANCELLED", "The job was cancelled."),
    ("UNKNOWN_CODE", "The compute request could not be completed."),
])
def test_public_job_error_payload_is_unchanged(code: str, message: str) -> None:
    assert compute._public_job_error(jobs_mod.JobError(code, "private detail")) == {
        "error": message, "code": code,
    }


@pytest.mark.parametrize("action", ["transcribe", "reframe"])
def test_local_stage_publishes_canonical_sidecars(tmp_path, monkeypatch, action: str) -> None:
    from pipeline import contracts

    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(config, "REFRAME_DIR", tmp_path / "reframe")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    transcript = (
        b'{"language":"en","duration":1,"segments":[{"start":0,"end":1,"text":"ok",'
        b'"words":[{"word":"ok","start":0,"end":1,"score":1}]}]}'
    )
    reframe = (
        b'{"clip_id":"source","aspect_ratio":"9:16","face_coverage_pct":0,'
        b'"frame_crops":[{"frame_number":0,"timestamp":0,"cx":0.5,"cy":0.5,'
        b'"crop_x":0,"crop_y":0,"crop_w":1,"crop_h":1}]}'
    )

    def transcribe(_source, *, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        for suffix, payload in (("words.json", transcript), ("srt", b""), ("txt", b"")):
            (output_dir / f"source.{suffix}").write_bytes(payload)
        return contracts.StageResult("ok", "transcribe", "source", output_path=str(output_dir / "source.words.json"))

    def analyze(_source, *, fps, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "source.reframe.json").write_bytes(reframe)

    monkeypatch.setattr(compute.transcribe_stage, "transcribe", transcribe)
    monkeypatch.setattr(compute.reframe_stage, "analyze", analyze)
    job_id = jobs_mod.new_job(action, "demo", {})
    assert jobs_mod.compare_and_set_status(job_id, expected={"queued"}, status="running")[1]
    selected = "transcribe" if action == "transcribe" else "reframe"
    result = compute._local_action(job_id, selected, "demo", source)
    target = (
        config.TRANSCRIPTS_DIR / "source.words.json"
        if action == "transcribe" else config.REFRAME_DIR / "source.reframe.json"
    )
    assert result.status == "ok" and result.output_path == str(target)
    assert target.read_bytes() == (transcript if action == "transcribe" else reframe)
    job = jobs_mod.get(job_id)
    assert job is not None and job["status"] == "publishing"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch) -> Iterator[None]:
    # Isolate the job mirror + preview dirs to tmp so the suite never writes real outputs/ state.
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(config, "PREVIEW_DIR", tmp_path / "preview")

    def _wipe() -> None:
        if config.PROJECTS_DIR.exists():
            for f in config.PROJECTS_DIR.glob(f"{_PREFIX}*"):
                f.unlink(missing_ok=True)
        if config.DATA_DIR.exists():
            for f in config.DATA_DIR.glob(f"{_PREFIX}*"):
                f.unlink(missing_ok=True)
    _wipe()
    jobs_mod._registry.clear()
    yield
    jobs_mod._registry.clear()
    _wipe()


@pytest.mark.parametrize("status", ["interrupted", "unknown_execution"])
def test_recovered_job_is_terminal_visible_and_explicitly_retryable(status: str) -> None:
    presented = compute._job_presentation({
        "id": "recovered", "kind": "transcribe", "project": "project",
        "status": status, "executionPreference": "auto",
    })

    assert presented["status"] == status
    assert presented["progress"] == 1.0
    assert presented["canCancel"] is False
    assert presented["canRetry"] is True
    assert presented["errorCode"] in {"APPLICATION_RESTARTED", "EXECUTION_UNKNOWN"}


def test_render_preview_segment_runs_inline_and_returns_path(monkeypatch) -> None:
    name = f"{_PREFIX}_prev"
    _write_project(name)
    seen: dict = {}

    def _stub(doc, start_f, end_f, dest, *, project_dir=config.ROOT, output_root=None):
        seen.update(start=start_f, end=end_f, dest=str(dest))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00\x00\x00\x18ftypisompreview")
        return contracts.StageResult(status="ok", stage="compile", clip_id=name, output_path=str(dest))

    monkeypatch.setattr(compute.compile_stage, "compile_preview_segment", _stub)

    async def scenario() -> dict:
        async with _client() as client:
            r = await client.post("/api/jobs", json={
                "kind": "render_preview_segment", "project": name,
                "params": {"start_f": 90, "end_f": 180},
            })
            assert r.status_code == 200
            return r.json()

    body = _do(scenario)
    assert body["output"].startswith("/media/preview/") and body["output"].endswith(".mp4")
    assert seen["start"] == 90 and seen["end"] == 180
    # Encode is private; the common publisher moves it to PREVIEW_DIR only
    # after claiming the durable publication phase.
    assert str(config.STATE_HOME / "job-results") in seen["dest"]
    assert body["output"].split("/")[-1] != Path(seen["dest"]).name


def test_render_final_human_gate_then_approve_enqueues(monkeypatch) -> None:
    name = f"{_PREFIX}_final"
    _write_project(name)
    # Disable the real worker so approval only proves the enqueue (no ffmpeg, deterministic).
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)
    while not studio_state._JOB_Q.empty():
        studio_state._JOB_Q.get_nowait()

    async def scenario() -> tuple[dict, dict, dict]:
        async with _client() as client:
            created = (await client.post("/api/jobs", json={"kind": "render_final", "project": name})).json()
            jid = created["jobId"]
            got = (await client.get(f"/api/jobs/{jid}")).json()
            approved = (await client.post(f"/api/jobs/{jid}/approve")).json()
            return created, got, approved

    created, got, approved = _do(scenario)
    assert created["status"] == "needs-approval"
    assert got["status"] == "needs-approval" and got["kind"] == "render_final"
    assert approved["status"] == "queued"
    # the approved job id was placed on the single GPU slot
    assert studio_state._JOB_Q.get_nowait() == created["jobId"]


def test_render_final_ignores_legacy_compute_preference(monkeypatch) -> None:
    name = f"{_PREFIX}_compute"
    _write_project(name)
    # Unknown routing fields are ignored instead of becoming durable job settings.
    async def scenario() -> dict:
        async with _client() as client:
            response = await client.post("/api/jobs", json={
                "kind": "render_final", "project": name, "executionPreference": "legacy",
            })
            assert response.status_code == 200
            return response.json()

    created = _do(scenario)
    persisted = jobs_mod.get(created["jobId"])
    assert persisted is not None
    assert not {"executionPreference", "executor", "routingReason", "fallbackAllowed"} & persisted.keys()


def test_render_final_duplicate_post_reuses_durable_active_job(monkeypatch) -> None:
    name = f"{_PREFIX}_dedupe"
    _write_project(name)

    async def scenario() -> tuple[dict, dict]:
        async with _client() as client:
            first = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            jobs_mod._registry.clear()  # prove a restart still consults the durable mirror
            second = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            assert first.status_code == second.status_code == 200
            return first.json(), second.json()

    first, second = _do(scenario)
    assert second == {"jobId": first["jobId"], "status": "needs-approval"}


def test_render_final_duplicate_post_reuses_completed_output_job() -> None:
    name = f"{_PREFIX}_dedupe_done"
    _write_project(name)

    async def scenario() -> tuple[dict, dict]:
        async with _client() as client:
            created = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            assert created.status_code == 200
            jobs_mod.set(created.json()["jobId"], status="done", output="/media/compiled/finished.mp4")
            repeated = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            assert repeated.status_code == 200
            return created.json(), repeated.json()

    created, repeated = _do(scenario)
    assert repeated["jobId"] == created["jobId"] and repeated["status"] == "done"


def test_render_final_duplicate_post_reuses_publishing_job() -> None:
    """A publication in progress retains its deterministic render identity."""
    name = f"{_PREFIX}_dedupe_publishing"
    _write_project(name)

    async def scenario() -> tuple[dict, dict]:
        async with _client() as client:
            created = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            assert created.status_code == 200
            jobs_mod.set(created.json()["jobId"], status="publishing")
            jobs_mod._registry.clear()  # restart: the durable state is authoritative
            repeated = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            assert repeated.status_code == 200
            return created.json(), repeated.json()

    created, repeated = _do(scenario)
    assert repeated["jobId"] == created["jobId"] and repeated["status"] == "publishing"


def test_render_final_changed_revision_creates_a_new_job() -> None:
    name = f"{_PREFIX}_dedupe_revision"
    _write_project(name)

    async def scenario() -> tuple[dict, dict]:
        async with _client() as client:
            first = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            doc = json.loads((config.PROJECTS_DIR / f"{name}.json").read_text(encoding="utf-8"))
            doc["revision"] += 1
            (config.PROJECTS_DIR / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
            second = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            assert first.status_code == second.status_code == 200
            return first.json(), second.json()

    first, second = _do(scenario)
    assert first["jobId"] != second["jobId"]


def test_render_final_changed_character_look_creates_a_new_job(tmp_path, monkeypatch, client_rig: None) -> None:
    """A character's palette lives in the cast, not the document: changing it must re-render."""
    from pipeline import cast, library
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    garden = library.create_workspace("Garden")["id"]
    cast.create_character(garden, "Notes Guest", "sample-rig", palette="fern")
    name = f"{_PREFIX}_dedupe_cast"
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"schemaVersion": 3, "name": name, "fps": 30, "width": 1920, "height": 1080, "revision": 1,
           "transcript": None, "tracks": [], "workspaceId": garden,
           "scenes": [{"id": "s1", "title": "Open", "role": "Hook", "startWord": 0,
                       "shot": {"mode": "animation", "character": "notes-guest", "pose": "wave"}}]}
    (config.PROJECTS_DIR / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")

    async def post() -> dict:
        async with _client() as client:
            created = await client.post("/api/jobs", json={"kind": "render_final", "project": name})
            assert created.status_code == 200, created.text
            return created.json()

    red = _do(post)
    jobs_mod.set(red["jobId"], status="done", output="/media/compiled/red.mp4")
    assert _do(post)["jobId"] == red["jobId"], "the same look reuses the finished render"
    cast.update_character("notes-guest", approved=True)
    assert _do(post)["jobId"] == red["jobId"], "approval changes no pixel"
    cast.update_character("notes-guest", palette="fog")
    assert _do(post)["jobId"] != red["jobId"], "a new palette renders again"


def test_unknown_legacy_compute_preference_is_ignored() -> None:
    _write_project(f"{_PREFIX}_badcompute")
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/jobs", json={
                "kind": "render_final", "project": f"{_PREFIX}_badcompute",
                "executionPreference": "legacy-worker",
            })

    assert _do(scenario).status_code == 200


def test_unhashable_legacy_compute_preference_is_ignored() -> None:
    _write_project(f"{_PREFIX}_badcompute_list")
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/jobs", json={
                "kind": "render_final", "project": f"{_PREFIX}_badcompute_list",
                "executionPreference": [],
            })

    assert _do(scenario).status_code == 200


def test_render_final_ignores_agent_supplied_approved(monkeypatch) -> None:
    """even a body carrying approved:true (prompt-injection self-approval) is created
    needs-approval and NOT enqueued — only POST /approve can release it. The agent has no gate."""
    name = f"{_PREFIX}_noself"
    _write_project(name)
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)
    while not studio_state._JOB_Q.empty():
        studio_state._JOB_Q.get_nowait()

    async def scenario() -> dict:
        async with _client() as client:
            return (await client.post("/api/jobs", json={
                "kind": "render_final", "project": name, "approved": True,
            })).json()

    body = _do(scenario)
    assert body["status"] == "needs-approval"
    assert studio_state._JOB_Q.empty()   # NOT enqueued despite approved:true in the request


def test_approve_unknown_job_is_404() -> None:
    async def scenario() -> int:
        async with _client() as client:
            return (await client.post("/api/jobs/deadbeef/approve")).status_code

    assert _do(scenario) == 404


def test_get_unknown_job_is_404() -> None:
    async def scenario() -> int:
        async with _client() as client:
            return (await client.get("/api/jobs/deadbeef")).status_code

    assert _do(scenario) == 404


def test_local_job_routes_list_and_return_jobs() -> None:
    jid = jobs_mod.new_job("transcribe", f"{_PREFIX}_listing", {})
    persisted = jobs_mod.get(jid)
    assert persisted is not None
    expected = compute._job_presentation(persisted)

    async def scenario() -> None:
        async with _client() as client:
            listed = await client.get(f"/api/compute/jobs?project={_PREFIX}_listing")
            assert listed.status_code == 200
            assert listed.json()["jobs"] == [expected]
            detail = await client.get(f"/api/compute/jobs/{jid}")
            assert detail.status_code == 200
            assert detail.json() == expected

    _do(scenario)


def test_reframe_requires_a_source_while_source_broll_is_still_unavailable() -> None:
    async def scenario() -> tuple[dict, dict]:
        async with _client() as client:
            rf = (await client.post("/api/jobs", json={"kind": "reframe", "project": "zzjob_x"})).json()
            sb = (await client.post("/api/jobs", json={
                "kind": "source_broll", "project": "zzjob_x", "params": {"query": "ocean"},
            })).json()
            return rf, sb

    rf, sb = _do(scenario)
    assert rf == {"status": "error", "error": "no source", "code": "not_found"}
    assert sb == {"available": False, "reason": "not available"}


def test_reframe_is_a_persisted_compute_job(monkeypatch) -> None:
    name = f"{_PREFIX}_reframe"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / f"{name}.mp4").write_bytes(b"video")
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)

    async def scenario() -> dict:
        async with _client() as client:
            response = await client.post("/api/jobs", json={
                "kind": "reframe", "project": name, "executionPreference": "legacy",
            })
            assert response.status_code == 200
            return response.json()

    created = _do(scenario)
    assert created["status"] == "queued"
    persisted = jobs_mod.get(created["jobId"])
    assert persisted is not None and persisted["kind"] == "reframe"


def test_transcription_job_is_queued(monkeypatch) -> None:
    monkeypatch.setattr(compute.addons, "available", lambda name: name == "transcription")
    name = f"{_PREFIX}_transcribe"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / f"{name}.mp4").write_bytes(b"video")
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)

    async def scenario() -> dict:
        async with _client() as client:
            response = await client.post("/api/jobs", json={"kind": "transcribe", "project": name})
            assert response.status_code == 200
            return response.json()

    created = _do(scenario)
    assert created["status"] == "queued"


def test_missing_whisperx_is_structured_dependency_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(compute.transcribe_stage, "transcribe", lambda _source, **_kwargs: (_ for _ in ()).throw(
        ModuleNotFoundError("No module named whisperx")
    ))
    jid = jobs_mod.new_job("transcribe", f"{_PREFIX}_whisperx", {})
    monkeypatch.setattr(helpers, "_source", lambda _project: source)

    with pytest.raises(jobs_mod.JobError) as error:
        compute._run_compute_action(jid, jobs_mod.get(jid) or {})
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"


def test_legacy_whole_dict_jobs_route_intact() -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/jobs")

    r = _do(scenario)
    assert r.status_code == 200 and isinstance(r.json(), dict)


# ── preview window cap + detach-for-large + pruning ───────────────────────────
def test_large_preview_window_detaches_as_job(monkeypatch) -> None:
    """A window over the sync cutoff (but under the cap) DETACHES: returns {jobId,status} and enqueues
    on the single GPU slot, so the mcp_gpu bridge never blocks on a multi-minute encode."""
    name = f"{_PREFIX}_bigprev"
    _write_project(name)
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)
    while not studio_state._JOB_Q.empty():
        studio_state._JOB_Q.get_nowait()

    async def scenario() -> dict:
        async with _client() as client:
            return (await client.post("/api/jobs", json={
                "kind": "render_preview_segment", "project": name,
                "params": {"start_f": 0, "end_f": 300},  # 300 > sync cutoff (90), <= cap (450)
            })).json()

    body = _do(scenario)
    assert "jobId" in body and body["status"] == "queued"
    jid = body["jobId"]
    assert studio_state._JOB_Q.get_nowait() == jid
    job = jobs_mod.get(jid)
    assert job is not None
    assert job["kind"] == "render_preview_segment"
    assert job["meta"] == {"start_f": 0, "end_f": 300}


def test_preview_window_over_cap_is_rejected() -> None:
    name = f"{_PREFIX}_capprev"
    _write_project(name)

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/jobs", json={
                "kind": "render_preview_segment", "project": name,
                "params": {"start_f": 0, "end_f": 1000},  # 1000 > cap (450)
            })

    r = _do(scenario)
    assert r.status_code == 400
    assert "too large" in r.json()["error"]


def test_inline_preview_prunes_dir_to_keep_newest(monkeypatch) -> None:
    name = f"{_PREFIX}_prune"
    _write_project(name)
    monkeypatch.setattr(config, "PREVIEW_KEEP", 3)
    config.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    # Seed 5 OLD preview files with explicitly older mtimes so ordering is deterministic.
    for i in range(5):
        f = config.PREVIEW_DIR / f"old{i}.mp4"
        f.write_bytes(b"x")
        os.utime(f, (1000 + i, 1000 + i))

    def _stub(doc, start_f, end_f, dest, *, project_dir=config.ROOT, output_root=None):  # noqa: ANN001
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00\x00\x00\x18ftypisomnew")  # the just-written preview is the newest file
        return contracts.StageResult(status="ok", stage="compile", clip_id=name, output_path=str(dest))

    monkeypatch.setattr(compute.compile_stage, "compile_preview_segment", _stub)

    async def scenario() -> dict:
        async with _client() as client:
            return (await client.post("/api/jobs", json={
                "kind": "render_preview_segment", "project": name,
                "params": {"start_f": 0, "end_f": 60},  # <= sync cutoff ⇒ inline + prune
            })).json()

    body = _do(scenario)
    new_name = body["output"].split("/")[-1]
    remaining = {p.name for p in config.PREVIEW_DIR.glob("*.mp4")}
    assert len(remaining) == 3           # pruned to PREVIEW_KEEP
    assert new_name in remaining         # the freshly written preview survives


# ── detached job lifecycle (_run_detached_job) — running→done/error + SSE marshalling ──
def _job(jid: str) -> dict:
    job = jobs_mod.get(jid)
    assert job is not None
    return job


def test_detached_render_final_reaches_done(monkeypatch) -> None:
    events: list[dict] = []
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda p, m: events.append(m))
    monkeypatch.setattr(
        compute,
        "_run_render_compute",
        lambda jid, job, *, preview: {"output": "/media/compiled/x.mp4", "errors": []},
    )
    jid = jobs_mod.new_job("render_final", f"{_PREFIX}_life", {})
    compute._run_detached_job(jid, _job(jid))
    job = _job(jid)
    assert job["status"] == "done" and job["output"] == "/media/compiled/x.mp4"
    assert [e["status"] for e in events] == ["running", "done"]
    assert events[-1]["kind"] == "render_final" and events[-1]["id"] == jid


def test_completed_export_output_serves_rendered_bytes(tmp_path, monkeypatch) -> None:
    name = f"{_PREFIX}_export"
    _write_project(name)
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda p, m: None)
    payload = b"\x00\x00\x00\x18ftypisomexport"
    encoded: list[Path] = []

    def _compile(doc, dest, *, project_dir, output_root):
        encoded.append(Path(dest))
        Path(dest).write_bytes(payload)
        return contracts.StageResult(status="ok", stage="compile", clip_id=name, output_path=str(dest))

    monkeypatch.setattr(compute.compile_stage, "compile_project", _compile)
    jid = jobs_mod.new_job("render_final", name, {})
    jobs_mod.set(jid, executionPreference="legacy")
    compute._run_detached_job(jid, _job(jid))

    async def scenario() -> None:
        async with _client() as client:
            job_response = await client.get(f"/api/jobs/{jid}")
            assert job_response.status_code == 200
            job = job_response.json()
            assert job["status"] == "done"
            assert job["output"] == f"/media/compiled/{name}.mp4"
            response = await client.get(job["output"])
            assert response.status_code == 200
            assert response.content == payload
            assert response.headers["Content-Type"] == "video/mp4"
            assert response.headers["Accept-Ranges"] == "bytes"
            ranged = await client.get(job["output"], headers={"Range": "bytes=4-11"})
            assert ranged.status_code == 206
            assert ranged.content == payload[4:12]
            assert ranged.headers["Content-Range"] == f"bytes 4-11/{len(payload)}"
            assert ranged.headers["Content-Length"] == "8"

    _do(scenario)
    assert len(encoded) == 1
    assert encoded[0].is_relative_to(config.STATE_HOME / "job-results" / jid)
    assert (config.FINAL_DIR / f"{name}.compiled.mp4").read_bytes() == payload


def test_detached_render_final_reaches_error(monkeypatch) -> None:
    events: list[dict] = []
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda p, m: events.append(m))
    monkeypatch.setattr(
        compute,
        "_run_render_compute",
        lambda jid, job, *, preview: {"errors": ["ffmpeg boom"]},
    )
    jid = jobs_mod.new_job("render_final", f"{_PREFIX}_lifeerr", {})
    compute._run_detached_job(jid, _job(jid))
    job = _job(jid)
    assert job["status"] == "error" and "ffmpeg boom" in job["error"]
    assert [e["status"] for e in events] == ["running", "error"]


def test_detached_unknown_kind_is_error(monkeypatch) -> None:
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda p, m: None)
    jid = jobs_mod.new_job("bogus_kind", f"{_PREFIX}_unk", {})
    compute._run_detached_job(jid, _job(jid))
    job = _job(jid)
    assert job["status"] == "error" and "unknown job kind" in job["error"]


def test_detached_preview_reaches_done(monkeypatch) -> None:
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda p, m: None)
    monkeypatch.setattr(
        compute,
        "_run_render_compute",
        lambda jid, job, *, preview: {"output": f"/media/preview/{jid}.mp4"},
    )
    jid = jobs_mod.new_job("render_preview_segment", f"{_PREFIX}_pv", {"start_f": 0, "end_f": 300})
    compute._run_detached_job(jid, _job(jid))
    job = _job(jid)
    assert job["status"] == "done" and job["output"] == f"/media/preview/{jid}.mp4"


def test_detached_job_survives_a_raising_compile(monkeypatch) -> None:
    """A crash inside a job is caught → status error, not a dead single-slot worker."""
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda p, m: None)

    calls: list[tuple[str, bool]] = []

    def _boom(_jid, _job, *, preview):
        calls.append((_jid, preview))
        raise RuntimeError("kaboom")

    monkeypatch.setattr(compute, "_run_render_compute", _boom)
    jid = jobs_mod.new_job("render_final", f"{_PREFIX}_raise", {})
    compute._run_detached_job(jid, _job(jid))
    assert _job(jid)["status"] == "error"
    assert _job(jid)["error"] == "kaboom"
    assert calls == [(jid, False)]


def test_cancel_before_worker_pickup_executes_no_work(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda p, m: None)
    monkeypatch.setattr(
        compute, "_run_compute_action",
        lambda jid, job: calls.append(jid),
    )
    jid = jobs_mod.new_job("transcribe", f"{_PREFIX}_cancelbefore", {})
    _, changed = jobs_mod.compare_and_set_status(
        jid, expected={"queued"}, status="cancelled",
    )
    assert changed

    compute._run_detached_job(jid, _job(jid))

    assert calls == []
    assert _job(jid)["status"] == "cancelled"


def test_cancel_during_compute_prevents_result_adoption(monkeypatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda p, m: None)

    def _compute(jid, job):  # noqa: ANN001, ANN202
        entered.set()
        assert release.wait(5)
        return contracts.StageResult(
            status="ok", stage="transcribe", clip_id="clip", output_path="must-not-adopt",
        )

    monkeypatch.setattr(compute, "_run_compute_action", _compute)
    jid = jobs_mod.new_job("transcribe", f"{_PREFIX}_cancelduring", {})
    thread = threading.Thread(target=compute._run_detached_job, args=(jid, _job(jid)))
    thread.start()
    assert entered.wait(5)
    _, changed = jobs_mod.compare_and_set_status(
        jid, expected={"running"}, status="cancelled",
    )
    assert changed
    release.set()
    thread.join(5)

    job = _job(jid)
    assert not thread.is_alive()
    assert job["status"] == "cancelled"
    assert "output" not in job


def test_approve_cancel_race_never_revives_or_executes_job(monkeypatch) -> None:
    while not studio_state._JOB_Q.empty():
        studio_state._JOB_Q.get_nowait()
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)
    calls: list[str] = []
    monkeypatch.setattr(
        compute, "_run_render_compute",
        lambda jid, job, *, preview: calls.append(jid),
    )
    jid = jobs_mod.new_job(
        "render_final", f"{_PREFIX}_approvecancel", {}, status="needs-approval",
    )
    barrier = threading.Barrier(3)
    responses: list[int] = []

    def _post(path: str) -> None:
        barrier.wait()

        async def scenario() -> int:
            async with _client() as client:
                return (await client.post(path)).status_code

        responses.append(_do(scenario))

    approve = threading.Thread(target=_post, args=(f"/api/jobs/{jid}/approve",))
    cancel = threading.Thread(target=_post, args=(f"/api/jobs/{jid}/cancel",))
    approve.start()
    cancel.start()
    barrier.wait()
    approve.join(5)
    cancel.join(5)

    assert sorted(responses) in ([200, 200], [200, 409])
    assert _job(jid)["status"] == "cancelled"
    while not studio_state._JOB_Q.empty():
        compute._run_detached_job(jid, _job(jid))
        studio_state._JOB_Q.get_nowait()
    assert calls == []
    assert _job(jid)["status"] == "cancelled"
