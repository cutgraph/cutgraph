"""POST /api/chat + agent events on the existing SSE + durable chat jsonl.

In-process via httpx.ASGITransport (harness = test_revision_gate.py). A fake AgentHost is injected via
`_agent_host_override` so no SDK is imported; the fake still routes emitted events through the studio's
real ToolCtx (hub.publish_event + chat-log mirror), proving the whole seam end to end.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterator
from typing import TypeVar

import httpx
import pytest

import config
from pipeline import proposals
from pipeline import project as project_mod
from pipeline.agent_session import AgentEvent, FakeAgentSession
from scripts import agent_host, studio_app
from studio import state as studio_state
from studio.routes import chat

T = TypeVar("T")
_PREFIX = "zzchat"


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


def _fake_host() -> agent_host.AgentHost:
    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        return FakeAgentSession(script=lambda t: [
            AgentEvent("chat_text", {"text": f"got: {t}"}),
            AgentEvent("turn_done", {}),
        ])
    # Real studio ToolCtx builder → events flow onto the hub + chat.jsonl exactly as in production.
    return agent_host.AgentHost(make_session=make_session, make_ctx=chat._make_tool_ctx)


def _write_video(name: str) -> None:
    project_mod.write_project(config.PROJECTS_DIR / f"{name}.json", project_mod.Project(name=name))


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    def _wipe() -> None:
        for base, pat in ((config.CHAT_DIR, f"{_PREFIX}*.jsonl"),
                          (config.NOTES_DIR, f"{_PREFIX}*.md")):
            if base.exists():
                for f in base.glob(pat):
                    f.unlink(missing_ok=True)
        if config.PROPOSALS_DIR.exists():
            for d in config.PROPOSALS_DIR.glob(f"{_PREFIX}*"):
                for f in d.glob("*"):
                    f.unlink(missing_ok=True)
                d.rmdir()
    _wipe()
    studio_state._agent_host_override = None
    yield
    studio_state._agent_host_override = None
    _wipe()


def test_chat_enqueues_emits_and_logs() -> None:
    name = f"{_PREFIX}_flow"
    _write_video(name)
    host = _fake_host()
    studio_state._agent_host_override = host

    async def scenario() -> tuple[dict, list[dict], dict]:
        async with _client() as client:
            q = studio_state.hub.subscribe(name)
            r = await client.post(f"/api/chat/{name}", json={"text": "hi there"})
            post_body = r.json()
            seen: list[dict] = []
            while not any(m.get("type") == "turn_done" for m in seen):
                seen.append(await asyncio.wait_for(q.get(), timeout=5))
            studio_state.hub.unsubscribe(name, q)
            await host.aclose_all()
            log = (await client.get(f"/api/chat/{name}/log")).json()
        return post_body, seen, log

    post_body, seen, log = _run(scenario())

    assert post_body == {"ok": True}
    assert any(m.get("type") == "chat_text" and m.get("text") == "got: hi there" for m in seen)
    assert any(m["type"] == "turn_done" for m in seen)
    # durable jsonl: the user turn + the agent's finalized chat_text (turn_done is ephemeral, not logged)
    entries = log["entries"]
    assert {"role": "user", "text": "hi there"} in entries
    assert any(e.get("role") == "agent" and e.get("type") == "chat_text"
               and e.get("text") == "got: hi there" for e in entries)
    assert not any(e.get("type") == "turn_done" for e in entries)


def test_chat_bad_body_is_400() -> None:
    name = f"{_PREFIX}_bad"
    _write_video(name)
    studio_state._agent_host_override = _fake_host()

    async def scenario() -> int:
        async with _client() as client:
            r = await client.post(f"/api/chat/{name}", json={"text": "   "})
            return r.status_code

    assert _run(scenario()) == 400


def test_chat_config_exposes_the_versioned_provider_registry_without_breaking_legacy_fields(monkeypatch) -> None:
    async def scenario() -> dict:
        async with _client() as client:
            return (await client.get("/api/chat/config")).json()

    body = _run(scenario())
    assert body["schemaVersion"] == 1
    assert body["defaults"] == {"model": None, "effort": None, "mode": "default"}
    providers = {provider["id"]: provider for provider in body["providers"]}
    assert providers["claude"]["profiles"][0]["capabilities"]["proposalOnly"] is True
    assert list(providers) == ["claude"]


def test_chat_rejects_an_unknown_provider_without_enqueuing_a_turn(monkeypatch) -> None:
    name = f"{_PREFIX}_unknown_provider"
    _write_video(name)
    host = _fake_host()
    studio_state._agent_host_override = host

    async def scenario() -> tuple[int, dict]:
        async with _client() as client:
            response = await client.post(f"/api/chat/{name}", json={
                "text": "hi", "providerId": "unknown", "profileId": "default",
                "model": None, "effort": "high", "mode": "auto",
            })
            return response.status_code, response.json()

    status, body = _run(scenario())
    assert status == 409
    assert body["code"] == "provider_unavailable"
    assert not host._workers


def test_chat_rejects_claude_when_key_verification_failed_without_enqueuing_a_turn(monkeypatch) -> None:
    monkeypatch.setattr(studio_state._claude_connection, "status",
                        lambda: {"connected": True, "state": "error"})
    name = f"{_PREFIX}_key_error"
    _write_video(name)
    host = _fake_host()
    studio_state._agent_host_override = host

    async def scenario() -> tuple[int, dict, dict]:
        async with _client() as client:
            response = await client.post(f"/api/chat/{name}", json={
                "text": "hi", "providerId": "claude", "profileId": "default",
                "model": None, "effort": None, "mode": "default",
            })
            log = (await client.get(f"/api/chat/{name}/log")).json()
            return response.status_code, response.json(), log

    status, body, log = _run(scenario())
    assert status == 409
    assert body["code"] == "authentication_required"
    assert "Settings" in body["error"]
    assert not host._workers
    assert log == {"entries": []}


def test_chat_log_empty_when_no_history() -> None:
    name = f"{_PREFIX}_emptylog"

    async def scenario() -> dict:
        async with _client() as client:
            return (await client.get(f"/api/chat/{name}/log")).json()

    assert _run(scenario()) == {"entries": []}


# ── a reject reaches the warm session as prepended next-turn context ──
def test_reject_feedback_reaches_session_next_turn() -> None:
    name = f"{_PREFIX}_fb"
    _write_video(name)
    sessions: list[FakeAgentSession] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        s = FakeAgentSession(script=lambda t: [
            AgentEvent("chat_text", {"text": f"got: {t}"}), AgentEvent("turn_done", {})])
        sessions.append(s)
        return s

    host = agent_host.AgentHost(make_session=make_session, make_ctx=chat._make_tool_ctx)
    studio_state._agent_host_override = host
    pid = proposals.write(name, ops=[], title="Cut intro", rationale="r", base_revision=1)["id"]

    async def _await_turns(q: "asyncio.Queue", n: int, seen: list) -> None:
        while sum(m.get("type") == "turn_done" for m in seen) < n:
            seen.append(await asyncio.wait_for(q.get(), timeout=5))

    async def scenario() -> None:
        async with _client() as client:
            q = studio_state.hub.subscribe(name)
            seen: list[dict] = []
            await client.post(f"/api/chat/{name}", json={"text": "hello"})
            await _await_turns(q, 1, seen)                     # first turn fully processed
            await client.post(f"/api/proposal/{name}/{pid}/status",
                              json={"status": "rejected", "reason": "keep the intro"})
            await client.post(f"/api/chat/{name}", json={"text": "try again"})
            await _await_turns(q, 2, seen)
            studio_state.hub.unsubscribe(name, q)
            await host.aclose_all()

    _run(scenario())

    assert sessions[0].turns[0] == "hello"
    second = sessions[0].turns[1]
    assert "rejected" in second and "keep the intro" in second   # feedback folded into the next turn
    assert second.endswith("try again")


def test_chat_disabled_mode_returns_503(monkeypatch) -> None:
    """AGENT_HOST_MODE outside {inproc,subprocess} ⇒ _chat_host() is None ⇒ chat 503 (the
    host-disabled contract, previously uncovered — every route test injected an override)."""
    name = f"{_PREFIX}_off"
    _write_video(name)
    studio_state._agent_host_override = None
    monkeypatch.setattr(config, "AGENT_HOST_MODE", "off")

    async def scenario() -> int:
        async with _client() as client:
            r = await client.post(f"/api/chat/{name}", json={"text": "hi"})
            return r.status_code

    assert _run(scenario()) == 503


def test_chat_missing_video_is_404_before_host_or_log(monkeypatch) -> None:
    name = f"{_PREFIX}_missing"

    def unexpected_host():
        raise AssertionError("missing video must not construct a host")

    monkeypatch.setattr(chat, "_chat_host", unexpected_host)

    async def scenario() -> int:
        async with _client() as client:
            return (await client.post(f"/api/chat/{name}", json={"text": "hello"})).status_code

    assert _run(scenario()) == 404
    assert not chat._chat_log_path(name).exists()


def test_chat_log_skips_corrupt_lines() -> None:
    """A truncated/garbage jsonl line (studio killed mid-append) is skipped, not
    fatal — the reconnect GET still reconciles the good turns."""
    name = f"{_PREFIX}_corrupt"
    config.CHAT_DIR.mkdir(parents=True, exist_ok=True)
    (config.CHAT_DIR / f"{name}.jsonl").write_text(
        '{"role":"user","text":"ok"}\n'
        '{ this is not valid json\n'
        '\n'
        '{"role":"agent","type":"chat_text","text":"fine"}\n',
        encoding="utf-8")

    entries = chat._read_chat_log(name)
    assert entries == [{"role": "user", "text": "ok"},
                       {"role": "agent", "type": "chat_text", "text": "fine"}]
