"""Sanitized worker status and auditable job-action routes."""
from __future__ import annotations

import asyncio
import json
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
from studio.routes import jobs as job_routes

T = TypeVar("T")


def _do(fn: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(fn())  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=studio_app.app),
        base_url="http://studio.test",
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data" / "source")
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "outputs" / "projects")
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "outputs" / "transcripts")
    monkeypatch.setattr(studio_state, "_MAIN_LOOP", None)
    jobs_mod._registry.clear()
    studio_state._JOBS.clear()
    yield
    jobs_mod._registry.clear()
    studio_state._JOBS.clear()
    studio_state._MAIN_LOOP = None


def test_job_projection_preserves_lifecycle_vocabulary_and_timestamps() -> None:
    base = {
        "id": "job-lifecycle",
        "kind": "render_final",
        "status": "running",
        "createdAt": "2026-08-25T12:00:00.123456Z",
        "createdOrdinal": 42,
        "started": 1_756_123_200.5,
        "ended": 1_756_123_201.5,
        "eventSequence": 7,
    }
    projected = compute._job_presentation(base)
    assert projected["createdAt"] == base["createdAt"]
    assert projected["createdOrdinal"] == 42
    assert projected["startedAt"].endswith("Z")
    assert projected["completedAt"].endswith("Z")

    for status in ("succeeded", "failed", "cancelled", "retrying"):
        candidate = {**base, "status": status, "executionPreference": "auto"}
        result = compute._job_presentation(candidate)
        assert result["status"] == status
    stale = compute._job_presentation({**base, "status": "error", "code": "STALE_REVISION"})
    assert stale["status"] == "stale"
    assert stale["canRetry"] is True


def test_failed_public_status_retry_matches_route_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = jobs_mod.new_job("transcribe", "demo", {}, status="failed")
    monkeypatch.setattr(
        job_routes,
        "_retry_job",
        lambda job: {
            "id": "child", "status": "retrying",
        },
    )

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post(f"/api/jobs/{job_id}/retry")

    response = _do(scenario)
    assert response.status_code == 200


def test_job_projection_hides_raw_error_and_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)
    source = config.DATA_DIR / "demo.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    original = jobs_mod.new_job(
        "transcribe", "demo", {"path": r"C:\private\source.mp4"}
    )
    jobs_mod.compare_and_set_status(original, expected={"queued"}, status="cancelled")
    jobs_mod.set(original, error=r"C:\private\key", code="WORKER_UNAVAILABLE")

    async def scenario() -> dict:
        async with _client() as client:
            listed = (await client.get("/api/compute/jobs?project=demo")).json()
            return listed

    listed = _do(scenario)
    encoded = str(listed)
    assert "private" not in encoded and "path" not in encoded
    old = listed["jobs"][0]
    persisted_snapshot = jobs_mod.get(original)
    assert persisted_snapshot is not None
    assert old["createdAt"] == persisted_snapshot["createdAt"]
    assert "canFallback" not in old
    persisted = jobs_mod.get(original)
    assert persisted is not None


def test_queued_compute_event_is_full_presentation_and_precedes_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compute.addons, "available", lambda name: name == "transcription")
    source = config.DATA_DIR / "demo.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    events: list[tuple[str, dict]] = []
    queued_after_event: list[bool] = []

    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)
    monkeypatch.setattr(
        studio_state.hub,
        "publish_event",
        lambda project, event: events.append((project, event)),
    )
    monkeypatch.setattr(
        studio_state._JOB_Q,
        "put",
        lambda job_id: queued_after_event.append(bool(events)),
    )

    result = compute._enqueue_compute_action(
        "transcribe", "demo", source_slug="demo"
    )

    assert result["status"] == "queued"
    assert queued_after_event == [True]
    assert len(events) == 1
    project, event = events[0]
    assert project == "demo"
    assert event == {
        "type": "job",
        "id": result["jobId"],
        "kind": "transcribe",
        "status": "queued",
        "progress": 0.05,
        "progressStage": "Queued",
        "sequence": 1,
        "canCancel": True,
        "canRetry": False,
    }


def test_approval_publishes_full_queued_event_before_fast_worker_can_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval must not let a fast worker turn queued into running before SSE sees it."""
    events: list[tuple[str, dict]] = []
    observed_before_queue: list[list[tuple[str, dict]]] = []
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)
    monkeypatch.setattr(
        studio_state.hub,
        "publish_event",
        lambda project, event: events.append((project, event)),
    )

    def fast_worker(job_id: str) -> None:
        # Model the single-slot worker immediately claiming work as soon as it
        # enters the queue.  The queued SSE event must already be complete.
        observed_before_queue.append(list(events))
        _updated, claimed = jobs_mod.compare_and_set_status(
            job_id, expected={"queued"}, status="running"
        )
        assert claimed

    monkeypatch.setattr(studio_state._JOB_Q, "put", fast_worker)
    job_id = jobs_mod.new_job("render_final", "approved-render", {}, status="needs-approval")

    async def scenario() -> dict:
        async with _client() as client:
            response = await client.post(f"/api/jobs/{job_id}/approve")
            assert response.status_code == 200
            return response.json()

    response = _do(scenario)
    assert response["status"] == "queued"
    assert len(observed_before_queue) == 1
    assert observed_before_queue[0] == events
    assert events == [
        (
            "approved-render",
            {
                "type": "job",
                "id": job_id,
                "kind": "render_final",
                "status": "queued",
                "progress": 0.05,
                "progressStage": "Queued",
                "sequence": 1,
                "canCancel": True,
                "canRetry": False,
            },
        )
    ]
    persisted = jobs_mod.get(job_id)
    assert persisted is not None
    assert persisted["eventSequence"] == 1
    assert persisted["status"] == "running"


async def _job_transitions(
    queue: asyncio.Queue[dict], job_id: str
) -> list[dict]:
    transitions: list[dict] = []
    while len(transitions) < 3:
        event = await asyncio.wait_for(queue.get(), timeout=2)
        if event.get("type") == "job" and event.get("id") == job_id:
            transitions.append(event)
    return transitions


def _run_jobs_in_threads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_source: str | None = None,
) -> list[threading.Thread]:
    threads: list[threading.Thread] = []
    monkeypatch.setattr(compute.addons, "available", lambda name: name == "transcription")
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)

    def dispatch(job_id: str) -> None:
        job = jobs_mod.get(job_id)
        assert job is not None
        thread = threading.Thread(
            target=compute._run_detached_job,
            args=(job_id, job),
        )
        threads.append(thread)
        thread.start()

    monkeypatch.setattr(studio_state._JOB_Q, "put", dispatch)
    def run_compute(job_id: str, job: dict) -> contracts.StageResult:
        source = compute._compute_job_source(job)
        if expected_source is not None:
            assert source is not None
            assert source.name == expected_source
        return contracts.StageResult(
            status="ok",
            stage=str(job["kind"]),
            clip_id=str(job["project"]),
            output_path=f"/media/{job_id}.json",
        )

    monkeypatch.setattr(compute, "_run_compute_action", run_compute)
    monkeypatch.setattr(
        compute,
        "_run_render_compute",
        lambda job_id, job, *, preview: {
            "output": f"/media/{job_id}.mp4",
            "errors": [],
        },
    )
    return threads


def _assert_complete_sequence(events: list[dict], first_status: str = "queued") -> None:
    assert [event["status"] for event in events] == [first_status, "running", "done"]
    assert [event["sequence"] for event in events] == [1, 2, 3]


def _write_speech_project_source(project: str, source_ref: str) -> None:
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.PROJECTS_DIR / f"{project}.json").write_text(
        json.dumps({
            "name": project,
            "revision": 1,
            "tracks": [{
                "id": "speech",
                "kind": "speech",
                "clips": [{
                    "id": "source",
                    "media": {"source": source_ref, "proxy": None},
                    "srcInF": 0,
                    "srcOutF": 30,
                    "tlStartF": 0,
                    "gainDb": 0.0,
                }],
            }],
        }),
        encoding="utf-8",
    )


def _write_renamed_speech_project(project: str, source_slug: str) -> None:
    _write_speech_project_source(project, f"data/source/{source_slug}.mp4")


@pytest.mark.parametrize("kind", ("transcribe", "reframe"))
def test_manual_renamed_project_compute_uses_persisted_speech_source_and_reconciles(
    kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = config.DATA_DIR / "intro.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    # A real alternative upload proves request params cannot steer the job
    # away from the persisted speech source.
    (config.DATA_DIR / "untrusted.mp4").write_bytes(b"other-video")
    _write_renamed_speech_project("summer_cut", "intro")
    threads = _run_jobs_in_threads(monkeypatch, expected_source="intro.mp4")

    async def scenario() -> tuple[list[dict], dict]:
        assert studio_state._MAIN_LOOP is None
        queue = studio_state.hub.subscribe("summer_cut")
        try:
            async with _client() as client:
                response = await client.post(
                    "/api/jobs",
                    json={
                        "kind": kind,
                        "project": "summer_cut",
                        # The request cannot select a path/source; only the
                        # persisted speech clip controls stage input.
                        "params": {"sourceSlug": "untrusted"},
                    },
                )
                assert response.status_code == 200, response.text
                job_id = response.json()["jobId"]
                events = await _job_transitions(queue, job_id)
                listed = (await client.get(
                    "/api/compute/jobs?project=summer_cut"
                )).json()
                return events, listed
        finally:
            studio_state.hub.unsubscribe("summer_cut", queue)

    events, listed = _do(scenario)
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    _assert_complete_sequence(events)
    assert len(listed["jobs"]) == 1
    assert listed["jobs"][0]["id"] == events[0]["id"]
    persisted = jobs_mod.get(events[0]["id"])
    assert persisted is not None
    assert persisted["project"] == "summer_cut"
    assert persisted["meta"] == {"sourceSlug": "intro"}


@pytest.mark.parametrize(
    ("source_ref", "duplicate_stem"),
    (
        ("data/source/nested/intro.mp4", False),
        ("data/source/../source/intro.mp4", False),
        ("../source/intro.mp4", False),
        ("private/secret.mp4", False),
        ("__absolute__", False),
        ("data/source/intro.txt", False),
        ("data/source/intro bad.mp4", False),
        ("data/source/intro.mp4", True),
    ),
)
def test_manual_compute_rejects_noncanonical_or_ambiguous_project_source(
    source_ref: str, duplicate_stem: bool,
) -> None:
    source = config.DATA_DIR / "intro.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    if duplicate_stem:
        (config.DATA_DIR / "intro.mov").write_bytes(b"other-video")
    if source_ref == "__absolute__":
        source_ref = str(source.resolve())
    _write_speech_project_source("summer_cut", source_ref)

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post(
                "/api/jobs",
                json={
                    "kind": "transcribe",
                    "project": "summer_cut",
                },
            )

    response = _do(scenario)
    assert response.status_code == 404
    assert response.json() == {"status": "error", "error": "no source", "code": 'not_found'}
    assert jobs_mod.all_persisted() == {}


def test_fresh_process_project_create_delivers_full_job_sse_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = config.DATA_DIR / "intro.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    monkeypatch.setattr(helpers, "_source_duration_frames", lambda _source: 30)
    threads = _run_jobs_in_threads(monkeypatch, expected_source="intro.mp4")

    async def scenario() -> tuple[list[dict], dict]:
        assert studio_state._MAIN_LOOP is None
        queue = studio_state.hub.subscribe("summer_cut")
        try:
            async with _client() as client:
                response = await client.post(
                    "/api/projects",
                    json={
                        "project": "summer_cut",
                        "source": "intro",
                    },
                )
                assert response.status_code == 201, response.text
                job_id = response.json()["transcription"]["jobId"]
                assert studio_state._MAIN_LOOP is asyncio.get_running_loop()
                events = await _job_transitions(queue, job_id)
                reconciled = (
                    await client.get("/api/compute/jobs?project=summer_cut")
                ).json()
                assert (
                    await client.get("/api/compute/jobs?project=intro")
                ).json() == {"jobs": []}
                return events, reconciled
        finally:
            studio_state.hub.unsubscribe("summer_cut", queue)

    events, reconciled = _do(scenario)
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    _assert_complete_sequence(events)
    assert len(reconciled["jobs"]) == 1
    assert reconciled["jobs"][0]["id"] == events[0]["id"]
    assert reconciled["jobs"][0]["status"] == "done"
    assert "intro" not in str(reconciled)
    persisted = jobs_mod.get(events[0]["id"])
    assert persisted is not None
    assert persisted["project"] == "summer_cut"
    assert persisted["meta"] == {"sourceSlug": "intro"}


def test_fresh_process_retry_delivers_full_job_sse_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = config.DATA_DIR / "intro.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    original = jobs_mod.new_job(
        "transcribe", "summer_cut", {"sourceSlug": "intro"}, status="error"
    )
    threads = _run_jobs_in_threads(monkeypatch, expected_source="intro.mp4")

    async def scenario() -> list[dict]:
        assert studio_state._MAIN_LOOP is None
        queue = studio_state.hub.subscribe("summer_cut")
        try:
            async with _client() as client:
                response = await client.post(f"/api/jobs/{original}/retry")
            assert response.status_code == 200, response.text
            job_id = response.json()["id"]
            assert studio_state._MAIN_LOOP is asyncio.get_running_loop()
            return await _job_transitions(queue, job_id)
        finally:
            studio_state.hub.unsubscribe("summer_cut", queue)

    events = _do(scenario)
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    _assert_complete_sequence(events, first_status="retrying")
    retried = jobs_mod.get(events[0]["id"])
    assert retried is not None
    assert retried["project"] == "summer_cut"
    assert retried["meta"] == {"sourceSlug": "intro"}
    parent = jobs_mod.get(original)
    assert parent is not None
    assert parent["retryState"] == "retrying"
    assert parent["retryChildJobId"] == retried["id"]
    assert retried["startedAt"] and retried["completedAt"]
    assert retried["createdAt"].count(".") == 1


def test_fresh_process_approval_delivers_full_job_sse_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = jobs_mod.new_job(
        "render_final", "demo", {}, status="needs-approval"
    )
    threads = _run_jobs_in_threads(monkeypatch)

    async def scenario() -> list[dict]:
        assert studio_state._MAIN_LOOP is None
        queue = studio_state.hub.subscribe("demo")
        try:
            async with _client() as client:
                response = await client.post(f"/api/jobs/{job_id}/approve")
            assert response.status_code == 200, response.text
            assert studio_state._MAIN_LOOP is asyncio.get_running_loop()
            return await _job_transitions(queue, job_id)
        finally:
            studio_state.hub.unsubscribe("demo", queue)

    events = _do(scenario)
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    _assert_complete_sequence(events)


@pytest.mark.parametrize("stored_status", ["fallback", "reconnected", "future-state"])
def test_legacy_job_record_loads_and_recovers_without_routing(tmp_path: Path, stored_status: str) -> None:
    record = {
        "id": "legacy-job", "kind": "transcribe", "project": "demo",
        "status": stored_status, "meta": {}, "createdAt": 1700000000.0,
        "executionPreference": "legacy", "executor": "legacy",
        "routingReason": "legacy value", "fallbackAllowed": True,
        "attemptState": "fallback", "retryState": "fallback",
    }
    config.JOBS_DIR.mkdir(parents=True)
    (config.JOBS_DIR / "legacy-job.json").write_text(json.dumps(record), encoding="utf-8")
    jobs_mod._registry.clear()
    loaded = jobs_mod.get("legacy-job")
    assert loaded is not None
    assert loaded == record
    assert loaded["executionPreference"] == record["executionPreference"]
    assert jobs_mod.recover_interrupted()["legacy-job"]["status"] == "unknown_execution"
    recovered = jobs_mod.get("legacy-job")
    assert recovered is not None
    assert recovered["recoveryPreviousStatus"] == stored_status
    for key, value in record.items():
        if key != "status":
            assert recovered[key] == value
    assert json.loads((config.JOBS_DIR / "legacy-job.json").read_text()) == recovered
    assert jobs_mod.recover_interrupted() == {}

    async def scenario() -> None:
        async with _client() as client:
            detail = await client.get("/api/compute/jobs/legacy-job")
            listing = await client.get("/api/compute/jobs?project=demo")
            assert detail.status_code == listing.status_code == 200
            assert listing.json()["jobs"] == [detail.json()]
            public = detail.json()
            assert public["status"] == "unknown_execution"
            assert public["progressStage"] == "Execution unknown"
            assert public["canCancel"] is False
            assert public["canRetry"] is True
            assert not {"executionPreference", "executor", "routingReason", "fallbackAllowed",
                        "attemptState", "retryState", "recoveryPreviousStatus"} & public.keys()
    _do(scenario)
