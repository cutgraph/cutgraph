"""Generalized job registry (pipeline/jobs.py).

Isolated, no GPU/worker: exercises the pure registry — new/set/get/all, the atomic file mirror, and
status transitions. These tests do not perform real encoding.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import config
from pipeline import jobs as jobs_mod


def test_job_error_preserves_code_message_and_retryability() -> None:
    with pytest.raises(jobs_mod.JobError) as error:
        raise jobs_mod.JobError("TIMEOUT", "timed out", retryable=True)
    assert error.value.code == "TIMEOUT"
    assert error.value.retryable is True
    assert str(error.value) == "timed out"
    with pytest.raises(jobs_mod.JobError) as error:
        raise jobs_mod.JobError("CANCELLED", "cancelled")
    assert error.value.code == "CANCELLED"
    assert error.value.retryable is False


@pytest.mark.parametrize("facts,code,message", (
    (jobs_mod.JobFacts("invalid"), "UNSUPPORTED_ACTION", "job action is not allowlisted"),
    (jobs_mod.JobFacts("transcribe", -1), "MALFORMED_DOCUMENT", "duration must be non-negative"),
    (jobs_mod.JobFacts("render_final", height=0), "MALFORMED_DOCUMENT", "height must be positive"),
))
def test_facts_preserve_validation_errors(
    facts: jobs_mod.JobFacts, code: str, message: str,
) -> None:
    with pytest.raises(jobs_mod.JobError) as error:
        facts.validate()
    assert error.value.code == code
    assert str(error.value) == message


@pytest.mark.parametrize("payload", [b"", b"short", b"not-an-mp4-header"])
def test_mp4_output_rejects_invalid_header(tmp_path: Path, payload: bytes) -> None:
    output = tmp_path / "output.mp4"
    output.write_bytes(payload)
    with pytest.raises(jobs_mod.JobError) as error:
        jobs_mod.validate_mp4_output(output)
    assert error.value.code == "INVALID_STAGE_RESULT"
    assert str(error.value) == "render result is not an ISO base media file"


def test_mp4_output_missing_and_valid(tmp_path: Path) -> None:
    output = tmp_path / "output.mp4"
    with pytest.raises(jobs_mod.JobError) as error:
        jobs_mod.validate_mp4_output(output)
    assert error.value.code == "MISSING_ARTIFACT"
    assert str(error.value) == "render result is unavailable"
    output.write_bytes(b"\x00\x00\x00\x18ftypisomrender")
    jobs_mod.validate_mp4_output(output)


@pytest.mark.parametrize("payload", [
    b'{"language":"en","duration":0,"duration":1,"segments":[]}',
    b'{"language":"en","duration":NaN,"segments":[]}',
    b'{"language":"en","duration":1e999,"segments":[]}',
    b'{"language":"en","duration":true,"segments":[]}',
    b'{"language":"en","duration":"1","segments":[]}',
    b'[' * 65 + b'0' + b']' * 65,
])
def test_stage_output_rejects_ambiguous_or_invalid_json(payload: bytes) -> None:
    with pytest.raises(jobs_mod.JobError) as error:
        jobs_mod.validate_stage_output("transcribe", payload)
    assert error.value.code == "INVALID_STAGE_RESULT"
    assert str(error.value) == "worker output does not satisfy the action schema"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    jobs_mod._registry.clear()
    yield
    jobs_mod._registry.clear()


def test_new_job_defaults_queued_and_mirrors_to_disk() -> None:
    jid = jobs_mod.new_job("render_final", "zzproj", {"foo": 1})
    assert jobs_mod._ID_RE.match(jid)
    job = jobs_mod.get(jid)
    assert job is not None
    assert job["kind"] == "render_final"
    assert job["project"] == "zzproj"
    assert job["status"] == "queued"
    assert job["meta"] == {"foo": 1}
    # atomic file mirror written at creation
    on_disk = json.loads((config.JOBS_DIR / f"{jid}.json").read_text(encoding="utf-8"))
    assert on_disk == job


def test_atomic_write_flushes_unique_sibling_temp_and_cleans_it(monkeypatch) -> None:
    created: list[tuple[int, str]] = []
    fsynced: list[int] = []
    real_mkstemp = jobs_mod.tempfile.mkstemp
    real_fsync = jobs_mod.os.fsync

    def capture_mkstemp(*args, **kwargs):  # noqa: ANN002, ANN003
        fd, raw_path = real_mkstemp(*args, **kwargs)
        created.append((fd, raw_path))
        return fd, raw_path

    def capture_fsync(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(jobs_mod.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(jobs_mod.os, "fsync", capture_fsync)
    jid = jobs_mod.new_job("transcribe", "zzproj", {"attempt": 1})

    assert len(created) == 1
    fd, raw_path = created[0]
    temporary = Path(raw_path)
    assert temporary.parent == config.JOBS_DIR
    assert temporary.name.startswith(f".{jid}.json.")
    assert temporary.suffix == ".tmp"
    assert not temporary.exists()
    assert fsynced == [fd]


def test_new_job_needs_approval_status() -> None:
    jid = jobs_mod.new_job("render_final", "zzproj", {}, status="needs-approval")
    job = jobs_mod.get(jid)
    assert job is not None and job["status"] == "needs-approval"


def test_set_merges_fields_atomically() -> None:
    jid = jobs_mod.new_job("render_final", "zzproj", {})
    updated = jobs_mod.set(jid, status="running", progress=0.5)
    assert updated is not None and updated["status"] == "running" and updated["progress"] == 0.5
    done = jobs_mod.set(jid, status="done", output="/media/compiled/zzproj.mp4")
    assert done is not None and done["status"] == "done" and done["output"] == "/media/compiled/zzproj.mp4"
    # file mirror reflects the latest merge
    on_disk = json.loads((config.JOBS_DIR / f"{jid}.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == "done" and on_disk["progress"] == 0.5


def test_set_unknown_id_returns_none() -> None:
    assert jobs_mod.set("deadbeef", status="running") is None


def test_compare_and_set_uses_persisted_status_as_authority() -> None:
    jid = jobs_mod.new_job("transcribe", "zzproj", {})
    persisted = jobs_mod.get(jid)
    assert persisted is not None
    persisted["status"] = "cancelled"
    jobs_mod._atomic_write(jid, persisted)

    current, changed = jobs_mod.compare_and_set_status(
        jid, expected={"queued"}, status="running",
    )

    assert changed is False
    assert current is not None and current["status"] == "cancelled"
    assert jobs_mod.get(jid)["status"] == "cancelled"  # type: ignore[index]


def test_terminal_status_cannot_be_overwritten() -> None:
    jid = jobs_mod.new_job("transcribe", "zzproj", {})
    cancelled, changed = jobs_mod.compare_and_set_status(
        jid, expected={"queued"}, status="cancelled",
    )
    assert changed and cancelled is not None
    current, changed = jobs_mod.compare_and_set_status(
        jid, expected={"cancelled"}, status="done", output="must-not-adopt",
    )
    assert changed is False
    assert current is not None and current["status"] == "cancelled"
    assert "output" not in current
    assert jobs_mod.set(jid, status="running")["status"] == "cancelled"  # type: ignore[index]


def test_get_falls_back_to_file_mirror_when_not_in_memory() -> None:
    jid = jobs_mod.new_job("render_final", "zzproj", {})
    jobs_mod._registry.clear()  # simulate a fresh process (memory lost, disk survives)
    recovered = jobs_mod.get(jid)
    assert recovered is not None and recovered["id"] == jid


def test_disk_cancelled_render_dedupe_creates_a_new_retry_id() -> None:
    """A stale in-memory active entry cannot reuse a cancelled disk job."""
    first, _job, created = jobs_mod.new_or_existing_render_final(
        "zzproj", {}, dedupe_key="immutable-render"
    )
    assert created
    persisted = jobs_mod.get(first)
    assert persisted is not None
    persisted["status"] = "cancelled"
    jobs_mod._atomic_write(first, persisted)
    jobs_mod._registry[first] = {**persisted, "status": "running"}

    retry, job, created = jobs_mod.new_or_existing_render_final(
        "zzproj", {}, dedupe_key="immutable-render"
    )

    assert created and retry != first and job["status"] == "needs-approval"


def test_disk_publishing_render_dedupe_reuses_existing_job() -> None:
    """Publication owns the render identity until its terminal result is durable."""
    first, _job, created = jobs_mod.new_or_existing_render_final(
        "zzproj", {}, dedupe_key="immutable-render"
    )
    assert created
    persisted = jobs_mod.get(first)
    assert persisted is not None
    persisted["status"] = "publishing"
    jobs_mod._atomic_write(first, persisted)
    # Simulate a process with an old cached state: the disk mirror must win.
    jobs_mod._registry[first] = {**persisted, "status": "running"}

    reused, job, created = jobs_mod.new_or_existing_render_final(
        "zzproj", {}, dedupe_key="immutable-render"
    )

    assert not created and reused == first and job["status"] == "publishing"


def test_all_snapshots_the_registry() -> None:
    a = jobs_mod.new_job("render_final", "p1", {})
    b = jobs_mod.new_job("transcribe", "p2", {})
    snap = jobs_mod.all()
    assert set(snap) == {a, b}
    snap["injected"] = {}  # a snapshot copy — mutating it must not corrupt the registry
    assert "injected" not in jobs_mod.all()


def test_unsafe_job_id_rejected() -> None:
    for bad in ("../escape", "a/b", "a:b", "x" * 201):
        with pytest.raises(ValueError):
            jobs_mod._path(bad)


def test_get_tolerates_corrupt_file_mirror() -> None:
    """a job killed mid-write leaves a half/garbage <id>.json — reading it returns None, never a
    JSONDecodeError that would crash the poller."""
    jid = jobs_mod.new_job("render_final", "zzproj", {})
    jobs_mod._registry.clear()  # force the on-disk mirror path
    (config.JOBS_DIR / f"{jid}.json").write_text("{ not valid json", encoding="utf-8")
    assert jobs_mod.get(jid) is None
    assert jobs_mod.set(jid, status="done") is None  # set() also degrades to None on a corrupt mirror


def test_recover_interrupted_fails_closed_without_replaying_or_adopting_outputs() -> None:
    queued = jobs_mod.new_job("transcribe", "queued-project", {})
    running = jobs_mod.new_job("reframe", "running-project", {})
    publishing = jobs_mod.new_job("render_final", "publishing-project", {})
    done = jobs_mod.new_job("render_final", "done-project", {})
    jobs_mod.set(running, status="running", output="must-not-be-adopted")
    jobs_mod.set(publishing, status="running")
    jobs_mod.set(publishing, status="publishing", output="also-not-proof")
    jobs_mod.set(done, status="running")
    jobs_mod.set(done, status="done", output="durable-success")
    jobs_mod._registry.clear()

    recovered = jobs_mod.recover_interrupted()

    assert set(recovered) == {queued, running, publishing}
    assert jobs_mod.get(queued)["status"] == "interrupted"  # type: ignore[index]
    assert jobs_mod.get(running)["status"] == "unknown_execution"  # type: ignore[index]
    assert jobs_mod.get(publishing)["status"] == "unknown_execution"  # type: ignore[index]
    assert jobs_mod.get(done)["status"] == "done"  # type: ignore[index]
    assert jobs_mod.get(done)["output"] == "durable-success"  # type: ignore[index]
    assert jobs_mod.get(running)["output"] == "must-not-be-adopted"  # type: ignore[index]
    assert jobs_mod.get(running)["recoveryReason"] == (  # type: ignore[index]
        "application_restarted_during_execution"
    )


def test_recover_interrupted_ignores_corrupt_and_identity_mismatched_records() -> None:
    config.JOBS_DIR.mkdir(parents=True)
    (config.JOBS_DIR / "corrupt.json").write_text("{", encoding="utf-8")
    (config.JOBS_DIR / "mismatch.json").write_text(
        json.dumps({"id": "different", "status": "running"}), encoding="utf-8",
    )

    assert jobs_mod.recover_interrupted() == {}
    assert (config.JOBS_DIR / "corrupt.json").read_text(encoding="utf-8") == "{"
    mismatch = json.loads((config.JOBS_DIR / "mismatch.json").read_text(encoding="utf-8"))
    assert mismatch == {"id": "different", "status": "running"}


def test_job_preflight_is_bounded_and_does_not_write() -> None:
    ready = jobs_mod.preflight_job(
        "render_final", "demo-project", metadata={"clipCount": 2}, dedupe_key="render-1",
    )
    assert ready.status == "ready"
    assert not config.JOBS_DIR.exists()

    assert jobs_mod.preflight_job("render_final", "../escape").status == "rejected"
    assert jobs_mod.preflight_job("unknown", "demo-project").status == "rejected"
    assert jobs_mod.preflight_job(
        "render_final", "demo-project", metadata={"sourcePath": "private/source.mp4"},
    ).status == "rejected"
    assert jobs_mod.preflight_job(
        "render_final", "demo-project", metadata={"items": [object()]},
    ).status == "rejected"


def test_final_job_creation_delegates_dedupe_and_redacts_metadata() -> None:
    first = jobs_mod.create_render_final_job(
        "demo-project", dedupe_key="immutable-render", metadata={"clipCount": 2},
    )
    second = jobs_mod.create_render_final_job(
        "demo-project", dedupe_key="immutable-render", metadata={"clipCount": 999},
    )

    assert first.created is True
    assert second.created is False
    assert second.job_id == first.job_id
    assert first.job["status"] == "needs-approval"
    assert "meta" not in first.job
    persisted = json.loads((config.JOBS_DIR / f"{first.job_id}.json").read_text(encoding="utf-8"))
    assert persisted["meta"] == {"clipCount": 2}


def test_approval_and_cancellation_use_native_cas(monkeypatch) -> None:
    operation = jobs_mod.create_render_final_job("demo-project", dedupe_key="render-1")

    approved, changed = jobs_mod.approve_job(operation.job_id)
    assert changed and approved is not None and approved["status"] == "queued"
    approved_again, changed_again = jobs_mod.approve_job(operation.job_id)
    assert not changed_again and approved_again is not None and approved_again["status"] == "queued"

    cas_calls: list[dict[str, object]] = []
    native_cas = jobs_mod.compare_and_set_status

    def capture_cas(*args, **kwargs):
        cas_calls.append(dict(kwargs))
        return native_cas(*args, **kwargs)

    monkeypatch.setattr(jobs_mod, "compare_and_set_status", capture_cas)
    cancelled, cancelled_changed = jobs_mod.cancel_job(operation.job_id)
    assert cancelled_changed and cancelled is not None and cancelled["status"] == "cancelled"
    assert "meta" not in cancelled
    assert cas_calls[-1]["status"] == "cancelled"
    assert isinstance(cas_calls[-1]["cancelRequestedAt"], float)
    job = jobs_mod.get(operation.job_id)
    assert job is not None and "cancelRequestedAt" in job


def test_recovery_delegates_fail_closed_native_reconciliation() -> None:
    operation = jobs_mod.create_render_final_job("demo-project", dedupe_key="render-1")
    jobs_mod.compare_and_set_status(operation.job_id, expected={"needs-approval"}, status="queued")
    jobs_mod.compare_and_set_status(operation.job_id, expected={"queued"}, status="running")
    jobs_mod.set(operation.job_id, status="publishing", output="/private/should-not-be-adopted")
    jobs_mod._registry.clear()

    recovered = jobs_mod.recover_jobs()

    assert recovered[operation.job_id]["status"] == "unknown_execution"
    assert "output" not in recovered[operation.job_id]
    assert recovered[operation.job_id]["recoveryReason"] == "application_restarted_during_execution"
