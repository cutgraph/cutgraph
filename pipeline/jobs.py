"""SDK-free generalized job registry.

The studio owns ONE job registry and ONE single-slot GPU serializer; `scripts/mcp_gpu.py` is only a
loopback-HTTP bridge into it (never a second queue). A detached compute job (render_final, …) gets an
opaque `job_id`, is polled via `GET /api/jobs/<id>`, and reports status transitions on the SSE channel.

Layout mirrors the proposal store: a process-global dict caches each job while the
`outputs/jobs/<id>.json` mirror is the persisted authority (atomic temp-sibling + os.replace).
Transitions consult that mirror under the registry lock, so stale memory cannot revive a cancelled
job. `job_id` is validated (`^[A-Za-z0-9_-]{1,64}$`) and its path asserted under JOBS_DIR.
"""
from __future__ import annotations

import builtins
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NoReturn, cast

import config
from pipeline import contracts
from pipeline.names import NameNotFound, check_case_collision, existing_name, project_name, validate_name
from pipeline.errors import ValidationError

CHUNK_SIZE = 1024 * 1024


class JobError(RuntimeError):
    """A structured, safe reason a job cannot be completed."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class JobFacts:
    action: str
    duration_seconds: float | None = None
    height: int | None = None
    preview: bool = False

    def validate(self) -> None:
        if self.action not in {"transcribe", "reframe", "render_final"}:
            raise JobError("UNSUPPORTED_ACTION", "job action is not allowlisted")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise JobError("MALFORMED_DOCUMENT", "duration must be non-negative")
        if self.height is not None and self.height < 1:
            raise JobError("MALFORMED_DOCUMENT", "height must be positive")


def validate_mp4_output(path: Path) -> None:
    """Check the container header without loading the complete render."""
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
    except OSError as exc:
        raise JobError("MISSING_ARTIFACT", "render result is unavailable") from exc
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise JobError("INVALID_STAGE_RESULT", "render result is not an ISO base media file")


_MAX_STAGE_JSON = 32 * 1024 * 1024


def _stage_error(code: str, message: str) -> NoReturn:
    raise JobError(code, message)


def _stage_json(payload: bytes) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> NoReturn:
        raise ValueError(f"non-standard JSON constant {value}")

    try:
        decoded = json.loads(payload, object_pairs_hook=object_pairs, parse_constant=constant)
    except RecursionError as exc:
        raise ValueError("invalid JSON nesting") from exc

    def check_depth(value: object, depth: int = 0) -> None:
        if depth > 64:
            raise ValueError("JSON nesting limit exceeded")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        if isinstance(value, dict):
            for child in value.values():
                check_depth(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check_depth(child, depth + 1)

    check_depth(decoded)
    return decoded


def validate_stage_output(action: str, payload: bytes) -> None:
    if len(payload) > _MAX_STAGE_JSON:
        _stage_error("PAYLOAD_TOO_LARGE", "stage JSON result exceeds configured limit")
    if action == "render_final":
        if len(payload) < 12 or payload[4:8] != b"ftyp":
            _stage_error("INVALID_STAGE_RESULT", "render result is not an ISO base media file")
        return
    try:
        data = _stage_json(payload)
        if action == "transcribe":
            if not isinstance(data, dict):
                _stage_error("INVALID_STAGE_RESULT", "transcript result must be an object")

            def number(value: object, label: str) -> int | float:
                # Coercion would accept booleans and strings before schema validation.
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    _stage_error("INVALID_STAGE_RESULT", f"{label} must be a finite JSON number")
                if isinstance(value, float) and not math.isfinite(value):
                    _stage_error("INVALID_STAGE_RESULT", f"{label} must be a finite JSON number")
                return value

            segments = data.get("segments")
            fillers = data.get("fillers", [])
            if not isinstance(segments, list) or not isinstance(fillers, list):
                _stage_error("INVALID_STAGE_RESULT", "transcript segments and fillers must be arrays")
            assert isinstance(segments, list)

            def filler_index(value: object) -> int:
                index = number(value, "filler.segIdx")
                if (
                    index < 0
                    or isinstance(index, float) and not index.is_integer()
                    or int(index) >= len(segments)
                ):
                    _stage_error(
                        "INVALID_STAGE_RESULT",
                        "filler.segIdx must be a non-negative integral segment index",
                    )
                return int(index)
            contracts.TranscriptResult(
                language=data["language"],
                duration=cast(float, number(data["duration"], "duration")),
                segments=[
                    contracts.TranscriptSegment(
                        start=cast(float, number(s["start"], "segment.start")),
                        end=cast(float, number(s["end"], "segment.end")),
                        text=s["text"],
                        words=[
                            contracts.WordToken(
                                word=w["word"],
                                start=cast(float, number(w["start"], "word.start")),
                                end=cast(float, number(w["end"], "word.end")),
                                score=cast(float, number(w["score"], "word.score")),
                            )
                            for w in s["words"]
                        ],
                    )
                    for s in segments
                ],
                fillers=[
                    contracts.FillerToken(
                        word=item["word"],
                        start=cast(float, number(item["start"], "filler.start")),
                        end=cast(float, number(item["end"], "filler.end")),
                        score=cast(float, number(item["score"], "filler.score")),
                        segIdx=filler_index(item["segIdx"]),
                    )
                    for item in fillers
                ],
            ).validate()
        elif action == "reframe":
            from pipeline.reframe import _metadata_from_dict

            _metadata_from_dict(cast(dict, data)).validate()
        else:
            _stage_error("UNSUPPORTED_ACTION", "worker result action is unsupported")
    except (
        UnicodeDecodeError,
        JobError,
        KeyError,
        TypeError,
        ValueError,
        ValidationError,
    ) as exc:
        raise JobError(
            "INVALID_STAGE_RESULT", "worker output does not satisfy the action schema"
        ) from exc


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_registry: dict[str, dict] = {}
_lock = threading.Lock()
_TERMINAL_STATUSES = frozenset({
    "done", "succeeded", "error", "failed", "cancelled", "stale",
    "interrupted", "unknown_execution",
})
_ALLOWED_TRANSITIONS = {
    "needs-approval": frozenset({"queued", "cancelled"}),
    "queued": frozenset({"running", "cancelled", "interrupted"}),
    "running": _TERMINAL_STATUSES | {"publishing"},
    # Publication is an explicit, durable commit point.  Cancellation is no
    # longer accepted once a worker owns this state, so a caller can never
    # observe "cancelled" alongside an artifact installed by that worker.
    "publishing": frozenset({"done", "error", "unknown_execution"}),
    "done": frozenset({"done"}),
    "succeeded": frozenset({"succeeded"}),
    "error": frozenset({"error"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
    "stale": frozenset({"stale"}),
    "interrupted": frozenset({"interrupted"}),
    "unknown_execution": frozenset({"unknown_execution"}),
}


def new_id() -> str:
    return uuid.uuid4().hex


def _created_at() -> str:
    """Return an RFC3339 timestamp with enough precision to order rapid attempts."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _created_ordinal() -> int:
    """Durable tie-breaker for timestamps, including mocked/equal wall-clock values."""
    return time.time_ns()


def _path(job_id: str) -> Path:
    if existing_name(job_id) is None:
        raise ValueError(f"unsafe job id: {job_id!r}")
    root = config.JOBS_DIR.resolve()
    path = (config.JOBS_DIR / f"{job_id}.json").resolve()
    if path.name != f"{job_id}.json":
        raise NameNotFound("The requested item was not found.")
    if path.parent != root:
        raise ValueError(f"job id escapes JOBS_DIR: {job_id!r}")
    return path


def _atomic_write(job_id: str, payload: dict) -> None:
    path = _path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2) + "\n"
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def new_job(kind: str, project: str, meta: dict, *, status: str = "queued") -> str:
    """Create a job (default status "queued"; render_final passes "needs-approval" for the human gate).
    Returns the opaque job_id. Atomically mirrored to disk."""
    job_id = new_id()
    validate_name(job_id)
    check_case_collision(config.JOBS_DIR / f"{job_id}.json")
    job = {
        "id": job_id,
        "kind": kind,
        "project": project,
        "status": status,
        "meta": meta or {},
        "createdAt": _created_at(),
        "createdOrdinal": _created_ordinal(),
    }
    with _lock:
        _registry[job_id] = job
        _atomic_write(job_id, job)
    return job_id


def new_or_existing_render_final(project: str, meta: dict, *, dedupe_key: str) -> tuple[str, dict, bool]:
    """Atomically create one final-render job per project/output.

    A successful final with the same immutable render identity is returned on
    a repeated POST. Failed/cancelled jobs may be retried. Looking at the disk
    mirror as well as memory makes this idempotent across a Studio restart.
    """
    if not isinstance(dedupe_key, str) or not dedupe_key:
        raise ValueError("render dedupe key must be a non-empty string")
    # Every non-retryable state with the same immutable identity is reusable.
    # In particular, a worker that has crossed the durable publication commit
    # point must not be shadowed by another human-gated render request.
    reusable = {"needs-approval", "queued", "running", "publishing", "done"}
    with _lock:
        candidates = dict(_registry)
        if config.JOBS_DIR.exists():
            for path in config.JOBS_DIR.glob("*.json"):
                try:
                    candidate = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                job_id = candidate.get("id")
                if isinstance(job_id, str) and _ID_RE.match(job_id):
                    # The persisted mirror is authoritative even when this
                    # process still has an older active entry cached.
                    candidates[job_id] = candidate
        existing = [
            job for job in candidates.values()
            if job.get("kind") == "render_final" and job.get("project") == project
            and job.get("dedupeKey") == dedupe_key
            and job.get("status") in reusable
        ]
        if existing:
            job = max(existing, key=lambda item: (
                str(item.get("createdAt", "")),
                int(item.get("createdOrdinal", -1)) if isinstance(item.get("createdOrdinal"), int) else -1,
            ))
            job_id = str(job["id"])
            _registry[job_id] = job
            return job_id, job, False
        job_id = new_id()
        validate_name(job_id)
        check_case_collision(config.JOBS_DIR / f"{job_id}.json")
        job = {
            "id": job_id,
            "kind": "render_final",
            "project": project,
            "status": "needs-approval",
            "meta": meta or {},
            "dedupeKey": dedupe_key,
            "createdAt": _created_at(),
            "createdOrdinal": _created_ordinal(),
        }
        _registry[job_id] = job
        _atomic_write(job_id, job)
        return job_id, job, True


def set(job_id: str, **fields: object) -> "dict | None":  # noqa: A001 — registry verb, not the builtin
    """Atomically merge status/progress/output/error (any fields) into a job. Returns the updated job,
    or None if the id is unknown."""
    with _lock:
        job = _current_locked(job_id)
        if job is None:
            return None
        new_status = fields.get("status")
        if (
            job.get("status") in _TERMINAL_STATUSES
            and new_status is not None
            and new_status != job.get("status")
        ):
            return job
        job = {**job, **fields}
        _registry[job_id] = job
        _atomic_write(job_id, job)
        return job


def compare_and_set_status(
    job_id: str,
    *,
    expected: Collection[str],
    status: str,
    **fields: object,
) -> tuple["dict | None", bool]:
    """Transition only when the persisted current status is expected."""
    expected_statuses = frozenset(expected)
    if not expected_statuses:
        raise ValueError("expected statuses must not be empty")
    if "status" in fields:
        raise ValueError("status must be passed via the status argument")
    with _lock:
        job = _current_locked(job_id)
        if job is None:
            return None, False
        current = job.get("status")
        if current not in expected_statuses:
            return job, False
        allowed = _ALLOWED_TRANSITIONS.get(str(current), frozenset())
        if status not in allowed:
            return job, False
        updated = {**job, **fields, "status": status}
        _registry[job_id] = updated
        _atomic_write(job_id, updated)
        return updated, True


def next_event_sequence(job_id: str) -> int:
    """Persist and return the next monotonic presentation-event sequence."""
    with _lock:
        job = _current_locked(job_id)
        if job is None:
            raise ValueError("no such job")
        current = job.get("eventSequence", 0)
        sequence = current + 1 if isinstance(current, int) and not isinstance(current, bool) else 1
        updated = {**job, "eventSequence": sequence}
        _registry[job_id] = updated
        _atomic_write(job_id, updated)
        return sequence


def _read_file(job_id: str) -> "dict | None":
    try:
        path = _path(job_id)
    except (ValueError, NameNotFound):
        return None
    if not path.exists():
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
        return job
    except (json.JSONDecodeError, OSError):
        return None


def get(job_id: str) -> "dict | None":
    with _lock:
        return _current_locked(job_id)


def _current_locked(job_id: str) -> "dict | None":
    """Return disk-authoritative state. Caller must hold ``_lock``."""
    try:
        persisted_exists = _path(job_id).exists()
    except (ValueError, NameNotFound):
        return None
    persisted = _read_file(job_id)
    if persisted_exists:
        if persisted is None:
            _registry.pop(job_id, None)
            return None
        _registry[job_id] = persisted
        return persisted
    return _registry.get(job_id)


def all() -> dict:  # noqa: A001 — registry verb, not the builtin
    with _lock:
        return dict(_registry)


def all_persisted() -> dict[str, dict]:
    """Return every valid durable job, with disk state winning over memory."""
    with _lock:
        result = dict(_registry)
        if config.JOBS_DIR.exists():
            for path in config.JOBS_DIR.glob("*.json"):
                job_id = path.stem
                if not _ID_RE.fullmatch(job_id):
                    continue
                persisted = _read_file(job_id)
                if isinstance(persisted, dict):
                    result[job_id] = persisted
                    _registry[job_id] = persisted
        return result


def recover_interrupted() -> dict[str, dict]:
    """Fail closed for durable jobs abandoned by a previous application instance.

    A queued job is known not to have reached this process's execution claim, so it becomes
    ``interrupted``. Once a job was running or publishing, the new process cannot prove whether an
    external tool crossed its commit boundary; those records become
    ``unknown_execution``. Recovery never re-enqueues work and never adopts an output file as proof
    of success.
    """
    recovered: dict[str, dict] = {}
    with _lock:
        if not config.JOBS_DIR.exists():
            return recovered
        for path in config.JOBS_DIR.glob("*.json"):
            job_id = path.stem
            if not _ID_RE.fullmatch(job_id):
                continue
            job = _read_file(job_id)
            if not isinstance(job, dict) or job.get("id") != job_id:
                continue
            status = job.get("status")
            if status == "queued":
                next_status = "interrupted"
                reason = "application_restarted_before_execution"
            elif isinstance(status, str) and status in {"running", "publishing"}:
                next_status = "unknown_execution"
                reason = "application_restarted_during_execution"
            elif not isinstance(status, str) or status not in _ALLOWED_TRANSITIONS:
                next_status = "unknown_execution"
                reason = "unsupported_job_status"
            else:
                _registry[job_id] = job
                continue
            updated = {
                **job,
                "status": next_status,
                "recoveryReason": reason,
                "recoveredAt": _created_at(),
            }
            if reason == "unsupported_job_status":
                updated.setdefault("recoveryPreviousStatus", status)
            _registry[job_id] = updated
            _atomic_write(job_id, updated)
            recovered[job_id] = updated
    return recovered


_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PRIVATE_KEY = re.compile(r"(?:path|file|secret|token|credential|password)", re.IGNORECASE)
_JOB_KINDS = frozenset({"render_final", "render_preview_segment", "transcribe", "reframe"})
_PUBLIC_JOB_FIELDS = frozenset({
    "id", "kind", "project", "status", "createdAt", "createdOrdinal", "dedupeKey",
    "progress",
    "recoveryReason", "recoveredAt",
})

JobKind = Literal["render_final", "render_preview_segment", "transcribe", "reframe"]


class JobStateError(ValueError):
    """A bounded input failure; no job state is changed."""


@dataclass(frozen=True)
class JobPreflight:
    status: Literal["ready", "rejected"]
    kind: JobKind | None
    project: str | None
    reason: str | None = None


@dataclass(frozen=True)
class JobOperation:
    job_id: str
    job: dict[str, object]
    created: bool
    preflight: JobPreflight


def _reject(reason: str, *, kind: JobKind | None = None, project: str | None = None) -> JobPreflight:
    return JobPreflight("rejected", kind, project, reason)


def _public_job(job: Mapping[str, object]) -> dict[str, object]:
    """Return public job fields without durable metadata or private paths."""
    return {key: value for key, value in job.items() if key in _PUBLIC_JOB_FIELDS}


def _safe_metadata(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return not isinstance(value, float) or value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, Mapping):
        return builtins.all(
            isinstance(key, str)
            and not _PRIVATE_KEY.search(key)
            and _safe_metadata(entry)
            for key, entry in value.items()
        )
    if isinstance(value, (list, tuple)):
        return builtins.all(_safe_metadata(entry) for entry in value)
    return False


def preflight_job(
    kind: str,
    project: str,
    *,
    metadata: Mapping[str, object] | None = None,
    dedupe_key: str | None = None,
) -> JobPreflight:
    """Validate closed local-job inputs without touching the job registry."""
    if kind not in _JOB_KINDS:
        return _reject("unsupported local job kind")
    selected_kind = cast(JobKind, kind)  # membership above narrows the runtime value for callers
    if project_name(project) is None:
        return _reject("project must be a safe local project slug", kind=selected_kind, project=project if isinstance(project, str) else None)
    if metadata is not None:
        if not isinstance(metadata, Mapping) or not _safe_metadata(metadata):
            return _reject("job metadata must be bounded public JSON without path or credential fields", kind=selected_kind, project=project)
        try:
            encoded = json.dumps(metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return _reject("job metadata must be bounded public JSON", kind=selected_kind, project=project)
        if len(encoded.encode("utf-8")) > 32 * 1024:
            return _reject("job metadata exceeds the 32 KiB bound", kind=selected_kind, project=project)
    if dedupe_key is not None and (not isinstance(dedupe_key, str) or not 1 <= len(dedupe_key) <= 256 or not _SAFE.fullmatch(dedupe_key)):
        return _reject("dedupe key must be a bounded safe identifier", kind=selected_kind, project=project)
    return JobPreflight("ready", selected_kind, project)


def create_render_final_job(
    project: str,
    *,
    dedupe_key: str,
    metadata: Mapping[str, object] | None = None,
) -> JobOperation:
    """Create/reuse the human-gated ``render_final`` job."""
    preflight = preflight_job("render_final", project, metadata=metadata, dedupe_key=dedupe_key)
    if preflight.status == "rejected":
        raise JobStateError(preflight.reason or "job preflight rejected")
    job_id, job, created = new_or_existing_render_final(
        project, dict(metadata or {}), dedupe_key=dedupe_key,
    )
    return JobOperation(job_id, _public_job(job), created, preflight)


def approve_job(job_id: str) -> tuple[dict[str, object] | None, bool]:
    """Release a human-gated job using the registry's persisted CAS."""
    if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id):
        raise JobStateError("bad job id")
    current = get(job_id)
    if current is None:
        return None, False
    updated, changed = compare_and_set_status(
        job_id, expected={"needs-approval"}, status="queued",
    )
    return (_public_job(updated or current) if updated or current else None), changed


def cancel_job(job_id: str) -> tuple[dict[str, object] | None, bool]:
    """Cancel before publication, preserving native running/publishing semantics."""
    if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id):
        raise JobStateError("bad job id")
    current = get(job_id)
    if current is None:
        return None, False
    updated, changed = compare_and_set_status(
        job_id,
        expected={"needs-approval", "queued", "running"},
        status="cancelled",
        cancelRequestedAt=time.time(),
    )
    return (_public_job(updated or current) if updated or current else None), changed


def recover_jobs() -> dict[str, dict[str, object]]:
    """Run restart reconciliation; never re-enqueues or adopts output."""
    return {job_id: _public_job(job) for job_id, job in recover_interrupted().items()}
