"""Local-only decision-event persistence and privacy-bounded preference summaries."""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import config
from pipeline import agent_credentials
from pipeline.errors import ValidationError
from pipeline.names import check_case_collision, exact_path, existing_name, safe_name, validate_name

CURRENT_SCHEMA_VERSION = 1
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_DECISIONS = {"accepted", "rejected", "modified", "manual"}
_SCOPES = {"project", "channel"}


@dataclass
class DecisionEvent:
    schemaVersion: int
    documentType: Literal["decisionEvent"]
    eventId: str
    projectId: str
    decision: str                       # accepted | rejected | modified | manual
    intent: str
    occurredAt: str                     # RFC 3339 timestamp with timezone
    preferenceScope: str                # project | channel
    rationale: str | None = None
    proposalId: str | None = None
    operationIds: list[str] = field(default_factory=list)
    evidenceIds: list[str] = field(default_factory=list)


def _nonempty_string(value: object, label: str) -> None:
    if not (isinstance(value, str) and value.strip()):
        raise ValidationError(f"{label} must be a non-empty string")


def _identifier(value: object, label: str) -> None:
    if not (isinstance(value, str) and safe_name(value) and _ID_RE.fullmatch(value)):
        raise ValidationError(f"{label} must be a portable identifier")


def _string_list(values: object, label: str) -> None:
    if not isinstance(values, list):
        raise ValidationError(f"{label} must be a list")
    for i, value in enumerate(values):
        _nonempty_string(value, f"{label}[{i}]")


def validate(document: DecisionEvent) -> None:
    if not (document.schemaVersion == CURRENT_SCHEMA_VERSION):
        raise ValidationError("unsupported decision schemaVersion")
    if not (document.documentType == "decisionEvent"):
        raise ValidationError()
    _identifier(document.eventId, "eventId")
    if existing_name(document.projectId) is None:
        raise ValidationError("projectId must be a portable name")
    if document.decision not in _DECISIONS:
        raise ValidationError(f"decision must be one of {_DECISIONS}")
    _nonempty_string(document.intent, "intent")
    if not (isinstance(document.occurredAt, str) and _RFC3339_RE.fullmatch(document.occurredAt)):
        raise ValidationError("occurredAt must be an RFC 3339 timestamp with timezone")
    if document.preferenceScope not in _SCOPES:
        raise ValidationError(f"preferenceScope must be one of {_SCOPES}")
    if document.rationale is not None:
        _nonempty_string(document.rationale, "rationale")
    if document.proposalId is not None:
        _identifier(document.proposalId, "proposalId")
    _string_list(document.operationIds, "operationIds")
    _string_list(document.evidenceIds, "evidenceIds")


def to_dict(document: DecisionEvent) -> dict[str, Any]:
    """Deterministic JSON-ready representation, omitting optional ``None`` fields.

    Omitting rather than serializing null keeps producer output compact and lets new optional
    fields remain additive.  Required fields, including ``schemaVersion`` and ``documentType``,
    are always present.
    """
    def _omit_none(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: _omit_none(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [_omit_none(item) for item in value]
        return value

    return _omit_none(asdict(document))


def _load(payload: dict) -> DecisionEvent:
    if not isinstance(payload, dict):
        raise ValidationError("decision must be an object")
    version = payload.get("schemaVersion", 0)
    if not (isinstance(version, int) and not isinstance(version, bool) and 0 <= version <= CURRENT_SCHEMA_VERSION):
        raise ValidationError("unsupported decision schemaVersion")
    document_type = payload.get("documentType", payload.get("type") if version == 0 else None)
    if not (document_type == "decisionEvent"):
        raise ValidationError("not a decision event")
    event = DecisionEvent(
        schemaVersion=CURRENT_SCHEMA_VERSION, documentType="decisionEvent", eventId=payload["eventId"],
        projectId=payload["projectId"], decision=payload["decision"], intent=payload["intent"],
        occurredAt=payload["occurredAt"], preferenceScope=payload["preferenceScope"],
        rationale=payload.get("rationale"), proposalId=payload.get("proposalId"),
        operationIds=payload.get("operationIds", []), evidenceIds=payload.get("evidenceIds", []),
    )
    validate(event)
    return event


def _path(project: str, event_id: str, *, scope: str, channel: str | None) -> Path:
    validate_name(event_id)
    directory = _scope_dir(project, scope=scope, channel=channel)
    if not directory.exists():
        check_case_collision(directory)
    path = directory / f"{event_id}.json"
    check_case_collision(path)
    return path


def _scope_dir(project: str, *, scope: str, channel: str | None) -> Path:
    if existing_name(project) is None:
        raise ValidationError("invalid project name")
    if scope not in {"project", "channel"}:
        raise ValidationError("scope must be project or channel")
    selected = project
    if scope == "channel":
        if existing_name(channel) is None:
            raise ValidationError("invalid channel name")
        selected = str(channel)
    root = config.DECISIONS_DIR.resolve()
    path = config.DECISIONS_DIR / ("channels" if scope == "channel" else "projects") / selected
    resolved = path.resolve()
    if root not in resolved.parents:
        raise ValidationError("decision scope must stay in its storage directory")
    exact_path(path, root, resolved=resolved)
    return path


def _redact_text(event: DecisionEvent) -> DecisionEvent:
    return replace(event, intent=agent_credentials.redact(event.intent),
                   rationale=agent_credentials.redact(event.rationale) if event.rationale is not None else None)


def write(event: DecisionEvent, *, channel: str | None = None) -> Path:
    """Atomically persist one validated event; all records remain on the local studio filesystem."""
    validate(event)
    if event.preferenceScope == "channel":
        if not (isinstance(channel, str) and channel):
            raise ValidationError("channel scope requires a channel")
    path = _path(event.projectId, event.eventId, scope=event.preferenceScope, channel=channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(to_dict(_redact_text(event)), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def record(
    project: str, *, decision: str, intent: str, scope: str = "project", rationale: str | None = None,
    proposal_id: str | None = None, operation_ids: list[str] | None = None, evidence_ids: list[str] | None = None,
    channel: str | None = None,
) -> DecisionEvent:
    """Create and persist a decision event with only local editorial metadata."""
    event = DecisionEvent(
        CURRENT_SCHEMA_VERSION, "decisionEvent", uuid.uuid4().hex, project, decision, intent,
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), scope, rationale, proposal_id,
        operation_ids or [], evidence_ids or [],
    )
    event = _redact_text(event)
    write(event, channel=channel)
    return event


def record_once(
    project: str, *, decision: str, intent: str, rationale: str | None = None,
    proposal_id: str | None = None, operation_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None, channel: str | None = None,
) -> DecisionEvent:
    """Record a proposal decision once so acceptance retries cannot duplicate it."""
    if proposal_id:
        for event in list_for(project, scope="project"):
            if event.proposalId == proposal_id and event.decision == decision:
                return event
    return record(
        project, decision=decision, intent=intent, rationale=rationale, proposal_id=proposal_id,
        operation_ids=operation_ids, evidence_ids=evidence_ids, channel=channel,
    )


def list_for(project: str, *, scope: str, channel: str | None = None) -> list[DecisionEvent]:
    """Read only the requested project or channel scope; never return media bytes or transcripts."""
    root = _scope_dir(project, scope=scope, channel=channel)
    out: list[DecisionEvent] = []
    for path in root.glob("**/*.json") if root.exists() else []:
        if root.resolve() not in path.resolve().parents:
            continue
        try:
            event = _load(json.loads(path.read_text(encoding="utf-8")))
            if event.preferenceScope != scope:
                continue
            out.append(event)
        except (ValidationError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda event: event.occurredAt, reverse=True)


def summary(project: str, *, scope: str, channel: str | None = None, limit: int = 12) -> dict:
    """Compact agent-facing preferences: editorial intent/outcome only, never raw footage or text."""
    events = list_for(project, scope=scope, channel=channel)[:limit]
    return {"scope": scope, "project": project, "channel": channel if scope == "channel" else None,
            "events": [{"decision": event.decision, "intent": event.intent, "rationale": event.rationale,
                        "occurredAt": event.occurredAt} for event in events]}
