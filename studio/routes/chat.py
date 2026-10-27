from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from studio.errors import JSONResponse

import config
from pipeline import decisions as decisions_mod
from pipeline import notes as notes_mod
from pipeline import project as project_mod
from pipeline import names
from pipeline import proposals as proposals_mod
from pipeline.agent_providers import Availability, ProviderRegistry
from pipeline import addons, agent_credentials
from studio import helpers, state
from studio.errors import agent_error_data, public_error
from pipeline.errors import ValidationError
from pipeline.secret_store import SecretStoreError

router = APIRouter()


def _provider_registry(*, verify: bool = True) -> ProviderRegistry:
    from pipeline.agent_providers import registry_from_config
    if not addons.available("assistant"):
        message = addons.unavailable_message("assistant")
        availability = Availability(False, "assistant_not_installed", message)
    else:
        status = state._claude_connection.status(verify=verify)
        availability = Availability(True) if status["connected"] and status["state"] == "ready" else Availability(
            False, "authentication_checking" if status["state"] == "checking" else "authentication_required",
            agent_credentials.connection_message(status),
        )
    return registry_from_config(config, claude_availability=availability)


# Durable agent events (chat_text/agent_error) mirrored into the jsonl for reconnect reconcile; the
# ephemeral SSE deltas (tool_use/turn_done) are not replayed. Proposals have their own store.
_DURABLE_CHAT_TYPES = frozenset({"chat_text", "agent_error", "proposal"})


def _chat_log_path(slug: str) -> Path:
    return names.exact_path(config.CHAT_DIR / f"{slug}.jsonl")


def _append_chat_log(slug: str, entry: dict) -> None:
    """Append one JSON line. All callers run on the single event loop (workers=1), so a plain append
    is atomic enough; O_APPEND of a small line never interleaves."""
    config.CHAT_DIR.mkdir(parents=True, exist_ok=True)
    with open(_chat_log_path(slug), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(agent_credentials.redact_data(entry)) + "\n")


def _read_chat_log(slug: str, n: int = 200) -> list[dict]:
    try:
        agent_credentials.resolve_key()
    except SecretStoreError:
        return []
    path = _chat_log_path(slug)
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") == "agent_error":
                entry = {"role": "agent", "type": "agent_error", **agent_error_data(entry)}
            entries.append(agent_credentials.redact_data(entry))
        except json.JSONDecodeError:
            continue
    return entries[-n:]


def _make_tool_ctx(project: str):  # noqa: ANN201 — agent_host.ToolCtx (lazy import)
    """Build the per-project ToolCtx the AgentHost injects: readers over the current doc/transcript and
    an emit that fans the event onto the SSE queue AND mirrors durable turns into the chat jsonl."""
    from scripts import agent_host

    def read_doc() -> "tuple[dict | None, int]":
        return helpers._read_project_doc(project)

    def read_transcript() -> "dict | None":
        slug = helpers._safe(helpers._transcript_slug(project))
        words_path = names.exact_path(config.TRANSCRIPTS_DIR / f"{slug}.words.json") if slug else None
        if not words_path or not words_path.exists():
            return None
        try:
            return helpers._reshape_transcript(json.loads(words_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return None


    def fps() -> int:
        doc, _rev = helpers._read_project_doc(project)
        return int((doc or {}).get("fps", config.DEFAULT_FPS))

    def channel() -> "str | None":
        doc, _rev = helpers._read_project_doc(project)
        return (doc or {}).get("channel")

    def read_preferences(scope: str) -> dict:
        return decisions_mod.summary(project, scope=scope, channel=channel())


    def emit(msg: dict) -> None:
        msg = agent_credentials.redact_data(msg)
        if msg.get("type") == "agent_error":
            if msg.get("authentication_failed") is True:
                state._claude_connection.reject(msg.get("key_fingerprint"))
            msg = {"type": "agent_error", **agent_error_data(msg)}
        state.hub.publish_event(project, msg)
        if msg.get("type") in _DURABLE_CHAT_TYPES:
            _append_chat_log(project, {"role": "agent", **msg})

    return agent_host.ToolCtx(
        slug=project, read_doc=read_doc, read_transcript=read_transcript, fps=fps, emit=emit,
        read_preferences=read_preferences, channel=channel,
    )


def _chat_host():  # noqa: ANN202 — AgentHost (lazy import) or None when disabled
    """The process-global AgentHost singleton (workers=1). A test override short-circuits the SDK path;
    otherwise the real host is built lazily on first chat so a box without the SDK still boots."""

    if state._agent_host_override is not None:
        return state._agent_host_override
    if config.AGENT_HOST_MODE not in ("inproc", "subprocess"):
        return None
    if state._agent_host is None:
        from scripts import agent_host
        state._agent_host = agent_host.AgentHost(
            make_session=agent_host.make_provider_session, make_ctx=_make_tool_ctx,
        )
    return state._agent_host


def _existing_chat_host():  # noqa: ANN202
    """The AgentHost ONLY if one already exists (test override or an already-built singleton) — never
    constructs one. Status routes push accept/reject feedback into a LIVE warm session; if the
    user never opened chat there is no session to inform, so nothing is built."""
    return state._agent_host_override if state._agent_host_override is not None else state._agent_host


@router.post("/api/chat/{p:path}")
async def api_chat(p: str, request: Request) -> Response:
    """Post one user turn to the co-editor. Enqueues on the per-project host (the queue serializes; no
    fail-fast lock needed) and returns immediately — the agent's reply streams out-of-band over the
    existing SSE channel as agent events (chat never blocks on the agent)."""
    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=400)
    doc, _revision = helpers._read_project_doc(project)
    if doc is None:
        return JSONResponse({"error": "no such video"}, status_code=404)
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"error": "missing text"}, status_code=400)
    if not addons.available("assistant"):
        return JSONResponse({"error": addons.unavailable_message("assistant"), "code": "assistant_not_installed"},
                            status_code=409)
    connection = await asyncio.to_thread(state._claude_connection.status)
    if connection["state"] != "ready":
        return JSONResponse({"error": agent_credentials.connection_message(connection), "code": "authentication_required"},
                            status_code=409)
    text = agent_credentials.redact(text)
    host = _chat_host()
    if host is None:
        return JSONResponse({"error": "agent host disabled"}, status_code=503)
    # The panel sends model/effort/mode with each turn; from_request validates against the config
    # allow-lists (falling back to defaults) so nothing off the wire reaches the SDK unchecked. A change
    # vs. the running session forces a rebuild inside post_turn.
    from pipeline.agent_providers import SelectionRejected
    from scripts import agent_host
    try:
        registry = await asyncio.to_thread(_provider_registry)
        settings = agent_host.AgentSettings.from_request(body, registry=registry)
    except SelectionRejected:
        # Never enqueue a foreign/unavailable provider as an implicit Claude turn.
        return JSONResponse({"error": "The selected provider is unavailable.", "code": "provider_unavailable"},
                            status_code=409)
    _append_chat_log(project, {"role": "user", "text": text})
    await host.post_turn(project, text, settings)  # type: ignore[attr-defined]
    return JSONResponse({"ok": True})


@router.get("/api/chat/config")
def api_chat_config(verify: bool = True) -> Response:
    """Versioned provider registry, retaining legacy panel fields until the  UI migration."""
    legacy = {
        "models": config.AGENT_MODELS,
        "efforts": config.AGENT_EFFORTS,
        "modes": config.AGENT_MODES,
        "defaults": {
            "model": config.AGENT_MODEL,
            "effort": config.AGENT_DEFAULT_EFFORT,
            "mode": config.AGENT_DEFAULT_MODE,
        },
    }
    return JSONResponse(_provider_registry(verify=verify).to_wire(legacy))


@router.get("/api/chat/{p:path}/log")
def api_chat_log(p: str) -> Response:
    """Tail of the durable chat jsonl — the client reconciles the transcript on reconnect from here,
    never from the (non-replayable) SSE stream."""
    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=400)
    return JSONResponse({"entries": _read_chat_log(project)})


@router.get("/api/chat/{p:path}/proposals")
def api_chat_proposals(p: str) -> Response:
    """The project's durable proposal store (reconnect reconcile of card status — the SSE status flips
    are ephemeral; the file is the authoritative archive)."""
    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=400)
    try:
        return JSONResponse({"proposals": proposals_mod.list_all(project)})
    except ValueError:
        return JSONResponse({"error": "bad project"}, status_code=400)


_PROPOSAL_TRANSITIONS = frozenset({"accepted", "rejected", "stale"})


_ACCEPTANCE_STEPS = frozenset({"projectEvent", "status", "notes", "decision", "feedback", "event"})


def _finish_proposal_status(project: str, current: dict, status: str, reason: object = None) -> dict:
    """Set a terminal proposal state and publish all durable/editorial side effects."""
    pid = str(current["id"])
    reason_text = agent_credentials.redact(reason) if isinstance(reason, str) else None
    updated = proposals_mod.set_status(
        project, pid, status, reason=reason_text if status == "rejected" else None,
    )
    if updated is None:
        raise ValueError("proposal disappeared while changing status")
    title = str(current.get("title") or "")
    if status == "accepted":
        body_note = str(current.get("rationale") or "(no rationale)")
        if reason_text:
            body_note = f"{body_note}\n\n({reason_text})"
        notes_mod.append(project, title, body_note)
    elif status == "rejected":
        notes_mod.append(project, f"rejected: {title}", reason_text or "(no reason given)")
        _append_chat_log(
            project, {"role": "system", "text": f"User rejected proposal {pid}: {reason_text or ''}".strip()},
        )
    if status in {"accepted", "rejected"}:
        project_doc, _revision = helpers._read_project_doc(project)
        channel = (project_doc or {}).get("channel")
        groups = current.get("intentGroups") or []
        operation_ids = [str(group.get("id")) for group in groups
                         if isinstance(group, dict) and group.get("id")]
        citations = current.get("citations") or []
        evidence_ids = [str(citation.get("evidenceId")) for citation in citations
                        if isinstance(citation, dict) and citation.get("evidenceId")]
        decisions_mod.record(
            project, decision=status, intent=title,
            rationale=reason_text or str(current.get("rationale") or "") or None,
            proposal_id=pid, operation_ids=operation_ids, evidence_ids=evidence_ids,
            channel=channel,
        )
    host = _existing_chat_host()
    if host is not None:
        if status == "accepted":
            note = f"System: the user accepted your proposal {pid} ({title})."
        elif status == "rejected":
            note = f"System: the user rejected your proposal {pid} ({title}): {reason_text or 'no reason given'}"
        else:
            note = f"System: proposal {pid} ({title}) went stale before it could be applied — regenerate it."
        host.push_context(project, note)  # type: ignore[attr-defined]
    state.hub.publish_event(project, {
        "type": "proposal", "id": pid, "status": status,
    })
    return updated


def _stale_proposal(project: str, current: dict, message: str) -> Response:
    updated = _finish_proposal_status(project, current, "stale")
    return JSONResponse(
        {"error": message, "currentStatus": "stale", **updated}, status_code=409,
    )


def _ops_digest(ops: object) -> str:
    return hashlib.sha256(
        json.dumps(ops, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_acceptance_journal(current: dict, journal_raw: object) -> dict:
    """Validate the complete pre-write journal and bind it to immutable proposal inputs."""
    if not isinstance(journal_raw, dict):
        raise ValueError("proposal acceptance journal is malformed")
    journal = journal_raw
    required = {"baseRevision", "targetRevision", "projectDigest", "opsDigest", "reason", "steps"}
    # Journals interrupted on earlier versions also carry receiptDigest, which no longer gates anything.
    if set(journal) - {"receiptDigest"} != required:
        raise ValueError("proposal acceptance journal schema is invalid")
    base_revision = journal.get("baseRevision")
    target_revision = journal.get("targetRevision")
    if (not isinstance(base_revision, int) or isinstance(base_revision, bool)
            or not isinstance(target_revision, int) or isinstance(target_revision, bool)
            or target_revision != base_revision + 1
            or current.get("baseRevision") != base_revision):
        raise ValueError("proposal acceptance journal revision is invalid")
    project_digest = journal.get("projectDigest")
    ops_digest = journal.get("opsDigest")
    if (not isinstance(project_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", project_digest)
            or not isinstance(ops_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", ops_digest)
            or ops_digest != _ops_digest(current.get("ops"))):
        raise ValueError("proposal acceptance journal target is invalid")
    reason = journal.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("proposal acceptance journal reason is invalid")
    steps = journal.get("steps")
    if not isinstance(steps, dict) or any(step not in _ACCEPTANCE_STEPS or not isinstance(done, bool)
                                          for step, done in steps.items()):
        raise ValueError("proposal acceptance journal steps are invalid")
    return journal


def _reconcile_accepted_proposal(project: str, current: dict) -> tuple[dict, dict, int]:
    """Finish a journaled acceptance exactly once after its project write is durable."""
    acceptance = _validate_acceptance_journal(current, current.get("acceptance"))
    target_revision = acceptance.get("targetRevision")
    target_digest = acceptance.get("projectDigest")
    if not isinstance(target_revision, int) or isinstance(target_revision, bool) or not isinstance(target_digest, str):
        raise ValueError("proposal acceptance journal is malformed")
    project_doc, revision = helpers._read_project_doc(project)
    if project_doc is None:
        raise ValueError("accepted proposal project does not match its journal")
    if revision != target_revision or helpers._project_digest(project_doc) != target_digest:
        raise ValueError("accepted proposal project does not match its journal")
    pid = str(current["id"])
    steps_raw = acceptance.get("steps")
    steps: dict[str, object] = steps_raw if isinstance(steps_raw, dict) else {}
    reason_raw = acceptance.get("reason")
    reason: str | None = reason_raw if isinstance(reason_raw, str) else None
    title = str(current.get("title") or "")
    if not steps.get("projectEvent"):
        if proposals_mod.mark_acceptance_step(project, pid, "projectEvent") is None:
            raise ValueError("proposal disappeared while journaling project event")
        try:
            state.hub.publish(project, revision)
        except Exception:  # noqa: BLE001 — SSE is a best-effort hint; GET is authoritative.
            pass
        steps["projectEvent"] = True
    if not steps.get("status"):
        if current.get("status") != "accepted":
            updated = proposals_mod.set_status(project, pid, "accepted")
            if updated is None:
                raise ValueError("proposal disappeared while accepting")
        if proposals_mod.mark_acceptance_step(project, pid, "status") is None:
            raise ValueError("proposal disappeared while journaling status")
        steps["status"] = True
    if not steps.get("notes"):
        body_note = str(current.get("rationale") or "(no rationale)")
        if reason:
            body_note = f"{body_note}\n\n({reason})"
        notes_mod.append_once(project, title, body_note, f"proposal:{pid}:accepted")
        if proposals_mod.mark_acceptance_step(project, pid, "notes") is None:
            raise ValueError("proposal disappeared while journaling notes")
        steps["notes"] = True
    if not steps.get("decision"):
        groups = current.get("intentGroups") or []
        operation_ids = [str(group.get("id")) for group in groups
                         if isinstance(group, dict) and group.get("id")]
        citations = current.get("citations") or []
        evidence_ids = [str(citation.get("evidenceId")) for citation in citations
                        if isinstance(citation, dict) and citation.get("evidenceId")]
        decisions_mod.record_once(
            project, decision="accepted", intent=title,
            rationale=reason or str(current.get("rationale") or "") or None,
            proposal_id=pid, operation_ids=operation_ids, evidence_ids=evidence_ids,
            channel=project_doc.get("channel"),
        )
        if proposals_mod.mark_acceptance_step(project, pid, "decision") is None:
            raise ValueError("proposal disappeared while journaling decision")
        steps["decision"] = True
    if not steps.get("feedback"):
        host = _existing_chat_host()
        if host is not None:
            host.push_context(project, f"System: the user accepted your proposal {pid} ({title}).")  # type: ignore[attr-defined]
        if proposals_mod.mark_acceptance_step(project, pid, "feedback") is None:
            raise ValueError("proposal disappeared while journaling feedback")
        steps["feedback"] = True
    if not steps.get("event"):
        event = {
            "type": "proposal", "id": pid, "status": "accepted",
        }
        if proposals_mod.mark_acceptance_step(project, pid, "event") is None:
            raise ValueError("proposal disappeared while journaling event")
        try:
            state.hub.publish_event(project, event)
        except Exception:  # noqa: BLE001 — SSE is a best-effort hint; GET is authoritative.
            pass
    updated = proposals_mod.read(project, pid)
    if updated is None:
        raise ValueError("proposal disappeared after acceptance")
    return updated, project_doc, revision


@router.post("/api/proposal/{p:path}/{pid}/accept")
async def api_proposal_accept(p: str, pid: str, request: Request) -> Response:
    """Atomically guard, apply, and settle a pending proposal.

    The base revision is checked before the project write. The event loop has no await between
    that check and the single write, so a stale proposal cannot mutate the video.
    """
    import jsonpatch
    import jsonpointer

    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=400)
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    reason = body.get("reason")
    if reason is not None and not isinstance(reason, str):
        return JSONResponse({"error": "reason must be a string"}, status_code=400)
    if isinstance(reason, str):
        reason = agent_credentials.redact(reason)
    try:
        current = proposals_mod.read(project, pid)
    except ValueError:
        return JSONResponse({"error": "bad proposal id"}, status_code=400)
    if current is None:
        return JSONResponse({"error": "no such proposal"}, status_code=404)
    if current.get("status") == "accepted" and isinstance(current.get("acceptance"), dict):
        try:
            updated, project_doc, revision = _reconcile_accepted_proposal(project, current)
        except ValueError as e:
            return JSONResponse({"error": public_error(e, 500)}, status_code=500)
        return JSONResponse({
            "ok": True, "project": project_doc, "revision": revision, "proposal": updated,
        })
    if current.get("status") != "pending":
        return JSONResponse(
            {"error": f"proposal already {current.get('status')}", "currentStatus": current.get("status")},
            status_code=409,
        )
    on_disk_doc, on_disk = helpers._read_project_doc(project)
    journal_raw = current.get("acceptance")
    if journal_raw is not None:
        try:
            journal = _validate_acceptance_journal(current, journal_raw)
        except ValueError as e:
            return JSONResponse({"error": public_error(e, 409), "currentStatus": "pending"}, status_code=409)
    else:
        journal = None
    if journal is not None:
        target_revision = journal.get("targetRevision")
        target_digest = journal.get("projectDigest")
        if (isinstance(target_revision, int) and not isinstance(target_revision, bool)
                and isinstance(target_digest, str) and on_disk_doc is not None
                and on_disk == target_revision and helpers._project_digest(on_disk_doc) == target_digest):
            try:
                updated, project_doc, revision = _reconcile_accepted_proposal(project, current)
            except ValueError as e:
                return JSONResponse({"error": public_error(e, 500)}, status_code=500)
            return JSONResponse({
                "ok": True, "project": project_doc, "revision": revision, "proposal": updated,
            })
        if current.get("baseRevision") != on_disk:
            return _stale_proposal(project, current, "proposal base revision is stale")
        effective_reason = journal.get("reason") if isinstance(journal.get("reason"), str) else None
    else:
        if current.get("baseRevision") != on_disk:
            return _stale_proposal(project, current, "proposal base revision is stale")
        effective_reason = reason
    try:
        base = project_mod.from_dict(on_disk_doc) if on_disk_doc is not None else helpers._skeleton(project)
        proj = project_mod.apply_ops(base, current.get("ops") or [])
    except (ValidationError, KeyError, TypeError, ValueError,
            jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
        return _stale_proposal(project, current, public_error(e, 409))
    proj.revision = on_disk + 1
    proj.name = project
    try:
        project_mod.validate(proj)
    except ValidationError as e:
        return _stale_proposal(project, current, public_error(e, 409))
    expected_doc = project_mod.to_dict(proj)
    expected_digest = helpers._project_digest(expected_doc)
    if journal is None:
        journal = {
            "baseRevision": on_disk, "targetRevision": proj.revision, "projectDigest": expected_digest,
            "opsDigest": _ops_digest(current.get("ops")),
            "reason": effective_reason, "steps": {},
        }
        try:
            _validate_acceptance_journal(current, journal)
        except ValueError as e:
            return JSONResponse({"error": public_error(e, 409), "currentStatus": "pending"}, status_code=409)
        try:
            current = proposals_mod.begin_accept(project, pid, journal) or current
        except ValueError as e:
            return JSONResponse({"error": public_error(e, 409)}, status_code=409)
        journal = _validate_acceptance_journal(current, current.get("acceptance"))
    if journal.get("targetRevision") != proj.revision or journal.get("projectDigest") != expected_digest:
        return JSONResponse({"error": "proposal acceptance journal target conflicts", "currentStatus": "pending"}, status_code=409)
    with project_mod.write_lock():
        project_mod.write_project(helpers._project_path(project), proj)
    try:
        updated, project_doc, revision = _reconcile_accepted_proposal(project, current)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 500)}, status_code=500)
    return JSONResponse({
        "ok": True, "project": project_doc, "revision": revision, "proposal": updated,
    })


@router.post("/api/proposal/{p:path}/{pid}/status")
async def api_proposal_status(p: str, pid: str, request: Request) -> Response:
    """Flip a pending proposal to accepted/rejected/stale (terminal states never re-open — the double-
    accept / idempotency guard). On accept, append the NOTES 'what I did & why' entry; on reject,
    log the reason to NOTES and queue it as a durable system note for the agent's next turn. Emits a
    `proposal` status event so every open tab greys the card."""
    project = helpers._safe(p.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=400)
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    status = body.get("status")
    if status not in _PROPOSAL_TRANSITIONS:
        return JSONResponse({"error": "bad status"}, status_code=400)
    reason = body.get("reason")
    try:
        current = proposals_mod.read(project, pid)
    except ValueError:
        return JSONResponse({"error": "bad proposal id"}, status_code=400)
    if current is None:
        return JSONResponse({"error": "no such proposal"}, status_code=404)
    if status == "accepted" and current.get("acceptance") is not None:
        return JSONResponse({"error": "journaled proposals must use atomic accept", "currentStatus": current.get("status")}, status_code=409)
    if current.get("status") != "pending":
        # Terminal → any further flip is a no-op conflict (the agent regenerates a NEW id instead).
        return JSONResponse(
            {"error": f"proposal already {current.get('status')}", "currentStatus": current.get("status")},
            status_code=409,
        )
    # Keep this legacy terminal-status route for integrations that settle status separately.  The UI
    # uses /accept so project mutation and status settlement are one guarded operation.
    try:
        updated = _finish_proposal_status(project, current, status, reason)
    except ValueError:
        return JSONResponse({"error": "proposal disappeared", "currentStatus": "unknown"}, status_code=409)
    return JSONResponse(updated)
