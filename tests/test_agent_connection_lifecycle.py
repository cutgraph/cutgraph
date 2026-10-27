from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest

import config
from pipeline import agent_credentials, project, secret_store
from pipeline.agent_session import AgentEvent, FakeAgentSession
from scripts import agent_host, studio_app
from studio import state
from studio.routes import chat


@pytest.fixture(autouse=True)
def credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "CHAT_DIR", tmp_path / "chat")
    monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200))))


def test_all_inherited_provider_variables_are_blank(monkeypatch):
    secret_store.set("anthropic-api-key", "test-api-secret-value")
    names = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
             "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_UNIX_SOCKET", "CLAUDE_CODE_REMOTE",
             "ANTHROPIC_GATEWAY_URL", "CLAUDE_FUTURE_ROUTING")
    for name in names:
        monkeypatch.setenv(name, "inherited-routing")
    env = agent_host._sdk_environment()
    assert all(env.get(name) == "" for name in names)
    assert env["ANTHROPIC_API_KEY"] == "test-api-secret-value"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_fresh_revoked_key_checks_in_background_and_stays_rejected(monkeypatch):
    started, finish = threading.Event(), threading.Event()
    calls = []

    def respond(request):
        calls.append(request)
        started.set()
        assert finish.wait(5)
        return httpx.Response(401)

    monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(
        transport=httpx.MockTransport(respond)))
    secret_store.set("anthropic-api-key", "revoked-test-secret-value")
    connection = agent_credentials.ClaudeConnection()
    try:
        assert connection.status() == {"connected": True, "state": "checking", "source": "settings"}
        assert started.wait(5)
        assert connection.status()["state"] == "checking"
    finally:
        finish.set()
    for _ in range(500):
        if connection.status()["state"] == "error":
            break
        threading.Event().wait(0.01)
    assert connection.status()["state"] == "error"
    assert connection.verify()["state"] == "error"
    assert len(calls) == 1
    secret_store.set("anthropic-api-key", "replacement-test-secret")
    assert connection.status()["state"] == "checking"


def _fake_sdk(monkeypatch, connect):
    clients = []

    class Client:
        def __init__(self, options):
            self.env, self.closed, self.turns = options.env, False, []
            clients.append(self)

        async def connect(self, _prompt):
            await connect(self, len(clients))

        async def query(self, text):
            self.turns.append(text)

        async def receive_response(self):
            for item in []:
                yield item

        async def disconnect(self):
            self.closed = True

    sdk = SimpleNamespace(ClaudeSDKClient=Client)
    for name in ("AssistantMessage", "ResultMessage", "TextBlock", "ToolResultBlock", "ToolUseBlock", "UserMessage"):
        setattr(sdk, name, type(name, (), {}))
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(agent_host, "build_options", lambda _directory, _ctx, *args, **kwargs:
                        SimpleNamespace(env=agent_host._sdk_environment(*args, **kwargs)))
    return clients


async def _emit(_event):
    pass


def test_failed_reconnect_closes_client_and_retries_next_turn(monkeypatch):
    async def connect(_client, count):
        if count == 2:
            raise RuntimeError("connection failed")

    clients = _fake_sdk(monkeypatch, connect)
    session = agent_host.SdkAgentSession("video", chat._make_tool_ctx("video"))

    async def scenario():
        secret_store.set("anthropic-api-key", "first-test-secret-value")
        await session.aconnect()
        secret_store.set("anthropic-api-key", "second-test-secret-value")
        with pytest.raises(RuntimeError, match="connection failed"):
            await session.send_turn("failed", _emit)
        assert clients[1].closed
        assert session._client is None
        await session.send_turn("retry", _emit)
        await session.aclose()

    asyncio.run(scenario())
    assert len(clients) == 3
    assert clients[1].turns == []
    assert clients[2].turns == ["retry"]
    assert all(client.closed for client in clients)


def test_session_uses_one_snapshot_and_rechecks_after_concurrent_change(monkeypatch):
    async def connect(_client, count):
        if count == 1:
            secret_store.set("anthropic-api-key", "second-test-secret-value")

    clients = _fake_sdk(monkeypatch, connect)
    original = agent_credentials.require_key
    resolves = []

    def resolve():
        key = original()
        resolves.append(key)
        return key

    monkeypatch.setattr(agent_credentials, "require_key", resolve)
    session = agent_host.SdkAgentSession("video", chat._make_tool_ctx("video"))

    async def scenario():
        secret_store.set("anthropic-api-key", "first-test-secret-value")
        await session.aconnect()
        assert resolves == ["first-test-secret-value"]
        await session.send_turn("safe", _emit)
        await session.aclose()

    asyncio.run(scenario())
    assert clients[0].env["ANTHROPIC_API_KEY"] == "first-test-secret-value"
    assert clients[0].turns == []
    assert clients[1].env["ANTHROPIC_API_KEY"] == "second-test-secret-value"
    assert clients[1].turns == ["safe"]


def test_environment_override_refuses_settings_mutations(monkeypatch):
    secret_store.set("anthropic-api-key", "stored-test-secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "override-test-secret")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            assert (await client.get("/api/agent/connection")).json()["source"] == "environment"
            for response in (await client.post("/api/agent/connection", json={"key": "replacement"}),
                             await client.delete("/api/agent/connection")):
                assert response.status_code == 400
                assert response.json()["error"] == "ANTHROPIC_API_KEY is set in this environment and takes precedence"
                assert "override-test-secret" not in response.text

    asyncio.run(scenario())
    assert secret_store.get("anthropic-api-key") == "stored-test-secret-value"


@pytest.mark.parametrize("body", [[], "key", None, 7])
@pytest.mark.parametrize("method,path", [("POST", "/api/agent/connection"),
                                        ("POST", "/api/agent/connection/verify"),
                                        ("DELETE", "/api/agent/connection")])
def test_connection_routes_reject_non_object_json(body, method, path):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            response = await client.request(method, path, content=json.dumps(body),
                                            headers={"Content-Type": "application/json"})
            assert response.status_code == 400

    asyncio.run(scenario())


def test_verify_handles_secret_store_failure(monkeypatch):
    def fail():
        raise secret_store.SecretStoreError("private failure detail")

    monkeypatch.setattr(state._claude_connection, "verify", fail)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            response = await client.post("/api/agent/connection/verify")
            assert response.status_code == 503
            assert response.json()["code"] == "unavailable"
            assert "private failure detail" not in response.text

    asyncio.run(scenario())


def test_disconnect_closes_warm_sessions(monkeypatch):
    session = FakeAgentSession()
    host = agent_host.AgentHost(make_session=lambda _p, _ctx: session, make_ctx=chat._make_tool_ctx)
    monkeypatch.setattr(state, "_agent_host_override", host)
    secret_store.set("anthropic-api-key", "test-api-secret-value")

    async def scenario():
        await host.post_turn("video", "hello")
        queue = state.hub.subscribe("video")
        try:
            while (await asyncio.wait_for(queue.get(), 5))["type"] != "turn_done":
                pass
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                         base_url="http://studio.test") as client:
                assert (await client.delete("/api/agent/connection")).status_code == 200
            assert session.closed
            assert not host._workers
        finally:
            state.hub.unsubscribe("video", queue)
            await host.aclose_all()

    asyncio.run(scenario())


def test_pasted_key_and_nested_tool_input_are_redacted_everywhere(monkeypatch):
    key = "pasted-test-secret-value"
    secret_store.set("anthropic-api-key", key)
    state._claude_connection.verify()
    project.write_project(config.PROJECTS_DIR / "video.json", project.Project(name="video"))
    session = FakeAgentSession(script=lambda _text: [
        AgentEvent("tool_use", {"name": "tool", "input": {key: [key, {"text": key}]}}),
        AgentEvent("chat_text", {"text": key, "nested": [key]}),
    ])
    host = agent_host.AgentHost(make_session=lambda _p, _ctx: session, make_ctx=chat._make_tool_ctx)
    monkeypatch.setattr(state, "_agent_host_override", host)

    async def scenario():
        queue = state.hub.subscribe("video")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                         base_url="http://studio.test") as client:
                assert (await client.post("/api/chat/video", json={"text": f"my key is {key}"})).status_code == 200
                events = []
                while not any(event["type"] == "turn_done" for event in events):
                    events.append(await asyncio.wait_for(queue.get(), 5))
                assert key not in str(events)
                assert "[redacted]" in str(events)
                assert key not in (await client.get("/api/chat/video/log")).text
                assert key not in chat._chat_log_path("video").read_text()
                assert session.turns == ["my key is [redacted]"]
        finally:
            state.hub.unsubscribe("video", queue)
            await host.aclose_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["studio.errors", "httpx", "httpcore.connection", "httpcore.http11",
                                  "httpcore.http2", "httpcore.proxy", "claude_agent_sdk",
                                  "claude_agent_sdk._internal.client", "claude_agent_sdk._internal.query",
                                  "claude_agent_sdk._internal.message_parser", "claude_agent_sdk._internal.transport",
                                  "claude_agent_sdk._internal.transport.subprocess_cli"])
def test_each_sensitive_logger_actually_redacts_output(name, caplog):
    key = "logged-test-secret-value"
    secret_store.set("anthropic-api-key", key)
    agent_credentials.resolve_key()
    with caplog.at_level(logging.DEBUG):
        try:
            raise RuntimeError(key)
        except RuntimeError:
            logging.getLogger(name).exception("credential %s", key, extra={"input": {"keys": [key]}})
    assert caplog.records
    assert key not in caplog.text
