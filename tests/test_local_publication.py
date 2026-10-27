"""Local render and stage publication preserves cancellation and commit safety."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

import config
from pipeline import jobs as jobs_mod
from studio import compute, helpers


@pytest.fixture(autouse=True)
def _local_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    jobs_mod._registry.clear()


def _running_job(kind: str) -> str:
    job_id = jobs_mod.new_job(kind, "demo", {})
    assert jobs_mod.compare_and_set_status(job_id, expected={"queued"}, status="running")[1]
    return job_id


def test_stale_or_cancelled_render_never_replaces_public_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "render.mp4"
    output.write_bytes(b"\x00\x00\x00\x18ftypisomrender")
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(helpers, "_read_project_doc", lambda _project: ({}, 7))
    expected = ("demo", 7, "a" * 64)
    states = iter((expected, ("demo", 8, "b" * 64)))
    monkeypatch.setattr(compute, "_project_job_state", lambda _project: next(states))
    monkeypatch.setattr(compute, "_job_cancelled", lambda _job_id: False)
    with pytest.raises(jobs_mod.JobError) as stale:
        compute._install_render_output(
            _running_job("render_final"), "demo", (output,), preview=False, expected_state=expected
        )
    assert stale.value.code == "STALE_REVISION"
    assert not (config.FINAL_DIR / "demo.compiled.mp4").exists()
    monkeypatch.setattr(compute, "_project_job_state", lambda _project: expected)
    cancelled = iter((False, True))
    monkeypatch.setattr(compute, "_job_cancelled", lambda _job_id: next(cancelled))
    with pytest.raises(jobs_mod.JobError) as cancellation:
        compute._install_render_output(
            _running_job("render_final"), "demo", (output,), preview=False, expected_state=expected
        )
    assert cancellation.value.code == "CANCELLED"
    assert not (config.FINAL_DIR / "demo.compiled.mp4").exists()


@pytest.mark.parametrize("action,canonical", [
    ("transcribe", "source.words.json"),
    ("reframe", "source.reframe.json"),
])
def test_stage_publication_claim_blocks_cancel_replace_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    action: Literal["transcribe", "reframe"], canonical: str,
) -> None:
    payload = (
        b'{"language":"en","duration":1,"segments":[{"start":0,"end":1,"text":"ok","words":[{"word":"ok","start":0,"end":1,"score":1}]}]}'
        if action == "transcribe" else (
            b'{"clip_id":"s0","aspect_ratio":"9:16","face_coverage_pct":0,'
            b'"frame_crops":[{"frame_number":0,"timestamp":0,"cx":0.5,"cy":0.5,'
            b'"crop_x":0,"crop_y":0,"crop_w":1,"crop_h":1}]}'
        )
    )
    private = tmp_path / "stage"
    private.mkdir()
    output = private / canonical
    output.write_bytes(payload)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(config, "REFRAME_DIR", tmp_path / "reframe")
    job_id = _running_job(action)
    actual_replace = compute.os.replace
    cancel_results: list[bool] = []

    def replace_then_attack(src: Path, dest: Path) -> None:
        if dest.name == canonical:
            _current, cancelled = jobs_mod.compare_and_set_status(
                job_id, expected={"running"}, status="cancelled"
            )
            cancel_results.append(cancelled)
        actual_replace(src, dest)

    monkeypatch.setattr(compute.os, "replace", replace_then_attack)
    compute._materialize_stage_output(job_id, action, source, (output,))

    assert cancel_results == [False]
    job = jobs_mod.get(job_id)
    assert job is not None and job["status"] == "publishing"
    root = config.TRANSCRIPTS_DIR if action == "transcribe" else config.REFRAME_DIR
    assert (root / canonical).read_bytes() == payload


def test_multi_sidecar_publication_rolls_back_and_marks_commit_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = _running_job("transcribe")
    root = tmp_path / "transcripts"
    root.mkdir()
    targets = [root / "source.words.json", root / "source.srt", root / "source.txt"]
    for target in targets:
        target.write_text(f"old-{target.suffix}", encoding="utf-8")
    prepared: list[tuple[Path, Path]] = []
    for index, target in enumerate(targets):
        temporary = target.with_name(f".{target.name}.{job_id}.tmp")
        temporary.write_text(f"new-{index}", encoding="utf-8")
        prepared.append((temporary, target))

    actual_replace = compute.os.replace

    def fail_second(source: Path, destination: Path) -> None:
        if destination.name == "source.srt" and source.name.endswith(".tmp"):
            raise OSError("simulated commit interruption")
        actual_replace(source, destination)

    monkeypatch.setattr(compute.os, "replace", fail_second)
    with pytest.raises(compute._PublicationUnknown):
        compute._publish_files_transactionally(job_id, prepared)

    assert [target.read_text(encoding="utf-8") for target in targets] == [
        "old-.json", "old-.srt", "old-.txt"
    ]
    assert all(not temporary.exists() for temporary, _target in prepared)
    assert not list(root.glob("*.bak"))


def test_ambiguous_publication_failure_finishes_unknown_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = jobs_mod.new_job("transcribe", "demo", {})
    monkeypatch.setattr(
        compute,
        "_run_compute_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            compute._PublicationUnknown("commit outcome cannot be determined")
        ),
    )
    compute._run_detached_job(job_id, jobs_mod.get(job_id) or {})
    job = jobs_mod.get(job_id)
    assert job is not None and job["status"] == "unknown_execution"
    assert job["code"] == "EXECUTION_UNKNOWN"
