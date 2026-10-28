"""SDK-free proposal store.

One file per proposal at `outputs/proposals/<slug>/<id>.json` — an additive sibling namespace, zero
migration (mirrors the flat `.words.json` convention). A proposal is a reviewable RFC-6902 patch the
agent WRITES but never applies: accept rides the server-side guarded `/api/proposal/<project>/<id>/accept`
operation and flips the file's `status`. Rejected/stale proposals stay in place with `status` flipped — the
file IS the archive.

Path-safety: `<slug>` matches the studio slug charset, `<id>` matches the project id charset
(`^[A-Za-z0-9_-]{1,64}$`, becomes a filename), and the built path is asserted to stay under
PROPOSALS_DIR (anchor-equality idiom, project.py:405). Writes are atomic (temp-sibling + os.replace);
the read-modify-write in `set_status` is guarded by a per-slug lock.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
from pipeline.errors import ValidationError
from pipeline.names import check_case_collision, exact_path, existing_name, validate_name

_STATUSES = frozenset({"pending", "accepted", "rejected", "stale"})
_CITATION_KINDS = frozenset({"transcript"})

# Per-slug locks so two concurrent status flips on the same project can't lose an update.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(slug: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(slug)
        if lk is None:
            lk = threading.Lock()
            _locks[slug] = lk
        return lk


def new_id() -> str:
    return uuid.uuid4().hex


# ── Server-side RFC-6902 identity guards ─────────────────────────────────
# The agent cannot be trusted to emit `test` guards (LLMs omit them), so the STORE derives an identity
# guard from the CURRENT doc for every mutating op and prepends it. Accept replays the stored (guarded)
# ops through apply_ops, which runs the `test` and fails CLOSED (400 → stale) when an intervening edit
# shifted the target — instead of a silent lossy misapply. The guard anchors on the deepest id-bearing
# element on the op's path (an id survives array reordering; a bare index does not).
_MUTATING = frozenset({"add", "remove", "replace", "move", "copy"})


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _anchor_guard(doc: dict, path: str) -> "dict | None":
    """Walk `path` from the doc root and return an identity `test` op for the DEEPEST ancestor (or the
    target itself) that is a dict carrying a string `id` — `{"op":"test","path":"<...>/id","value":id}`.
    None when nothing on the path has an id (e.g. a top-level `/width` replace)."""
    node: object = doc
    prefix: list[str] = []
    best: "dict | None" = None
    for raw in path.split("/")[1:]:
        token = _unescape(raw)
        if isinstance(node, list):
            try:
                idx = int(token)
            except ValueError:
                break
            if not (0 <= idx < len(node)):
                break
            node = node[idx]
        elif isinstance(node, dict):
            if token not in node:
                break
            node = node[token]
        else:
            break
        prefix.append(token)
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            pointer = "/" + "/".join(_escape(p) for p in prefix) + "/id"
            best = {"op": "test", "path": pointer, "value": node["id"]}
    return best


def guard_ops(ops: list, doc: "dict | None") -> list:
    """Prepend a derived identity guard before each mutating op. Idempotent-ish: an op the
    agent already guarded (an identical preceding `test`) is not double-guarded. With no `doc` the ops
    pass through unchanged (the caller had no current document to anchor against)."""
    if not doc:
        return list(ops)
    out: list = []
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in _MUTATING:
            out.append(op)
            continue
        path = op.get("path")
        # An `add` targets a not-yet-existing position; anchor on the container array's parent instead.
        guard_path = path.rsplit("/", 1)[0] if op.get("op") == "add" and isinstance(path, str) else path
        guard = _anchor_guard(doc, guard_path) if isinstance(guard_path, str) else None
        if guard is not None and (not out or out[-1] != guard):
            out.append(guard)
        out.append(op)
    return out


def proposal_event(proposal: dict) -> dict:
    """The SSE `proposal` event for a freshly written proposal. Carries the REAL (guarded) `ops` so a
    LIVE card can be accepted directly — without a `getProposals` round-trip that the live SSE path
    never triggers (the old event omitted `ops`, so accept PATCHed an EMPTY op list, a silent
    no-op recorded as 'accepted')."""
    return {
        "type": "proposal",
        "id": proposal["id"],
        "title": proposal.get("title"),
        "rationale": proposal.get("rationale"),
        "savesSec": proposal.get("savesSec"),
        "ops": proposal.get("ops", []),
        "intentGroups": proposal.get("intentGroups", []),
        "citations": proposal.get("citations", []),
        "preview": proposal.get("preview"),
        "marks": proposal.get("marks", []),
        "status": "pending",
        "baseRevision": proposal.get("baseRevision"),
    }


def _dir(slug: str) -> Path:
    if existing_name(slug) is None:
        raise ValueError(f"unsafe proposal slug: {slug!r}")
    path = config.PROPOSALS_DIR / slug
    exact_path(path)
    return path


def _path(slug: str, pid: str) -> Path:
    if existing_name(pid) is None:
        raise ValueError(f"unsafe proposal id: {pid!r}")
    base = _dir(slug).resolve()
    exact_path(base / f"{pid}.json")
    root = config.PROPOSALS_DIR.resolve()
    path = (base / f"{pid}.json").resolve()
    if base != root and root not in base.parents:
        raise ValueError(f"proposal slug escapes PROPOSALS_DIR: {slug!r}")
    if path.parent != base:
        raise ValueError(f"proposal id escapes its slug dir: {pid!r}")
    return path


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _nonempty(value: object, label: str) -> str:
    if not (isinstance(value, str) and value.strip()):
        raise ValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _citation_list(citations: object, *, required: bool) -> list[dict]:
    """Validate source-span citations without ever treating an LLM explanation as evidence.

    Citations name transcript spans (seconds) the reviewer can inspect.
    """
    if citations is None and not required:
        return []
    if not (isinstance(citations, list) and citations):
        raise ValidationError("citations: major proposals require evidence citations, a non-empty array of "
                              "{kind:'transcript', start, end, summary}")
    out: list[dict] = []
    for i, raw in enumerate(citations):
        if not isinstance(raw, dict):
            raise ValidationError(f"citations[{i}] must be an object {{kind:'transcript', start, end, summary}}")
        kind = raw.get("kind")
        if kind not in _CITATION_KINDS:
            raise ValidationError(f"citations[{i}].kind must be transcript")
        start, end = raw.get("start"), raw.get("end")
        if not (isinstance(start, (int, float)) and not isinstance(start, bool) and start >= 0):
            raise ValidationError(f"citations[{i}].start must be a non-negative number of seconds")
        if not (isinstance(end, (int, float)) and not isinstance(end, bool) and end > start):
            raise ValidationError(f"citations[{i}].end must be after start, in seconds")
        citation = {"kind": kind, "start": start, "end": end, "summary": _nonempty(raw.get("summary"), f"citations[{i}].summary")}
        out.append(citation)
    return out


def _intent_groups(groups: object, fallback_ops: list, title: str) -> tuple[list[dict], list]:
    """Normalize material edits into named groups; accepting the proposal remains one undo step."""
    # Historical tests and callers may create an intentionally empty placeholder proposal.
    # Keep those readable; new agent-tool calls always supply non-empty explicit groups.
    if groups is None and not fallback_ops:
        return [], []
    if groups is None:
        groups = [{"id": "change-1", "intent": title, "ops": fallback_ops}]
    if not (isinstance(groups, list) and groups):
        raise ValidationError("intentGroups must be a non-empty list")
    seen: set[str] = set()
    normalized: list[dict] = []
    flattened: list = []
    for i, raw in enumerate(groups):
        if not isinstance(raw, dict):
            raise ValidationError(f"intentGroups[{i}] must be an object")
        group_id = _nonempty(raw.get("id"), f"intentGroups[{i}].id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group_id):
            raise ValidationError(f"intentGroups[{i}].id must be a portable identifier")
        if group_id in seen:
            raise ValidationError("duplicate intent group id")
        group_ops = raw.get("ops")
        if not (isinstance(group_ops, list) and group_ops):
            raise ValidationError(f"intentGroups[{i}].ops must not be empty")
        seen.add(group_id)
        normalized.append({"id": group_id, "intent": _nonempty(raw.get("intent"), f"intentGroups[{i}].intent"), "ops": group_ops})
        flattened.extend(group_ops)
    return normalized, flattened


def _preview(preview: object, *, required: bool) -> dict | None:
    if preview is None and not required:
        return None
    if not isinstance(preview, dict):
        raise ValidationError("material proposals require a compile preview")
    start, end, ref = preview.get("startF"), preview.get("endF"), preview.get("previewRef")
    if not (isinstance(start, int) and not isinstance(start, bool) and start >= 0):
        raise ValidationError("preview.startF must be non-negative")
    if not (isinstance(end, int) and not isinstance(end, bool) and end > start):
        raise ValidationError("preview.endF must be after startF")
    if not (isinstance(ref, str) and re.fullmatch(r"/media/preview/[A-Za-z0-9_-]+\.mp4", ref)):
        raise ValidationError("preview.previewRef must be a served compile preview")
    return {"startF": start, "endF": end, "previewRef": ref}


def write(
    slug: str, *, ops: list, title: str, rationale: str, base_revision: int,
    doc: "dict | None" = None, saves_sec: "float | None" = None, intent_groups: object = None,
    citations: object = None, material: bool = False, preview: object = None, require_evidence: bool = False,
    marks: list[dict] | None = None,
) -> dict:
    """Persist a new pending proposal atomically; returns the full proposal dict (incl. generated id).

    When `doc` (the current project) is supplied, server-side identity guards are derived and stored as
    part of `ops` so accept fails closed on a stale target. Without `doc` the ops are stored
    verbatim (used by tests that pre-guard their ops)."""
    _nonempty(title, "title")
    _nonempty(rationale, "rationale")
    _dir(slug)  # validate the namespace before validating any caller-provided proposal shape
    groups, grouped_ops = _intent_groups(intent_groups, ops, title)
    citations_out = _citation_list(citations, required=require_evidence)
    preview_out = _preview(preview, required=material)
    pid = new_id()
    validate_name(pid)
    check_case_collision(config.PROPOSALS_DIR / slug / f"{pid}.json")
    guarded = guard_ops(grouped_ops, doc)
    proposal: dict = {
        "id": pid,
        "ops": guarded,
        "baseRevision": base_revision,
        "title": title,
        "rationale": rationale,
        "status": "pending",
        "intentGroups": groups,
        "citations": citations_out,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if saves_sec is not None:
        proposal["savesSec"] = saves_sec
    if preview_out is not None:
        proposal["preview"] = preview_out
    # The Story view's per-word review marks ({a, b, kind, label}, b exclusive): which transcript words
    # the Script lens highlights while the proposal is pending. Server-built by pipeline.copilot.
    if marks:
        proposal["marks"] = marks
    _atomic_write(_path(slug, pid), proposal)
    return proposal


def read(slug: str, pid: str) -> "dict | None":
    path = _path(slug, pid)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_all(slug: str) -> list[dict]:
    d = _dir(slug)
    if not d.exists():
        return []
    out: list[dict] = []
    for f in d.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda p: (p.get("createdAt") or "", p.get("id") or ""))
    return out


def set_status(slug: str, pid: str, status: str, *, reason: "str | None" = None) -> "dict | None":
    """Atomic read-modify-write of a proposal's status under a per-slug lock. Returns the updated
    proposal, or None if it does not exist."""
    if status not in _STATUSES:
        raise ValueError(f"invalid proposal status: {status!r}")
    path = _path(slug, pid)
    with _lock(slug):
        if not path.exists():
            return None
        proposal = json.loads(path.read_text(encoding="utf-8"))
        proposal["status"] = status
        if reason is not None:
            proposal["rejectedReason"] = reason
        _atomic_write(path, proposal)
        return proposal


def begin_accept(slug: str, pid: str, acceptance: dict) -> "dict | None":
    """Durably journal an acceptance before its project write.

    Retrying the same proposal returns the existing journal, so a lost response or a failure in a
    later side effect never causes the RFC-6902 operations to run a second time.
    """
    path = _path(slug, pid)
    with _lock(slug):
        if not path.exists():
            return None
        proposal = json.loads(path.read_text(encoding="utf-8"))
        existing = proposal.get("acceptance")
        if existing is not None:
            if existing != acceptance:
                raise ValueError("proposal acceptance journal conflicts")
            return proposal
        if proposal.get("status") != "pending":
            return proposal
        proposal["acceptance"] = acceptance
        _atomic_write(path, proposal)
        return proposal


def mark_acceptance_step(slug: str, pid: str, step: str) -> "dict | None":
    """Mark one idempotent post-write acceptance side effect complete."""
    path = _path(slug, pid)
    with _lock(slug):
        if not path.exists():
            return None
        proposal = json.loads(path.read_text(encoding="utf-8"))
        acceptance = proposal.get("acceptance")
        if not isinstance(acceptance, dict):
            raise ValueError("proposal has no acceptance journal")
        steps = acceptance.setdefault("steps", {})
        if not isinstance(steps, dict):
            raise ValueError("proposal acceptance journal steps are malformed")
        steps[step] = True
        _atomic_write(path, proposal)
        return proposal
