"""Proposal store + the RFC-6902 test-guard primitive and revision-gated acceptance.

Covers: the id-guard mechanism (a passing `test` applies, a wrong one raises JsonPatchTestFailed);
proposals.write/read/list_all/set_status round-tripping atomically; path-safety rejecting a bad id
before any write; and the accept-flow server half via httpx.ASGITransport — trivially-safe rebase
across a benign bump, stale on an index-shifting write, reject + NOTES, and the double-accept guard.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Iterator
from typing import TypeVar

import httpx
import jsonpatch
import pytest

from pipeline.errors import ValidationError

import config
from pipeline import decisions, notes, proposals
from pipeline import project as project_mod
from scripts import studio_app
from studio import helpers
from studio import state as studio_state
from studio.routes import chat

T = TypeVar("T")

_PREFIX = "zzprop"


def _doc() -> dict:
    return {
        "schemaVersion": 1, "name": "unit", "fps": 30, "width": 1920, "height": 1080, "revision": 0,
        "transcript": None,
        "tracks": [{"id": "t1", "kind": "speech", "clips": [
            {"id": "c1", "media": {"source": "data/source/a.mp4", "proxy": None},
             "srcInF": 0, "srcOutF": 30, "tlStartF": 0, "gainDb": 0.0},
        ]}],
    }


def _doc2() -> dict:
    """Two non-overlapping speech clips (c1 @0, c2 @30) — for the accept-flow rebase/index-shift."""
    d = _doc()
    d["tracks"][0]["clips"].append(
        {"id": "c2", "media": {"source": "data/source/a.mp4", "proxy": None},
         "srcInF": 0, "srcOutF": 30, "tlStartF": 30, "gainDb": 0.0})
    return d


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=studio_app.app)
    return httpx.AsyncClient(transport=transport, base_url="http://studio.test")


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    def _wipe() -> None:
        if config.PROPOSALS_DIR.exists():
            for d in config.PROPOSALS_DIR.glob(f"{_PREFIX}*"):
                for f in d.glob("*"):
                    f.unlink(missing_ok=True)
                d.rmdir()
        for base, pat in ((config.PROJECTS_DIR, f"{_PREFIX}*.json"),
                          (config.NOTES_DIR, f"{_PREFIX}*.md"),
                          (config.CHAT_DIR, f"{_PREFIX}*.jsonl")):
            if base.exists():
                for f in base.glob(pat):
                    f.unlink(missing_ok=True)
    _wipe()
    yield
    _wipe()


# ── the test-guard primitive (the whole rebase-safety mechanism) ──────────────────
def test_id_guard_passes_and_applies() -> None:
    proj = project_mod.from_dict(_doc())
    out = project_mod.apply_ops(proj, [
        {"op": "test", "path": "/tracks/0/clips/0/id", "value": "c1"},
        {"op": "replace", "path": "/tracks/0/clips/0/srcOutF", "value": 60},
    ])
    assert out.tracks[0].clips[0].srcOutF == 60


def test_id_guard_wrong_value_raises() -> None:
    proj = project_mod.from_dict(_doc())
    with pytest.raises(jsonpatch.JsonPatchTestFailed):
        project_mod.apply_ops(proj, [
            {"op": "test", "path": "/tracks/0/clips/0/id", "value": "WRONG"},
            {"op": "replace", "path": "/tracks/0/clips/0/srcOutF", "value": 60},
        ])


def _doc5() -> dict:
    """Five speech clips c0..c4 for the index-shift-MISAPPLY scenario: removing an earlier clip leaves
    index 3 VALID but pointing at a different clip, so a bare pointer misapplies where the guard fails."""
    d = _doc()
    d["tracks"][0]["clips"] = [
        {"id": f"c{i}", "media": {"source": "data/source/a.mp4", "proxy": None},
         "srcInF": 0, "srcOutF": 30, "tlStartF": i * 30, "gainDb": 0.0}
        for i in range(5)
    ]
    return d


# ── server-side identity-guard generation + enforcement ───────────────
def test_guard_ops_prepends_identity_guard_from_current_doc() -> None:
    """The store derives the identity guard the LLM omits — it anchors on the deepest id-bearing element
    on the op's path, catching an index shift a bare pointer would silently misapply."""
    guarded = proposals.guard_ops(
        [{"op": "remove", "path": "/tracks/0/clips/3"}], _doc5())
    assert guarded[0] == {"op": "test", "path": "/tracks/0/clips/3/id", "value": "c3"}
    assert guarded[1] == {"op": "remove", "path": "/tracks/0/clips/3"}


def test_guard_ops_noop_without_doc() -> None:
    ops = [{"op": "replace", "path": "/width", "value": 1280}]
    assert proposals.guard_ops(ops, None) == ops


def test_write_stores_guarded_ops_and_event_carries_them() -> None:
    """a written proposal's stored + emitted ops are NON-EMPTY and guarded, so a live-session accept
    PATCHes the real ops (the bug: the event omitted ops → accept sent []) and fails closed if stale."""
    slug = f"{_PREFIX}_ev"
    p = proposals.write(slug, ops=[{"op": "remove", "path": "/tracks/0/clips/3"}],
                        title="Cut clip", rationale="filler", base_revision=5, doc=_doc5())
    assert p["ops"][0]["op"] == "test"                       # guard prepended, stored
    assert p["ops"][-1] == {"op": "remove", "path": "/tracks/0/clips/3"}
    ev = proposals.proposal_event(p)
    assert ev["type"] == "proposal" and ev["status"] == "pending"
    assert ev["ops"] == p["ops"]                             # the event carries the REAL guarded ops
    assert ev["ops"]                                          # NON-EMPTY (regression guard)
    assert ev["baseRevision"] == p["baseRevision"]


def test_generated_guard_makes_index_shift_stale_end_to_end() -> None:
    """The agent omits a guard; the store adds one; an intervening remove shifts the
    target so accept fails closed → 400 (marked stale), NOT a silent lossy misapply of the wrong clip."""
    name = f"{_PREFIX}_g2e"
    slug = f"{_PREFIX}_g2e"
    # Agent proposes removing clips/3 (c3) with NO test guard — the store guards it against c3's id.
    pid = proposals.write(slug, ops=[{"op": "remove", "path": "/tracks/0/clips/3"}],
                          title="Cut c3", rationale="r", base_revision=1, doc=_doc5())["id"]
    guarded = proposals.read(slug, pid)["ops"]  # type: ignore[index]

    async def scenario() -> int:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc5(), "baseRevision": 0})  # rev1
            # Human removes clips/1 (c1): clips/3 is now c4 — a DIFFERENT, VALID clip. A raw
            # remove /tracks/0/clips/3 would succeed and delete the wrong clip; the id-guard must catch it.
            await client.patch(
                f"/api/project/{name}",
                json={"ops": [{"op": "remove", "path": "/tracks/0/clips/1"}], "baseRevision": 1})  # rev2
            env = (await client.get(f"/api/project/{name}")).json()
            accept = await client.patch(
                f"/api/project/{name}", json={"ops": guarded, "baseRevision": env["revision"]})
            return accept.status_code

    assert _run(scenario()) == 400  # guard fails closed → stale, not misapplied


# ── write / read / list / set_status round-trip ───────────────────────────────────
def test_write_read_list_roundtrip() -> None:
    slug = f"{_PREFIX}_rt"
    ops = [{"op": "test", "path": "/tracks/0/clips/0/id", "value": "c1"},
           {"op": "replace", "path": "/tracks/0/clips/0/srcOutF", "value": 60}]
    p = proposals.write(slug, ops=ops, title="Tighten", rationale="filler", base_revision=3, saves_sec=6.2)
    assert p["status"] == "pending"
    assert p["baseRevision"] == 3
    assert p["savesSec"] == 6.2
    assert "createdAt" in p

    got = proposals.read(slug, p["id"])
    assert got == p
    assert [q["id"] for q in proposals.list_all(slug)] == [p["id"]]


def test_major_proposal_persists_intent_groups_citations_and_preview() -> None:
    slug = f"{_PREFIX}_grounded"
    p = proposals.write(
        slug, ops=[], title="Tighten opener", rationale="keep the claim crisp", base_revision=3,
        intent_groups=[{"id": "trim-intro", "intent": "remove the slow lead-in", "ops": [
            {"op": "replace", "path": "/tracks/0/clips/0/srcInF", "value": 8},
        ]}],
        citations=[
            {"kind": "transcript", "start": 0.0, "end": 1.2, "summary": "opening claim"},
        ],
        material=True, preview={"startF": 0, "endF": 90, "previewRef": "/media/preview/abc123.mp4"},
        require_evidence=True,
    )
    assert p["ops"][-1]["path"] == "/tracks/0/clips/0/srcInF"
    assert p["intentGroups"][0]["id"] == "trim-intro"
    assert {c["kind"] for c in p["citations"]} == {"transcript"}
    assert p["preview"]["previewRef"] == "/media/preview/abc123.mp4"
    event = proposals.proposal_event(p)
    assert event["intentGroups"] == p["intentGroups"] and event["citations"] == p["citations"]


def test_major_proposal_requires_transcript_citations() -> None:
    def write(citations: object) -> dict:
        return proposals.write(
            f"{_PREFIX}_cites", ops=[], title="Tighten opener", rationale="r", base_revision=1,
            intent_groups=[{"id": "trim", "intent": "trim", "ops": [{"op": "replace", "path": "/width", "value": 1280}]}],
            citations=citations, require_evidence=True,
        )

    with pytest.raises(ValidationError, match="require evidence citations"):
        write(None)
    with pytest.raises(ValidationError, match="require evidence citations"):
        write([])
    with pytest.raises(ValidationError, match="kind must be transcript"):
        write([{"kind": "visual", "start": 0, "end": 900, "summary": "a frame", "evidenceId": "ev1"}])
    with pytest.raises(ValidationError, match="end must be after start"):
        write([{"kind": "transcript", "start": 2.0, "end": 1.0, "summary": "backwards"}])
    assert write([{"kind": "transcript", "start": 0.0, "end": 1.2, "summary": "opening claim"}])["citations"]


def test_major_proposal_rejects_missing_preview() -> None:
    with pytest.raises(ValidationError, match="compile preview"):
        proposals.write(
            f"{_PREFIX}_badpreview", ops=[{"op": "replace", "path": "/width", "value": 1280}],
            title="Change", rationale="r", base_revision=1, material=True,
        )


def test_set_status_atomic_rmw() -> None:
    slug = f"{_PREFIX}_ss"
    p = proposals.write(slug, ops=[], title="t", rationale="r", base_revision=1)
    updated = proposals.set_status(slug, p["id"], "rejected", reason="not now")
    assert updated is not None
    assert updated["status"] == "rejected"
    assert updated["rejectedReason"] == "not now"
    reread = proposals.read(slug, p["id"])
    assert reread is not None and reread["status"] == "rejected"


def test_set_status_missing_returns_none() -> None:
    slug = f"{_PREFIX}_missing"
    assert proposals.set_status(slug, "deadbeef", "accepted") is None


def test_bad_id_rejected_before_write() -> None:
    slug = f"{_PREFIX}_bad"
    for bad in ("..", "a/b", "c:evil", "x" * 201):
        with pytest.raises(ValueError):
            proposals.read(slug, bad)


def test_bad_slug_rejected() -> None:
    with pytest.raises(ValueError):
        proposals.write("../evil", ops=[], title="t", rationale="r", base_revision=0)


def test_invalid_status_rejected() -> None:
    slug = f"{_PREFIX}_inv"
    p = proposals.write(slug, ops=[], title="t", rationale="r", base_revision=0)
    with pytest.raises(ValueError):
        proposals.set_status(slug, p["id"], "bogus")


# ── accept-flow server half (httpx.ASGITransport; harness = test_revision_gate.py) ──────────────
def test_trivially_safe_rebase_across_benign_bump() -> None:
    """A benign intervening write (/width) bumps the revision; the accept PATCH re-GETs the CURRENT
    revision and the id-guarded ops still apply cleanly → 200, mutation present. Proves re-anchoring at
    the current revision, never replaying the proposal's stale baseRevision."""
    name = f"{_PREFIX}_rebase"
    ops = [
        {"op": "test", "path": "/tracks/0/clips/1/id", "value": "c2"},
        {"op": "replace", "path": "/tracks/0/clips/1/srcOutF", "value": 20},
    ]

    async def scenario() -> httpx.Response:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc2(), "baseRevision": 0})  # rev1
            await client.patch(  # benign bump → rev2
                f"/api/project/{name}",
                json={"ops": [{"op": "replace", "path": "/width", "value": 1280}], "baseRevision": 1})
            # accept: re-GET current revision (2), PATCH the id-guarded ops.
            env = (await client.get(f"/api/project/{name}")).json()
            return await client.patch(
                f"/api/project/{name}", json={"ops": ops, "baseRevision": env["revision"]})

    r = _run(scenario())
    assert r.status_code == 200
    body = r.json()
    assert body["revision"] == 3
    assert body["project"]["tracks"][0]["clips"][1]["srcOutF"] == 20


def test_index_shifting_write_makes_proposal_stale() -> None:
    """An intervening write removes an earlier clip, shifting indices so the id-guarded path no
    longer resolves to c2. The accept PATCH raises inside apply_ops → 400; the UI flips the card to
    stale. The stale proposal never edits the wrong clip."""
    name = f"{_PREFIX}_shift"
    slug = f"{_PREFIX}_shift"
    ops = [
        {"op": "test", "path": "/tracks/0/clips/1/id", "value": "c2"},
        {"op": "replace", "path": "/tracks/0/clips/1/srcOutF", "value": 20},
    ]
    pid = proposals.write(slug, ops=ops, title="Trim c2", rationale="r", base_revision=1)["id"]

    async def scenario() -> tuple[int, httpx.Response]:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc2(), "baseRevision": 0})  # rev1
            # remove clips/0 (c1) → index 1 no longer holds c2 (now out of range / different).
            await client.patch(
                f"/api/project/{name}",
                json={"ops": [{"op": "remove", "path": "/tracks/0/clips/0"}], "baseRevision": 1})  # rev2
            env = (await client.get(f"/api/project/{name}")).json()
            accept = await client.patch(
                f"/api/project/{name}", json={"ops": ops, "baseRevision": env["revision"]})
            return accept.status_code, accept

    code, _ = _run(scenario())
    assert code == 400  # guard/pointer failure → the accept PATCH is rejected, not misapplied
    # The UI marks the card stale after the 400 (client); prove that flip lands + persists.
    updated = proposals.set_status(slug, pid, "stale")
    assert updated is not None and updated["status"] == "stale"
    assert proposals.read(slug, pid)["status"] == "stale"  # type: ignore[index]


def test_reject_flips_status_logs_notes_and_leaves_revision() -> None:
    """Reject → status rejected + reason recorded, a NOTES entry appended, the project revision
    unchanged (reject never touches project.json)."""
    name = f"{_PREFIX}_reject"
    slug = f"{_PREFIX}_reject"
    pid = proposals.write(slug, ops=[], title="Cut the pause", rationale="r", base_revision=1)["id"]

    async def scenario() -> tuple[httpx.Response, int]:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})  # rev1
            resp = await client.post(
                f"/api/proposal/{name}/{pid}/status", json={"status": "rejected", "reason": "keep the pause"})
            rev = (await client.get(f"/api/project/{name}")).json()["revision"]
            return resp, rev

    resp, rev = _run(scenario())
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "rejected"
    assert payload["rejectedReason"] == "keep the pause"
    assert rev == 1  # project untouched by a reject
    assert "keep the pause" in notes.read(slug)


def test_double_accept_blocked_by_status_guard() -> None:
    """A terminal proposal never re-opens: the second accept flip is a 409 conflict — one accepted
    proposal = one checkpoint, no second revision bump."""
    name = f"{_PREFIX}_double"
    slug = f"{_PREFIX}_double"
    pid = proposals.write(slug, ops=[], title="Once", rationale="r", base_revision=1)["id"]

    async def scenario() -> tuple[int, int]:
        async with _client() as client:
            first = await client.post(f"/api/proposal/{name}/{pid}/status", json={"status": "accepted"})
            second = await client.post(f"/api/proposal/{name}/{pid}/status", json={"status": "accepted"})
            return first.status_code, second.status_code

    first, second = _run(scenario())
    assert first == 200
    assert second == 409
    assert proposals.read(slug, pid)["status"] == "accepted"  # type: ignore[index]


def test_atomic_accept_applies_once_and_settles_after_revision_increment(tmp_path, monkeypatch) -> None:
    """Acceptance applies once when the guarded write advances the revision."""
    name = f"{_PREFIX}_atomic"
    pid = proposals.write(
        name, ops=[{"op": "replace", "path": "/width", "value": 1280}], title="Resize",
        rationale="fit the channel", base_revision=1, doc=_doc(),
    )["id"]

    async def scenario() -> tuple[httpx.Response, httpx.Response, dict, dict]:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            first = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
            second = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
            project = (await client.get(f"/api/project/{name}")).json()
            proposal = proposals.read(name, pid) or {}
            return first, second, project, proposal

    first, second, project, proposal = _run(scenario())
    assert first.status_code == 200
    assert first.json()["revision"] == 2
    assert project["revision"] == 2 and project["project"]["width"] == 1280
    assert second.status_code == 200  # retry reconciles the already-journaled acceptance
    assert proposal["status"] == "accepted"


def test_malformed_or_conflicting_acceptance_journal_fails_closed(tmp_path, monkeypatch) -> None:
    """Malformed and ops-conflicting journals never write the project."""
    name = f"{_PREFIX}_bad_journal"
    pid = proposals.write(
        name, ops=[{"op": "replace", "path": "/width", "value": 1280}], title="Resize",
        rationale="fit the channel", base_revision=1, doc=_doc(),
    )["id"]

    async def scenario() -> tuple[int, int, dict, dict]:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            proposals.begin_accept(name, pid, {"steps": {}})
            malformed = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
            raw_path = config.PROPOSALS_DIR / name / f"{pid}.json"
            stored = json.loads(raw_path.read_text(encoding="utf-8"))
            current = proposals.read(name, pid) or {}
            stored["acceptance"] = {
                "baseRevision": 1, "targetRevision": 2,
                "projectDigest": "0" * 64, "opsDigest": chat._ops_digest(current.get("ops")),
                "reason": None, "steps": {},
            }
            raw_path.write_text(json.dumps(stored), encoding="utf-8")
            conflicting = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
            project = (await client.get(f"/api/project/{name}")).json()
            return malformed.status_code, conflicting.status_code, project, proposals.read(name, pid) or {}

    malformed_status, conflicting_status, project, proposal = _run(scenario())
    assert malformed_status == 409 and conflicting_status == 409
    assert proposal["status"] == "pending"
    assert project["revision"] == 1 and project["project"]["width"] == 1920


def test_journal_interrupted_on_an_earlier_version_still_reconciles(tmp_path, monkeypatch) -> None:
    """An accept interrupted on an earlier version left receiptDigest in its journal; a retry settles it."""
    monkeypatch.setattr(config, "NOTES_DIR", tmp_path / "notes")
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    name = f"{_PREFIX}_old_journal"
    pid = proposals.write(
        name, ops=[{"op": "replace", "path": "/width", "value": 1280}], title="Resize",
        rationale="fit the channel", base_revision=1, doc=_doc(),
    )["id"]

    async def scenario() -> tuple[int, dict, dict]:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            original = proposals.set_status

            def fail_once(*args, **kwargs):
                raise RuntimeError("injected status failure")

            proposals.set_status = fail_once
            try:
                try:
                    await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
                except RuntimeError:
                    pass  # ASGITransport surfaces injected server failures directly.
            finally:
                proposals.set_status = original
            raw_path = config.PROPOSALS_DIR / name / f"{pid}.json"
            stored = json.loads(raw_path.read_text(encoding="utf-8"))
            stored["acceptance"]["receiptDigest"] = "0" * 64
            raw_path.write_text(json.dumps(stored), encoding="utf-8")
            retry = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
            project = (await client.get(f"/api/project/{name}")).json()
            return retry.status_code, project, proposals.read(name, pid) or {}

    status_code, project, proposal = _run(scenario())
    assert status_code == 200
    assert project["revision"] == 2 and project["project"]["width"] == 1280
    assert proposal["status"] == "accepted"


def test_legacy_status_cannot_accept_active_journal(tmp_path, monkeypatch) -> None:
    """The status-only compatibility route cannot settle a journaled atomic accept."""
    name = f"{_PREFIX}_status_journal"
    pid = proposals.write(name, ops=[], title="Once", rationale="r", base_revision=1)["id"]
    proposals.begin_accept(name, pid, {"active": True})

    async def scenario() -> tuple[int, dict]:
        async with _client() as client:
            response = await client.post(f"/api/proposal/{name}/{pid}/status", json={"status": "accepted"})
            return response.status_code, proposals.read(name, pid) or {}

    status_code, proposal = _run(scenario())
    assert status_code == 409 and proposal["status"] == "pending"


def test_accept_retry_reconciles_project_and_each_post_write_failure_boundary(tmp_path, monkeypatch) -> None:
    """A journaled accept retries without duplicate ops, notes, decisions, or terminal events."""
    monkeypatch.setattr(config, "NOTES_DIR", tmp_path / "notes")
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    stages = ("project", "status", "notes", "decision", "event")

    async def scenario() -> list[tuple[str, int, int, str, int]]:
        results: list[tuple[str, int, int, str, int]] = []
        async with _client() as client:
            for index, stage in enumerate(stages):
                name = f"{_PREFIX}_retry_{index}"
                pid = proposals.write(
                    name, ops=[{"op": "replace", "path": "/width", "value": 1280}], title="Resize",
                    rationale="fit the channel", base_revision=1, doc=_doc(),
                )["id"]
                await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
                original = {
                    "project": helpers.project_mod.write_project,
                    "status": proposals.set_status,
                    "notes": chat.notes_mod.append_once,
                    "decision": chat.decisions_mod.record_once,
                    "event": studio_state.hub.publish_event,
                }[stage]
                target = {
                    "project": helpers.project_mod,
                    "status": proposals,
                    "notes": chat.notes_mod,
                    "decision": chat.decisions_mod,
                    "event": studio_state.hub,
                }[stage]
                attr = {
                    "project": "write_project", "status": "set_status", "notes": "append_once",
                    "decision": "record_once", "event": "publish_event",
                }[stage]

                def fail_once(*args, **kwargs):
                    raise RuntimeError(f"injected {stage} failure")

                setattr(target, attr, fail_once)
                try:
                    try:
                        await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
                    except RuntimeError:
                        pass  # ASGITransport surfaces injected server failures directly.
                finally:
                    setattr(target, attr, original)
                retry = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
                again = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
                project = (await client.get(f"/api/project/{name}")).json()
                proposal = proposals.read(name, pid) or {}
                results.append((stage, retry.status_code, again.status_code, proposal.get("status", ""), project["revision"]))
                assert project["project"]["width"] == 1280
                assert notes.read(name).count("Resize") == 1
                assert len(decisions.list_for(name, scope="project")) == 1
        return results

    assert _run(scenario()) == [(stage, 200, 200, "accepted", 2) for stage in stages]


def test_delivery_marker_prevents_duplicate_sse_after_publish_failure(tmp_path, monkeypatch) -> None:
    """SSE is a best-effort hint: a delivered-then-failed publish is not replayed on retry."""
    name = f"{_PREFIX}_sse_once"
    pid = proposals.write(
        name, ops=[{"op": "replace", "path": "/width", "value": 1280}], title="Resize",
        rationale="fit the channel", base_revision=1, doc=_doc(),
    )["id"]
    project_messages: list[tuple[str, int]] = []
    proposal_messages: list[dict] = []

    def delivered_project(project: str, revision: int) -> None:
        project_messages.append((project, revision))
        raise RuntimeError("disconnect after delivery")

    def delivered_proposal(project: str, message: dict) -> None:
        proposal_messages.append(message)
        raise RuntimeError("disconnect after delivery")

    async def scenario() -> tuple[int, int]:
        async with _client() as client:
            await client.put(f"/api/project/{name}", json={"project": _doc(), "baseRevision": 0})
            monkeypatch.setattr(studio_state.hub, "publish", delivered_project)
            monkeypatch.setattr(studio_state.hub, "publish_event", delivered_proposal)
            first = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
            retry = await client.post(f"/api/proposal/{name}/{pid}/accept", json={})
            return first.status_code, retry.status_code

    first_status, retry_status = _run(scenario())
    assert first_status == retry_status == 200
    assert project_messages == [(name, 2)]
    assert len(proposal_messages) == 1 and proposal_messages[0]["status"] == "accepted"


def test_status_route_unknown_proposal_is_404() -> None:
    name = f"{_PREFIX}_404"

    async def scenario() -> int:
        async with _client() as client:
            r = await client.post(f"/api/proposal/{name}/deadbeef/status", json={"status": "accepted"})
            return r.status_code

    assert _run(scenario()) == 404


def test_list_all_skips_a_corrupt_proposal_file() -> None:
    """A half-written proposal json is skipped by list_all, not fatal — the good
    proposals still reconcile on reconnect."""
    slug = f"{_PREFIX}_corrupt"
    good = proposals.write(slug, ops=[], title="good", rationale="r", base_revision=0)
    (config.PROPOSALS_DIR / slug / "bad.json").write_text("{ not valid json", encoding="utf-8")
    assert [p["id"] for p in proposals.list_all(slug)] == [good["id"]]


def test_accept_status_flip_appends_the_accept_notes_entry() -> None:
    name = f"{_PREFIX}_accept_notes"
    pid = proposals.write(
        name, ops=[], title="Tighten the intro", rationale="3 filler restarts", base_revision=1,
    )["id"]

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post(f"/api/proposal/{name}/{pid}/status", json={"status": "accepted"})

    resp = _run(scenario())
    assert resp.status_code == 200
    text = notes.read(name)
    assert "— Claude: Tighten the intro" in text
    assert "3 filler restarts" in text
