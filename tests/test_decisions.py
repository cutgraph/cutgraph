"""local decision-event persistence and preference-query tests."""
from __future__ import annotations

import json

import pytest

from pipeline.errors import ValidationError

import config
from pipeline import decisions


def test_record_scopes_and_summarizes_without_media(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    decisions.record("planting-seedling", decision="accepted", intent="tighten opener", rationale="keep cue", proposal_id="p1")
    decisions.record("planting-seedling", decision="manual", intent="keep full step", scope="channel", channel="gardening")
    project = decisions.summary("planting-seedling", scope="project")
    channel = decisions.summary("planting-seedling", scope="channel", channel="gardening")
    assert project["events"][0]["intent"] == "tighten opener"
    assert channel["events"][0]["decision"] == "manual"
    assert "media" not in str(project) and "transcript" not in str(channel)


def test_channel_scope_requires_channel(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    try:
        decisions.record("planting-seedling", decision="manual", intent="keep cue", scope="channel")
    except ValidationError as exc:
        assert "channel" in str(exc)
    else:
        raise AssertionError("channel-scoped decision accepted without channel")


def test_decision_roundtrip_preserves_proposal_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    event = decisions.record(
        "video", decision="accepted", intent="tighten opener", proposal_id="proposal",
        operation_ids=["trim"], evidence_ids=["source-span"],
    )
    assert decisions.list_for("video", scope="project") == [event]
    payload = json.loads((config.DECISIONS_DIR / "projects" / "video" / f"{event.eventId}.json").read_text())
    assert payload["operationIds"] == ["trim"] and payload["evidenceIds"] == ["source-span"]
    assert "rationale" not in payload


def test_legacy_decision_remains_readable_without_rewriting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    event = decisions.record("video", decision="rejected", intent="keep the opening")
    payload = decisions.to_dict(event)
    payload.pop("schemaVersion")
    payload["type"] = payload.pop("documentType")
    path = config.DECISIONS_DIR / "projects" / "video" / f"{event.eventId}.json"
    legacy = json.dumps(payload)
    path.write_text(legacy, encoding="utf-8")
    assert decisions.list_for("video", scope="project") == [event]
    assert path.read_text(encoding="utf-8") == legacy


@pytest.mark.parametrize("changes", [
    {"schemaVersion": 2}, {"schemaVersion": True}, {"documentType": "other"},
    {"eventId": "../outside"}, {"decision": "unknown"}, {"intent": ""},
    {"occurredAt": "2026-01-01"}, {"preferenceScope": "other"}, {"operationIds": [""]},
])
def test_invalid_decision_sidecars_are_ignored(tmp_path, monkeypatch, changes) -> None:
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    event = decisions.record("video", decision="accepted", intent="tighten opener")
    payload = {**decisions.to_dict(event), **changes}
    path = config.DECISIONS_DIR / "projects" / "video" / f"{event.eventId}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert decisions.list_for("video", scope="project") == []
