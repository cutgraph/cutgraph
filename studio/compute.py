from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import config
from pipeline import compile as compile_stage
from pipeline import addons, contracts, scene_render
from pipeline import names
from pipeline import jobs as jobs_mod
from pipeline import project as project_mod
from pipeline import proxy as proxy_stage
from pipeline import reframe as reframe_stage
from pipeline import transcribe as transcribe_stage
from studio import helpers, state
from studio.errors import failure_message

def _job_source_slug(job: dict) -> str | None:
    """Resolve a compute job's bounded source identity.

    The durable ``project`` field is the Studio/SSE identity. A renamed
    project may still point at an uploaded source with a different slug, so
    mechanical jobs retain only that safe slug (never an absolute path) in
    metadata. Jobs written before this distinction existed continue to use
    their project slug as the source slug.
    """
    raw_meta = job.get("meta")
    if isinstance(raw_meta, dict) and "sourceSlug" in raw_meta:
        candidate = raw_meta.get("sourceSlug")
        if (
            isinstance(candidate, str)
            and names.source_name(candidate) == candidate
        ):
            return candidate
        return None
    project = job.get("project")
    if (
        isinstance(project, str)
        and names.source_name(project) == project
    ):
        return project
    return None


def _compute_job_source(job: dict) -> Path | None:
    slug = _job_source_slug(job)
    return helpers._source(slug) if slug is not None else None


def _project_compute_source_slug(project: str) -> str | None:
    """Return the one project-owned source that mechanical jobs may use.

    Manual compute requests name a Studio project, not an upload slug.  Read
    the persisted speech clip and accept it only when it resolves to one
    direct video child of the configured source directory.  This deliberately
    never accepts a request-provided path, nested path, or arbitrary project
    media as an input to WhisperX/MediaPipe.
    """
    try:
        doc, _revision = helpers._read_project_doc(project)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None
    source_ref = helpers._speech_source(doc or {})
    if not isinstance(source_ref, str) or not source_ref:
        return None
    # Validate the persisted logical reference *before* any OS path
    # normalization.  A resolve-first check would make ``data/source/../``
    # indistinguishable from the canonical source path, which is too broad
    # for a worker input boundary.
    if "\\" in source_ref:
        return None
    parts = source_ref.split("/")
    if len(parts) != 3 or parts[:2] != ["data", "source"]:
        return None
    filename = parts[2]
    ext = Path(filename).suffix
    slug = filename[: -len(ext)] if ext else ""
    if (
        ext not in helpers.VIDEO_EXTS
        or not slug
        or names.source_name(slug) != slug
        or filename != f"{slug}{ext}"
    ):
        return None
    try:
        source_root = config.DATA_DIR.resolve()
        candidate = names.exact_path(source_root / filename, source_root).resolve()
    except OSError:
        return None
    if (
        candidate.parent != source_root
        or not candidate.exists()
    ):
        return None
    try:
        # `_source` is the legacy stage resolver. Refuse an ambiguous stem
        # rather than letting its extension-order decide which project input
        # runs; the project doc must name the one canonical source file.
        same_stem = [
            path for path in source_root.iterdir()
            if path.is_file()
            and path.suffix.lower() in helpers.VIDEO_EXTS
            and path.stem == slug
        ]
    except OSError:
        return None
    if len(same_stem) != 1:
        return None
    try:
        return slug if helpers._source(slug).resolve() == candidate else None
    except OSError:
        return None


def _log_ffmpeg(cmd: list[str]) -> None:
    """Repo rule: every ffmpeg invocation is logged with its full command string (mirrors
    pipeline.proxy._log_ffmpeg) so run.log is the complete record of what touched the media."""
    with open(config.RUN_LOG, "a", encoding="utf-8") as fh:
        fh.write(" ".join(cmd) + "\n")


@contextmanager
def _owned_process_scope() -> Iterator[None]:
    """Register subprocesses launched by the current Studio operation.

    The scope is deliberately narrow and serialized.  Pipeline stages share
    the stdlib ``subprocess`` module, so wrapping Popen for the duration of an
    operation captures ffmpeg, ffprobe, proxy, and transcription children
    without changing those stage APIs or their test seams.
    """
    if getattr(state._PROCESS_SCOPE_LOCAL, "active", False):
        yield
        return
    with state._PROCESS_SCOPE_LOCK:
        original_popen = subprocess.Popen
        owner = threading.get_ident()

        # A subclass, not a wrapper function: a module imported by another thread while a stage runs
        # must still find a class (the co-pilot's lazily imported MCP SDK evaluates
        # `subprocess.Popen[bytes]` at import time, and a function there broke its first turn).
        class TrackedPopen(original_popen):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN204 - Popen compatibility shim
                # Popen is process-global. Provider/auth or launcher work in a
                # different thread may legitimately start while a media stage is
                # active; pass those calls through without adopting them into this
                # stage's ownership tree.
                owned = threading.get_ident() == owner
                # Put owned media commands in their own process group so a forced
                # shutdown can remove descendants as a unit on POSIX. Windows
                # uses taskkill /T in _terminate_owned_process_tree below.
                if owned and os.name != "nt":
                    kwargs.setdefault("start_new_session", True)
                elif owned and sys.platform == "win32":
                    kwargs["creationflags"] = (
                        int(kwargs.get("creationflags", 0))
                        | int(subprocess.CREATE_NEW_PROCESS_GROUP)
                    )
                super().__init__(*args, **kwargs)
                if owned:
                    with state._OWNED_PROCESSES_LOCK:
                        state._OWNED_PROCESSES[id(self)] = (self, owner)

        state._PROCESS_SCOPE_LOCAL.active = True
        subprocess.Popen = TrackedPopen
        try:
            yield
        finally:
            subprocess.Popen = original_popen
            state._PROCESS_SCOPE_LOCAL.active = False
            with state._OWNED_PROCESSES_LOCK:
                for key, (process, _process_owner) in tuple(state._OWNED_PROCESSES.items()):
                    try:
                        finished = process.poll() is not None
                    except (AttributeError, OSError):
                        finished = True
                    if finished:
                        state._OWNED_PROCESSES.pop(key, None)


def _terminate_owned_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate one owned process and all descendants, escalating promptly."""
    try:
        pid = int(process.pid)
    except (AttributeError, TypeError, ValueError):
        return
    try:
        if process.poll() is not None:
            return
    except (AttributeError, OSError):
        return
    if os.name == "nt":
        # ``taskkill /T`` is the Windows equivalent of a process group kill.
        # Use the original Popen implementation indirectly through os.system
        # only as a last resort; the normal root terminate/kill path below is
        # still used when taskkill is unavailable (notably in unit tests).
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except (AttributeError, OSError):
                return
    try:
        process.wait(timeout=0.5)
    except (subprocess.TimeoutExpired, OSError, AttributeError):
        try:
            process.kill()
        except (AttributeError, OSError):
            return
        try:
            process.wait(timeout=0.5)
        except (subprocess.TimeoutExpired, OSError, AttributeError):
            pass


def _terminate_owned_processes(owners: Collection[int] | None = None) -> None:
    """Stop owned children for the selected worker threads."""
    with state._OWNED_PROCESSES_LOCK:
        entries = tuple(state._OWNED_PROCESSES.items())
    for key, (process, owner) in entries:
        if owners is not None and owner not in owners:
            continue
        _terminate_owned_process_tree(process)
        with state._OWNED_PROCESSES_LOCK:
            state._OWNED_PROCESSES.pop(key, None)


def _gpu_run(fn, *args, **kwargs):  # noqa: ANN001, ANN201 — generic guarded call
    """Run a GPU/ffmpeg-shelling callable while holding the single GPU slot. Called from the worker
    thread directly and from route handlers via `asyncio.to_thread` (so the lock is taken IN the thread,
    never on the event loop)."""
    with state._GPU_LOCK:
        with _owned_process_scope():
            return fn(*args, **kwargs)


def _capture_job_event_loop() -> None:
    """Bind worker-thread job events to the loop dispatching the work.

    The studio runs one uvicorn worker, but tests and orderly restarts can
    replace that worker's event loop. Capture at every dispatch boundary
    instead of relying on an unrelated route having run earlier.
    """

    try:
        state._MAIN_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        # Sync unit tests may exercise the queue helpers without an event
        # loop. They have no live SSE subscriber to notify.
        pass


def _emit_job_threadsafe(project: str, msg: dict) -> None:
    """Publish a job-transition event from the worker THREAD onto the event loop's SSE queues.
    asyncio.Queue is not thread-safe, so the put is marshalled back onto the loop; a no-op until an
    dispatch boundary has captured the loop (safe under a sync unit test)."""
    loop = state._MAIN_LOOP
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(state.hub.publish_event, project, msg)
    except RuntimeError:
        pass  # the loop has already closed (e.g. between test runs) — the event is simply dropped


def _job_cancelled(job_id: str) -> bool:
    job = jobs_mod.get(job_id)
    return bool(job and job.get("status") == "cancelled")


_SAFE_JOB_ERROR_MESSAGES = {
    **addons.ERROR_MESSAGES,
    **transcribe_stage.ERROR_MESSAGES,
    "TRANSCRIPTION_NOT_INSTALLED": "Transcription is not installed. Open Settings, Add-ons to install it, then restart Studio.",
    "DEPENDENCY_UNAVAILABLE": "Required compute support is unavailable.",
    "TIMEOUT": "The job timed out.",
    "CANCELLED": "The job was cancelled.",
    "STALE_REVISION": "The project changed before the result could be applied.",
    "DUPLICATE_IN_FLIGHT": "This job is already running.",
}


def _public_job_error(exc: jobs_mod.JobError) -> dict[str, str]:
    return {
        "error": _SAFE_JOB_ERROR_MESSAGES.get(
            exc.code, "The compute request could not be completed."
        ),
        "code": exc.code,
    }


def _iso_timestamp(value: object) -> str | None:
    """Normalize durable epoch or RFC3339 lifecycle values for the UI contract."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return None


def _job_presentation(job: dict, *, sequence: int | None = None) -> dict:
    """Return the path/credential-free job shape exposed to Studio clients."""
    raw_status = str(job.get("status") or "")
    code = str(job.get("code") or "")
    attempt_state = str(job.get("attemptState") or "")
    if raw_status in {"error", "failed"} and code == "STALE_REVISION":
        raw_status = "stale"
    # Retry is durable attempt context, not a fabricated completion state. It is shown
    # while the child is queued or approval-gated, then cleared when the worker claims it.
    if attempt_state == "retrying" and raw_status in {"needs-approval", "queued"}:
        raw_status = attempt_state
    status = (
        raw_status
        if raw_status in {
            "needs-approval", "queued", "running", "publishing",
            "done", "succeeded", "error", "failed", "cancelled",
            "retrying", "stale", "interrupted",
            "unknown_execution",
        }
        else "queued"
    )
    raw_kind = str(job.get("kind") or "")
    kind = (
        raw_kind
        if raw_kind in {"transcribe", "reframe", "render_final", "render_preview_segment", "render_scene", "addon_install"}
        else "compute_job"
    )
    progress_by_status = {
        "needs-approval": 0.0, "queued": 0.05, "running": 0.35,
        "publishing": 0.9, "done": 1.0, "succeeded": 1.0,
        "error": 1.0, "failed": 1.0, "cancelled": 1.0,
        "retrying": 0.05, "stale": 1.0,
        "interrupted": 1.0, "unknown_execution": 1.0,
    }
    stage_by_status = {
        "needs-approval": "Awaiting approval", "queued": "Queued",
        "running": "Computing", "publishing": "Returning result",
        "done": "Complete", "succeeded": "Complete", "error": "Failed",
        "failed": "Failed", "cancelled": "Cancelled", "retrying": "Retrying",
        "stale": "Stale",
        "interrupted": "Interrupted", "unknown_execution": "Execution unknown",
    }
    # A running job may narrate a sub-stage with its own progress ("Rendering animation N/M").
    note = job.get("stageNote") if status == "running" else None
    note_progress = job.get("stageProgress")
    result: dict[str, object] = {
        "id": str(job.get("id") or ""),
        "kind": kind,
        "status": status,
        "progress": (
            note_progress if isinstance(note, str) and isinstance(note_progress, float)
            else progress_by_status.get(status, 0.0)
        ),
        "progressStage": note if isinstance(note, str) else stage_by_status.get(status, "Pending"),
        "canCancel": job.get("status") in {"needs-approval", "queued", "running"},
        "canRetry": job.get("status") in {
            "error", "failed", "cancelled", "interrupted", "unknown_execution",
        } or status == "stale",
    }
    # Snapshot/list responses carry the durable creation timestamp used by
    # the Jobs activity surface. Keep the historical SSE event shape stable;
    # live events are enriched by the next reconciliation snapshot.
    created_at = _iso_timestamp(job.get("createdAt"))
    if sequence is None and created_at is not None:
        result["createdAt"] = created_at
    ordinal = job.get("createdOrdinal")
    if sequence is None and isinstance(ordinal, int) and not isinstance(ordinal, bool):
        result["createdOrdinal"] = ordinal
    started_at = _iso_timestamp(job.get("startedAt") or job.get("started"))
    completed_at = _iso_timestamp(job.get("completedAt") or job.get("ended"))
    if started_at is not None:
        result["startedAt"] = started_at
    if completed_at is not None:
        result["completedAt"] = completed_at
    if attempt_state == "retrying":
        result["attemptState"] = attempt_state
    elif sequence is not None and "attemptState" in job:
        # Explicitly clear a retry marker when the worker claims the child.
        result["attemptState"] = None
    if job.get("status") == "needs-approval":
        result["requiresApproval"] = True
    persisted_sequence = job.get("eventSequence")
    if sequence is not None:
        result["sequence"] = sequence
    elif isinstance(persisted_sequence, int) and not isinstance(persisted_sequence, bool):
        result["sequence"] = persisted_sequence
    retry_state = str(job.get("retryState") or "")
    if retry_state == "retrying":
        result["retryState"] = retry_state
    retry_child = job.get("retryChildJobId")
    if isinstance(retry_child, str):
        result["retryChildJobId"] = retry_child
    if status in {"error", "failed", "stale"}:
        result["error"] = failure_message(
            job.get("error"), _SAFE_JOB_ERROR_MESSAGES.get(code, "The job failed during execution.")
        )
        if code:
            result["errorCode"] = code
    elif status == "interrupted":
        result["error"] = "The application closed before this job began. Retry it when ready."
        result["errorCode"] = "APPLICATION_RESTARTED"
    elif status == "unknown_execution":
        result["error"] = (
            "The application closed while this job may have been running. Verify any external "
            "result before starting another attempt."
        )
        result["errorCode"] = "EXECUTION_UNKNOWN"
    output = job.get("output")
    if isinstance(output, str) and output.startswith("/media/"):
        result["output"] = output
    scenes = job.get("scenes")
    if isinstance(scenes, list):
        result["scenes"] = [
            {"id": s["id"], "state": s["state"]} for s in scenes
            if isinstance(s, dict) and isinstance(s.get("id"), str)
            and s.get("state") in {"cached", "waiting", "rendering", "rendered"}
        ]
    parent = job.get("parentJobId")
    if isinstance(parent, str):
        result["parentJobId"] = parent
    return result


def _job_event(job_id: str) -> dict:
    sequence = jobs_mod.next_event_sequence(job_id)
    job = jobs_mod.get(job_id)
    if job is None:
        raise ValueError("no such job")
    return {"type": "job", **_job_presentation(job, sequence=sequence)}


def _transcript_lifecycle(project: str) -> dict:
    """The durable, project-scoped state used to reconcile the editor UI.

    The transcript sidecar is the ready authority; jobs explain all non-ready
    states and retain an actionable failure/cancellation record across reloads.
    """
    source_slug = _project_compute_source_slug(project)
    words_path = (
        names.exact_path(config.TRANSCRIPTS_DIR / f"{source_slug}.words.json", config.TRANSCRIPTS_DIR)
        if source_slug else None
    )
    jobs = [
        job for job in jobs_mod.all_persisted().values()
        if job.get("project") == project and job.get("kind") == "transcribe"
    ]
    latest = max(jobs, key=lambda job: str(job.get("createdAt") or ""), default=None)
    presented = _job_presentation(latest) if latest is not None else None

    if words_path and words_path.exists():
        if source_slug and not helpers._source(source_slug).exists():
            return {"state": "stale", "job": presented,
                    "message": "The source footage is offline; reconnect it before retranscribing."}
        return {"state": "ready", "job": presented}
    if latest is not None:
        status = str(latest.get("status") or "")
        if status in {"queued", "running", "publishing"}:
            return {"state": "running" if status in {"running", "publishing"} else "queued", "job": presented}
        if status == "error":
            return {"state": "failed", "job": presented,
                    "message": str(presented.get("error") if presented else "Transcription failed.")}
        if status == "cancelled":
            return {"state": "not_started", "job": presented,
                    "message": "Transcription was cancelled."}
        if status in {"interrupted", "unknown_execution"}:
            return {"state": "not_started", "job": presented,
                    "message": str(presented.get("error") if presented else "Transcription was interrupted.")}
    if not addons.available("transcription"):
        return {"state": "failed", "job": presented, "message": addons.unavailable_message("transcription"),
                "errorCode": "TRANSCRIPTION_NOT_INSTALLED"}
    return {"state": "not_started", "job": presented}


def _emit_job_update_threadsafe(project: str, job_id: str) -> None:
    event = _job_event(job_id)
    job = jobs_mod.get(job_id)
    _emit_job_threadsafe(state.ADDON_EVENTS_CHANNEL if job and job.get("kind") == "addon_install" else project, event)


def _publish_job_update(project: str, job_id: str) -> None:
    state.hub.publish_event(project, _job_event(job_id))


def _dispatch_queued_job(project: str, job_id: str) -> None:
    """Publish queued state, then expose the job to the single-slot worker."""
    _capture_job_event_loop()
    _publish_job_update(project, job_id)
    # Lifespan shutdown closes the dispatch gate before signalling the worker.
    # A request racing with shutdown may still have published its durable queued
    # state, but it must not add new in-memory work after the queue is stopped.
    if _ensure_worker() is False:
        return
    state._JOB_Q.put(job_id)


def _begin_publication(job_id: str) -> None:
    """Claim the non-cancellable artifact-publication phase durably."""
    current, claimed = jobs_mod.compare_and_set_status(
        job_id,
        expected={"running"},
        status="publishing",
        publishingStartedAt=time.time(),
    )
    if claimed:
        job = jobs_mod.get(job_id)
        if job is not None:
            _emit_job_update_threadsafe(str(job.get("project") or ""), job_id)
        return
    status = str((current or {}).get("status") or "missing")
    if status == "cancelled":
        raise jobs_mod.JobError(
            "CANCELLED", "job was cancelled before artifact publication"
        )
    raise jobs_mod.JobError(
        "PUBLICATION_REJECTED", f"job cannot publish from state {status}"
    )


class _PublicationUnknown(RuntimeError):
    """A publication crossed its durable claim but its filesystem outcome is ambiguous."""


def _publish_files_transactionally(
    job_id: str, prepared: list[tuple[Path, Path]]
) -> None:
    """Atomically publish one or more staged files, restoring prior files on failure.

    The durable ``publishing`` claim is made before any public path changes.
    Once that claim succeeds, a failed ``os.replace`` is inherently ambiguous
    (the rename may have happened before the exception reached Python), so the
    caller must surface ``unknown_execution`` even when rollback succeeds. The
    backup/restore pass makes the public namespace consistent for the next
    recovery attempt and prevents a partial sidecar set from being observed.
    """
    if not prepared:
        raise ValueError("publication requires at least one staged file")
    targets = [target for _temporary, target in prepared]
    if len(set(targets)) != len(targets):
        raise ValueError("publication targets must be unique")
    backups: list[tuple[Path, Path]] = []
    claimed = False
    commit_succeeded = False
    rollback_failed = False
    try:
        try:
            _begin_publication(job_id)
        except jobs_mod.JobError:
            # A rejected claim (cancelled/stale lifecycle state) is a known
            # ordinary routing outcome, not an ambiguous commit.
            raise
        except Exception as exc:  # noqa: BLE001 - durable CAS/write may be uncertain
            raise _PublicationUnknown(
                f"publication claim for job {job_id} is unknown: {exc}"
            ) from exc
        claimed = True

        # Move every old target out of the public namespace before installing
        # any replacement. A deterministic job-specific backup makes the
        # rollback recoverable after a process interruption as well.
        for _temporary, target in prepared:
            if target.exists():
                backup = target.with_name(f".{target.name}.{job_id}.bak")
                backup.unlink(missing_ok=True)
                os.replace(target, backup)
                backups.append((backup, target))
        for temporary, target in prepared:
            os.replace(temporary, target)
        commit_succeeded = True
    except Exception as exc:  # noqa: BLE001 - commit/rollback must be classified
        if not claimed:
            raise
        rollback_error: Exception | None = None
        try:
            # Remove both successfully installed files and a file whose
            # replace raised after actually moving it.
            for _temporary, target in reversed(prepared):
                target.unlink(missing_ok=True)
            for backup, target in reversed(backups):
                if backup.exists():
                    os.replace(backup, target)
        except Exception as restore_exc:  # noqa: BLE001 - retain original context
            rollback_error = restore_exc
            rollback_failed = True
        detail = f"publication commit for job {job_id} is unknown: {exc}"
        if rollback_error is not None:
            detail += f"; rollback failed: {rollback_error}"
        raise _PublicationUnknown(detail) from exc
    finally:
        for temporary, _target in prepared:
            temporary.unlink(missing_ok=True)
        # On success, backups are no longer needed. If rollback itself failed,
        # leave any surviving backup in place for manual/recovery inspection.
        if commit_succeeded and not rollback_failed:
            for backup, _target in backups:
                backup.unlink(missing_ok=True)


def _project_job_state(project: str) -> tuple[str, int, str]:
    """The immutable project anchor used both before dispatch and at adoption."""
    doc, revision = helpers._read_project_doc(project)
    return project, revision, helpers._timeline_hash(doc or {})


def _materialize_stage_output(job_id: str, action: Literal["transcribe", "reframe"], source: Path,
                               adopted: tuple[Path, ...]) -> contracts.StageResult:
    """Restore the existing local sidecar contract after validated adoption."""
    if not adopted:
        raise jobs_mod.JobError("INVALID_STAGE_RESULT", "worker returned no stage output")
    payload = adopted[0].read_bytes()
    jobs_mod.validate_stage_output(action, payload)
    root = config.TRANSCRIPTS_DIR if action == "transcribe" else config.REFRAME_DIR
    allowed_names = (
        {
            f"{source.stem}.words.json",
            f"{source.stem}.srt",
            f"{source.stem}.txt",
        }
        if action == "transcribe"
        else {f"{source.stem}.reframe.json"}
    )
    outputs = [item for item in adopted if item.name in allowed_names]
    # Artifact adoption is content-addressed, so output files are
    # named by SHA-256 rather than their stage filename.
    # A fixed handler returns exactly one validated stage artifact; restore
    # that artifact to the canonical Studio sidecar name on publication.
    if len(adopted) == 1 and not outputs:
        canonical = (
            f"{source.stem}.words.json"
            if action == "transcribe"
            else f"{source.stem}.reframe.json"
        )
        outputs = [adopted[0]]
        targets = {adopted[0]: canonical}
    elif outputs and outputs[0] == adopted[0]:
        targets = {item: item.name for item in outputs}
    else:
        raise jobs_mod.JobError(
            "INVALID_STAGE_RESULT", "worker returned an unexpected stage output"
        )
    root.mkdir(parents=True, exist_ok=True)
    prepared: list[tuple[Path, Path]] = []
    try:
        for item in outputs:
            target = root / targets[item]
            temporary = target.with_name(f".{target.name}.{job_id}.tmp")
            temporary.write_bytes(item.read_bytes())
            prepared.append((temporary, target))
    except Exception:
        for temporary, _target in prepared:
            temporary.unlink(missing_ok=True)
        raise
    _publish_files_transactionally(job_id, prepared)
    target = root / targets[adopted[0]]
    return contracts.StageResult("ok", action, source.stem, output_path=str(target))


def _local_render(
    job_id: str,
    project: str,
    doc: dict,
    meta: dict,
    *,
    preview: bool,
    expected_state: tuple[str, int, str],
) -> dict:
    """Render only into a job-private directory, then use the common publisher.

    Compilers take an explicit project root and an explicit output root here.
    In particular, no local fallback is allowed to give ffmpeg its public
    destination: the sole public ``os.replace`` is in ``_install_render_output``
    after its durable publication claim.
    """
    private = config.STATE_HOME / "job-results" / job_id
    private.mkdir(parents=True, exist_ok=True)
    destination = private / ("preview.mp4" if preview else "final.mp4")
    try:
        if preview:
            result = compile_stage.compile_preview_segment(
                doc,
                int(meta["start_f"]),
                int(meta["end_f"]),
                destination,
                project_dir=config.STATE_HOME,
                output_root=private,
            )
        else:
            scenes, _rendered = _render_scenes(job_id, project, doc)
            # A footage-only project compiles exactly as it did before animated scenes existed.
            result = compile_stage.compile_project(
                doc,
                destination,
                project_dir=config.STATE_HOME,
                output_root=private,
                **({"scenes": scenes} if scenes else {}),
            )
    except Exception as exc:  # noqa: BLE001 -- a compiler error remains a job failure
        return {"error": str(exc)} if preview else {"errors": [str(exc)]}
    if result.status != "ok" or not result.output_path:
        reason = result.reason or "render failed"
        return {"error": reason} if preview else {"errors": [reason]}
    # Do not trust a StageResult's string alone: validate the concrete private
    # artifact before it becomes eligible for the common public installer.
    produced = Path(result.output_path)
    jobs_mod.validate_mp4_output(produced)
    return _install_render_output(
        job_id, project, (produced,), preview=preview, expected_state=expected_state
    )


def _render_scenes(
    job_id: str | None, project: str, doc: dict, scene_id: str | None = None
) -> tuple[list[compile_stage.SceneLayer], int]:
    """Render uncached animated scenes for a local export while holding the GPU slot.

    Report animation progress and each scene's cached, waiting, rendering or rendered state.
    """
    plan: list[tuple[str, int]] = []

    def states(n: int) -> list[dict]:
        return [{"id": sid, "state": "cached" if not k else "rendered" if k < n else "rendering" if k == n else "waiting"}
                for sid, k in plan]

    def planned(scenes: list[tuple[str, int]]) -> None:
        plan[:] = scenes
        if job_id is not None:
            jobs_mod.set(job_id, scenes=states(0))
            _emit_job_update_threadsafe(project, job_id)

    def progress(n: int, m: int) -> None:
        if job_id is None:
            return
        if _job_cancelled(job_id):
            raise jobs_mod.JobError("CANCELLED", "job was cancelled while rendering scenes")
        jobs_mod.set(job_id, stageNote=f"Rendering animation {n}/{m}",
                     stageProgress=round(0.05 + 0.3 * (n - 1) / m, 3), scenes=states(n))
        _emit_job_update_threadsafe(project, job_id)

    layers, rendered = scene_render.ensure_scenes(
        doc, root=config.STATE_HOME, scene_id=scene_id, on_progress=progress, on_plan=planned
    )
    if rendered and job_id is not None:
        jobs_mod.set(job_id, stageNote=None, stageProgress=None, scenes=states(rendered + 1))
        _emit_job_update_threadsafe(project, job_id)
    return layers, rendered


def _install_render_output(
    job_id: str,
    project: str,
    adopted: tuple[Path, ...],
    *,
    preview: bool,
    expected_state: tuple[str, int, str],
) -> dict:
    if len(adopted) != 1:
        raise jobs_mod.JobError(
            "INVALID_STAGE_RESULT", "render returned an invalid artifact set"
        )
    jobs_mod.validate_mp4_output(adopted[0])
    if _job_cancelled(job_id):
        raise jobs_mod.JobError("CANCELLED", "render was cancelled before materialization")
    if _project_job_state(project) != expected_state:
        raise jobs_mod.JobError(
            "STALE_REVISION", "project changed before render materialization"
        )
    if preview:
        target = config.PREVIEW_DIR / f"{job_id}.mp4"
        public = f"/media/preview/{job_id}.mp4"
    else:
        target = config.FINAL_DIR / f"{project}.compiled.mp4"
        public = f"/media/compiled/{project}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{job_id}.tmp")
    with adopted[0].open("rb") as source, temporary.open("wb") as destination:
        while chunk := source.read(jobs_mod.CHUNK_SIZE):
            destination.write(chunk)
        destination.flush()
        os.fsync(destination.fileno())
    if _job_cancelled(job_id):
        temporary.unlink(missing_ok=True)
        raise jobs_mod.JobError("CANCELLED", "render was cancelled before materialization")
    if _project_job_state(project) != expected_state:
        temporary.unlink(missing_ok=True)
        raise jobs_mod.JobError(
            "STALE_REVISION", "project changed before render materialization"
        )
    prepared = [(temporary, target)]
    if not preview:
        doc, _revision = helpers._read_project_doc(project)
        hash_target = config.FINAL_DIR / f"{project}.compiled.mp4.hash"
        hash_temporary = hash_target.with_name(f".{hash_target.name}.{job_id}.tmp")
        try:
            hash_temporary.write_text(helpers._timeline_hash(doc or {}), encoding="utf-8")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        prepared.append((hash_temporary, hash_target))
    _publish_files_transactionally(job_id, prepared)
    if preview:
        _prune_preview_dir()
    return {"output": public, "errors": []}


def _local_action(
    job_id: str, action: Literal["transcribe", "reframe"], project: str, source: Path
) -> contracts.StageResult:
    private = config.STATE_HOME / "job-results" / job_id
    if action == "transcribe":
        try:
            result = transcribe_stage.transcribe(source, output_dir=private)
        except (ImportError, ModuleNotFoundError) as exc:
            raise jobs_mod.JobError(
                "DEPENDENCY_UNAVAILABLE", "local transcription requires WhisperX"
            ) from exc
        if result.status != "ok" or not result.output_path:
            return result
        outputs = tuple(
            private / name
            for name in (
                f"{source.stem}.words.json",
                f"{source.stem}.srt",
                f"{source.stem}.txt",
            )
        )
        return _materialize_stage_output(job_id, action, source, outputs)
    doc, _revision = helpers._read_project_doc(project)
    fps = int((doc or {}).get("fps", config.DEFAULT_FPS))
    try:
        reframe_stage.analyze(source, fps=fps, output_dir=private)
    except (ImportError, ModuleNotFoundError) as exc:
        raise jobs_mod.JobError(
            "DEPENDENCY_UNAVAILABLE", "local reframe requires OpenCV and MediaPipe"
        ) from exc
    return _materialize_stage_output(
        job_id, action, source, (private / f"{source.stem}.reframe.json",)
    )


def _run_compute_action(job_id: str, job: dict) -> contracts.StageResult:
    action = job.get("kind")
    project = str(job.get("project") or "")
    if action not in {"transcribe", "reframe"}:
        raise jobs_mod.JobError("UNSUPPORTED_ACTION", "job action is not worker eligible")
    source = _compute_job_source(job)
    if source is None or not source.exists():
        raise jobs_mod.JobError("MISSING_ARTIFACT", "source video is unavailable")
    jobs_mod.JobFacts(action).validate()
    return _gpu_run(_local_action, job_id, action, project, source)


def _run_detached_job(job_id: str, job: dict) -> None:
    kind = job.get("kind")
    project = str(job.get("project") or "")
    started_epoch = time.time()
    _current, started = jobs_mod.compare_and_set_status(
        job_id,
        expected={"queued", "retrying"},
        status="running",
        started=started_epoch,
        startedAt=_iso_timestamp(started_epoch),
        attemptState=None,
    )
    if not started:
        return
    if kind == "transcribe":
        state._JOBS[project] = {"status": "running", "started": time.time()}
    _emit_job_update_threadsafe(project, job_id)
    try:
        if kind in {"transcribe", "reframe"}:
            result = _run_compute_action(job_id, job)
            if result.status != "ok":
                status = "error"
                fields = {"error": result.reason or "stage failed"}
                if result.metrics.get("errorCode") in _SAFE_JOB_ERROR_MESSAGES:
                    fields["code"] = result.metrics["errorCode"]
            else:
                status = "done"
                fields = {"output": result.output_path}
        elif kind == "render_final":
            r = _run_render_compute(job_id, job, preview=False)
            if r.get("errors"):
                status = "error"
                fields = {"error": "; ".join(r["errors"])}
            else:
                status = "done"
                fields = {"output": r.get("output")}
        elif kind == "render_scene":
            doc, _revision = helpers._read_project_doc(project)
            if doc is None:
                status = "error"
                fields = {"error": f"unknown project: {project}"}
            else:
                scene_id = (job.get("meta") or {}).get("sceneId")
                _layers, rendered = _gpu_run(_render_scenes, job_id, project, doc, scene_id)
                status = "done"
                fields = {"rendered": rendered}
        elif kind == "render_preview_segment":
            # A larger judge/choose preview window (over the sync cutoff) runs detached so the
            # mcp_gpu bridge never blocks on a multi-minute encode inside its HTTP timeout.
            out = _run_render_compute(job_id, job, preview=True)
            if out.get("error"):
                status = "error"
                fields = {"error": out["error"]}
            else:
                status = "done"
                fields = {"output": out.get("output")}
        else:
            status = "error"
            fields = {"error": f"unknown job kind: {kind}"}
    except _PublicationUnknown as exc:
        # The durable publication claim has already crossed the commit
        # boundary. Even if a best-effort rollback restored the old files,
        # this attempt cannot be safely classified as an ordinary failure.
        status = "unknown_execution"
        fields = {"error": str(exc), "code": "EXECUTION_UNKNOWN"}
    except jobs_mod.JobError as exc:
        if exc.code == "CANCELLED" or _job_cancelled(job_id):
            status = "cancelled"
            fields = {}
        else:
            status = "error"
            fields = {"error": str(exc), "code": exc.code}
    except Exception as e:  # noqa: BLE001 — one bad job must not kill the single-slot worker
        if _job_cancelled(job_id):
            status = "cancelled"
            fields = {}
        else:
            status = "error"
            fields = {"error": str(e)}
    completed_epoch = time.time()
    current, published = jobs_mod.compare_and_set_status(
        job_id,
        expected={"running", "publishing"},
        status=status,
        ended=completed_epoch,
        completedAt=_iso_timestamp(completed_epoch),
        **fields,
    )
    if not published:
        status = str((current or {}).get("status") or status)
    _emit_job_update_threadsafe(project, job_id)
    if kind == "transcribe":
        state._JOBS[project] = {"status": status, "ended": time.time()}


def _worker() -> None:
    """Own the single-slot FIFO until the application asks it to stop.

    The queue is intentionally drained (without executing) after the stop
    signal.  Durable jobs remain on disk for the recovery phase; in-memory
    queue entries must not be replayed by a later application instance merely
    because a sentinel happened to be behind them.
    """

    try:
        while True:
            key = state._JOB_Q.get()
            try:
                if key is state._WORKER_SENTINEL:
                    break
                if state._WORKER_STOP.is_set():
                    continue
                # The queue has historically accepted only strings.  Keep the
                # guard explicit because the private sentinel shares its queue.
                if not isinstance(key, str):
                    continue
                detached = jobs_mod.get(key)
                if detached is not None:
                    _run_detached_job(key, detached)
                    continue
                slug = key
                state._JOBS[slug] = {"status": "running", "started": time.time()}
                try:
                    proc = _gpu_run(
                        subprocess.run,
                        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "transcribe.py"), str(helpers._source(slug))],
                        capture_output=True, text=True,
                    )
                    ok = proc.returncode == 0 and (config.TRANSCRIPTS_DIR / f"{slug}.words.json").exists()
                    state._JOBS[slug] = {
                        "status": "done" if ok else "error", "ended": time.time(),
                        "error": None if ok else (proc.stderr or proc.stdout or "transcribe failed").strip()[-400:],
                    }
                except Exception as e:  # noqa: BLE001 — keep the worker alive across a bad job
                    state._JOBS[slug] = {"status": "error", "ended": time.time(), "error": str(e)}
            finally:
                state._JOB_Q.task_done()
    finally:
        with state._WORKER_LOCK:
            if state._WORKER_THREAD is threading.current_thread():
                state._WORKER_THREAD = None
                state._WORKER_STARTED = False


def _ensure_worker() -> bool:
    """Start the FIFO worker when dispatch is open; return whether it is available."""

    with state._WORKER_LOCK:
        if state._WORKER_STOP.is_set():
            # A completed shutdown leaves the event set as a dispatch gate.
            # Only the next FastAPI lifespan startup may clear it; otherwise a
            # request racing with shutdown could silently restart the worker.
            return False
        if state._WORKER_THREAD is not None and state._WORKER_THREAD.is_alive():
            state._WORKER_STARTED = True
            return True
        if not state._WORKER_STARTED:
            thread = threading.Thread(target=_worker, name="cutgraph-job-worker", daemon=True)
            state._WORKER_THREAD = thread
            thread.start()
            state._WORKER_STARTED = True
        return True


def _stop_fifo_worker() -> None:
    """Signal and boundedly join the application-owned FIFO worker.

    Queued work is intentionally not cancelled or rewritten here.  The
    durable job registry remains authoritative and a later recovery pass will
    classify active records.  This function only prevents new dispatch and
    ensures the worker cannot wait forever on ``Queue.get`` during shutdown.
    """

    with state._WORKER_LOCK:
        state._WORKER_STOP.set()
        thread = state._WORKER_THREAD
        if thread is not None and thread.is_alive():
            state._JOB_Q.put(state._WORKER_SENTINEL)
    if thread is not None and thread is not threading.current_thread() and thread.ident is not None:
        _terminate_owned_processes({thread.ident})
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=state._WORKER_JOIN_TIMEOUT_S)
        if thread.is_alive() and thread.ident is not None:
            # A child may have spawned a short-lived grandchild after the
            # first ownership snapshot. Re-scan before returning from the
            # bounded join so shutdown cannot orphan that tree.
            _terminate_owned_processes({thread.ident})
    with state._WORKER_LOCK:
        if thread is None or not thread.is_alive():
            state._WORKER_THREAD = None
            state._WORKER_STARTED = False


def _stop_background_work() -> None:
    """Stop dispatch and boundedly join upload-triggered background threads."""
    state._BACKGROUND_STOP.set()
    with state._BACKGROUND_LOCK:
        threads = tuple(state._BACKGROUND_THREADS)
    current = threading.current_thread()
    owners = {thread.ident for thread in threads if thread.ident is not None}
    owners.discard(current.ident)
    _terminate_owned_processes(owners)
    deadline = time.monotonic() + state._BACKGROUND_JOIN_TIMEOUT_S
    for thread in threads:
        if thread is current:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)
    _terminate_owned_processes(owners)
    with state._BACKGROUND_LOCK:
        state._BACKGROUND_THREADS.difference_update(thread for thread in threads if not thread.is_alive())


def _prepare_runtime_start() -> None:
    """Re-open dispatch gates for a fresh FastAPI application lifetime."""

    with state._WORKER_LOCK:
        if state._WORKER_THREAD is None or not state._WORKER_THREAD.is_alive():
            state._WORKER_STOP.clear()
            state._WORKER_STARTED = False
    with state._BACKGROUND_LOCK:
        if not state._BACKGROUND_THREADS:
            state._BACKGROUND_STOP.clear()


def _enqueue_compute_action(
    kind: Literal["transcribe", "reframe"],
    project: str,
    *,
    source_slug: str | None = None,
) -> dict:
    """Create a persisted, explainable Studio job for a local compute action."""
    # Direct legacy callers provide their uploaded source explicitly.  The
    # Studio's manual `/api/jobs` route supplies only a project, so derive the
    # sole allowed source from its persisted speech clip rather than assuming
    # the project and upload share a name.
    if source_slug is None:
        # Raw-slug API callers predate project documents and remain supported.
        # Once a document exists, however, its speech clip is authoritative:
        # do not fall back to the project name if that bounded resolution fails.
        source_slug = (
            _project_compute_source_slug(project)
            if helpers._project_path(project).exists()
            else project
        )
    if source_slug is None:
        return {"status": "error", "error": "no source"}
    if names.source_name(source_slug) != source_slug:
        return {"status": "error", "error": "no source"}
    if names.exact_path(config.TRANSCRIPTS_DIR / f"{source_slug}.words.json", config.TRANSCRIPTS_DIR).exists():
        if kind == "transcribe":
            return {"status": "done"}
    cur = state._JOBS.get(project, {}).get("status")
    if cur in ("queued", "running"):
        return {"status": cur}
    if not helpers._source(source_slug).exists():
        return {"status": "error", "error": "no source"}
    if kind == "transcribe" and not addons.available("transcription"):
        return {"status": "error", "error": addons.unavailable_message("transcription"),
                "code": "TRANSCRIPTION_NOT_INSTALLED"}
    facts = jobs_mod.JobFacts(kind)
    try:
        facts.validate()
    except jobs_mod.JobError as exc:
        return {"status": "error", "error": str(exc), "code": exc.code}
    job_id = jobs_mod.new_job(kind, project, {"sourceSlug": source_slug})
    # Publish before the worker can advance
    # this job.  Live SSE consumers then receive the same complete state that
    # reconnect reconciliation reads from the persisted registry.
    state._JOBS[project] = {"status": "queued"}
    _dispatch_queued_job(project, job_id)
    return {"jobId": job_id, "status": "queued"}


def _enqueue_transcribe(
    source_slug: str,
    *,
    project: str | None = None,
) -> dict:
    return _enqueue_compute_action(
        "transcribe",
        project or source_slug,
        source_slug=source_slug,
    )


# ── scrub proxies (proxy verb) ─────────────────────────────────────────
# The editor scrubs a short-GOP 540p proxy, not the source. On upload we kick the proxy encode off a
# fire-and-forget daemon thread (best-effort/additive — the UI falls back to `source` until it lands),
# keeping the upload response snappy for large files. `/media/source/<slug>` serves the same
# `<stem>_540.mp4`, starting its encode on first request if no upload did (`_source_proxy`).
#
# A caller that already knows which Studio project wants the result (new-video-from-footage)
# passes `project=`; `_PROXY_WAITERS` both dedupes concurrent encodes of the same source (upload()
# starts one before the project exists; api_create_project() calls in again once it does) and
# remembers who to notify. Completion fires a `job`-kind SSE event on that project's channel — the
# same transport transcription progress already uses — so the UI never has to poll for readiness.
def _scrub_proxy_path(source: Path) -> Path:
    return config.PROXIES_DIR / f"{source.stem}_540.mp4"


def _source_proxy(source: Path) -> tuple[str, Path | None]:
    """Where a source's scrub proxy stands: ("ready", path), ("building", None) or ("failed", None).
    Asking starts the one encode it needs. make_proxy renames a finished encode into place before
    `_spawn_proxy` drops it from `_PROXY_WAITERS`, so a proxy that isn't building is complete."""
    _spawn_proxy(source, reuse=True)
    with state._BACKGROUND_LOCK:
        if str(source) in state._PROXY_WAITERS:
            return "building", None
    proxy = _scrub_proxy_path(source)
    return ("ready", proxy) if proxy.is_file() else ("failed", None)


def _spawn_proxy(source: Path, *, project: str | None = None, reuse: bool = False) -> None:
    if state._BACKGROUND_STOP.is_set():
        return
    _capture_job_event_loop()  # so the background thread's completion notice has a loop to land on
    key = str(source)
    with state._BACKGROUND_LOCK:
        waiters = state._PROXY_WAITERS.get(key)
        if waiters is not None:
            if project:
                waiters.add(project)
            return  # an encode for this exact source is already in flight
        if reuse and (key in state._PROXY_FAILED or _scrub_proxy_path(source).is_file()):
            return
        if state._BACKGROUND_STOP.is_set():
            return
        state._PROXY_WAITERS[key] = {project} if project else set()

    def _run() -> None:
        proxy_rel: str | None = None
        try:
            if not state._BACKGROUND_STOP.is_set():
                result = _gpu_run(proxy_stage.make_proxy, source)
                if result.status == "ok" and result.output_path:
                    proxy_rel = Path(result.output_path).relative_to(config.STATE_HOME).as_posix()
        except Exception:  # noqa: BLE001 — proxy is best-effort; a failed encode must not crash the server
            pass
        finally:
            with state._BACKGROUND_LOCK:
                state._BACKGROUND_THREADS.discard(threading.current_thread())
                notify = state._PROXY_WAITERS.pop(key, set())
                if proxy_rel:
                    state._PROXY_FAILED.discard(key)
                else:
                    state._PROXY_FAILED.add(key)
            for waiting_project in notify:
                _emit_job_threadsafe(waiting_project, {
                    "type": "job", "kind": "proxy",
                    "status": "ready" if proxy_rel else "error",
                    "proxy": proxy_rel,
                })

    thread = threading.Thread(target=_run, name="cutgraph-proxy", daemon=True)
    with state._BACKGROUND_LOCK:
        # Shutdown may begin between the first check and registration.  Keep
        # the stop gate under the same lock as ownership registration so no
        # proxy can escape tracking at that boundary.
        if state._BACKGROUND_STOP.is_set():
            state._PROXY_WAITERS.pop(key, None)
            return
        state._BACKGROUND_THREADS.add(thread)
    thread.start()


def _prune_preview_dir() -> None:
    """Keep only the newest config.PREVIEW_KEEP preview mp4s — an editing session's dozens of
    taste-decision clips would otherwise fill the disk. Best-effort; a failed unlink never breaks."""
    try:
        files = sorted(config.PREVIEW_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in files[config.PREVIEW_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def _render_facts(doc: dict, *, preview: bool) -> jobs_mod.JobFacts:
    project = project_mod.from_dict(doc)
    project_mod.validate(project)
    end_f = max(
        (
            clip.tlStartF + clip.srcOutF - clip.srcInF
            for track in project.tracks
            for clip in track.clips
        ),
        default=0,
    )
    return jobs_mod.JobFacts(
        "render_final",
        duration_seconds=end_f / project.fps,
        height=project.height,
        preview=preview,
    )


def _run_render_compute(job_id: str, job: dict, *, preview: bool) -> dict:
    project = str(job.get("project") or "")
    doc, _revision = helpers._read_project_doc(project)
    if doc is None:
        return {"errors": [f"unknown project: {project}"]}
    meta = job.get("meta") or {}
    _render_facts(doc, preview=preview).validate()
    expected_state = (project, _revision, helpers._timeline_hash(doc))
    result = _gpu_run(
        _local_render, job_id, project, doc, meta,
        preview=preview, expected_state=expected_state,
    )
    return result
