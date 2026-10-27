from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

import config
from pipeline import agent_credentials, decisions, notes, project, proposals, secret_store
from pipeline.agent_session import AgentEvent, FakeAgentSession
from pipeline.errors import ValidationError
from scripts import agent_host, studio_app
from studio import state
from studio.routes import chat

KEY = "synthetic-api-key-for-regressions"


@pytest.fixture(autouse=True)
def credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for name in ("RUNTIME_DIR", "PROJECTS_DIR", "CHAT_DIR", "NOTES_DIR", "DECISIONS_DIR", "PROPOSALS_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    monkeypatch.setattr(agent_credentials, "_known_secrets", set())
    monkeypatch.setattr(agent_credentials, "_secret_pattern", None)
    monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200))))


def test_short_key_refused_and_event_types_survive_a_turn(monkeypatch):
    project.write_project(config.PROJECTS_DIR / "video.json", project.Project(name="video"))
    session = FakeAgentSession(script=lambda _text: [AgentEvent("chat_text", {"text": "type a title"})])
    host = agent_host.AgentHost(make_session=lambda _p, _ctx: session, make_ctx=chat._make_tool_ctx)
    monkeypatch.setattr(state, "_agent_host_override", host)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            response = await client.post("/api/agent/connection", json={"key": "type"})
            assert response.status_code == 400
            assert "type" not in agent_credentials._known_secrets
            assert (await client.post("/api/agent/connection", json={"key": KEY})).json()["state"] == "ready"
            queue = state.hub.subscribe("video")
            try:
                assert (await client.post("/api/chat/video", json={"text": "type a title"})).status_code == 200
                events = []
                while not any(event["type"] == "turn_done" for event in events):
                    events.append(await asyncio.wait_for(queue.get(), 5))
                assert all(isinstance(event["type"], str) for event in events)
                assert any(event.get("text") == "type a title" for event in events)
            finally:
                state.hub.unsubscribe("video", queue)
                await host.aclose_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("length", [1, 4, 19])
@pytest.mark.parametrize("source", ["settings", "environment"])
def test_invalid_short_key_never_enters_redaction_set(monkeypatch, length, source):
    value = "x" * length
    if source == "settings":
        secret_store.set("anthropic-api-key", value)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    assert agent_credentials.resolve_key() is None
    assert agent_credentials.redact(value) == value


@pytest.mark.parametrize("source", ["settings", "environment"])
@pytest.mark.parametrize("probe_status", [200, 401, 403])
def test_auth_turn_rejection_can_be_verified(monkeypatch, source, probe_status):
    project.write_project(config.PROJECTS_DIR / "video.json", project.Project(name="video"))
    connection = agent_credentials.ClaudeConnection()
    monkeypatch.setattr(state, "_claude_connection", connection)
    if source == "environment":
        monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
        assert connection.verify()["state"] == "ready"
    else:
        assert connection.connect(KEY)["state"] == "ready"

    def fail(_text):
        return [AgentEvent("agent_error", {"kind": "auth", "message": "authentication_failed"},
                           authentication_failed=True, key_fingerprint=agent_credentials.key_fingerprint(KEY))]

    host = agent_host.AgentHost(lambda _p, _ctx: FakeAgentSession(script=fail), chat._make_tool_ctx)
    monkeypatch.setattr(state, "_agent_host_override", host)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            queue = state.hub.subscribe("video")
            try:
                assert (await client.post("/api/chat/video", json={"text": "hello"})).status_code == 200
                while True:
                    event = await asyncio.wait_for(queue.get(), 5)
                    if event["type"] == "agent_error":
                        assert event["kind"] == "auth"
                        assert "authentication_failed" not in event and "key_fingerprint" not in event
                    if event["type"] == "turn_done":
                        break
                assert connection.snapshot()["state"] == "error"
                monkeypatch.setattr(agent_credentials, "_new_client", lambda: pytest.fail("rejected keys must stay offline"))
                for _ in range(3):
                    availability = (await client.get("/api/chat/config")).json()["providers"][0]["availability"]
                    assert not availability["available"]
                    assert availability["error"]["code"] == "authentication_required"
                requests = []

                def respond(request):
                    requests.append(request)
                    return httpx.Response(probe_status)

                monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(
                    transport=httpx.MockTransport(respond)))
                expected = "ready" if probe_status == 200 else "error"
                assert (await client.post("/api/agent/connection/verify")).json()["state"] == expected
                assert len(requests) == 1
                availability = (await client.get("/api/chat/config")).json()["providers"][0]["availability"]
                assert availability["available"] == (probe_status == 200)
                if probe_status != 200:
                    assert (await client.post("/api/agent/connection/verify")).json()["state"] == "error"
                assert len(requests) == 1
                log = (await client.get("/api/chat/video/log")).text
                assert "key_fingerprint" not in log and "authentication_failed" not in log
            finally:
                state.hub.unsubscribe("video", queue)
                await host.aclose_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [
    "unstructured_event", "407 Proxy Authentication Required", "Failed to load OAuth config",
    "unknown author field", "authentication failed",
])
def test_unstructured_auth_errors_do_not_reject_key(monkeypatch, failure):
    connection = agent_credentials.ClaudeConnection()
    monkeypatch.setattr(state, "_claude_connection", connection)
    assert connection.connect(KEY)["state"] == "ready"
    monkeypatch.setattr(connection, "reject", lambda _: pytest.fail("unstructured errors must not reject a key"))

    def fail(_text):
        if failure == "unstructured_event":
            return [AgentEvent("agent_error", {"kind": "auth", "message": "authentication failed"})]
        raise RuntimeError(failure)

    host = agent_host.AgentHost(lambda _p, _ctx: FakeAgentSession(script=fail), chat._make_tool_ctx)

    async def scenario():
        queue = state.hub.subscribe("video")
        try:
            await host.post_turn("video", "hello")
            events = []
            while not events or events[-1]["type"] != "turn_done":
                events.append(await asyncio.wait_for(queue.get(), 5))
            assert events[0]["type"] == "agent_error"
            assert events[0]["kind"] == "auth"
            assert connection.status()["state"] == "ready"
            assert chat._provider_registry().providers[0].availability.available
        finally:
            state.hub.unsubscribe("video", queue)
            await host.aclose_all()

    asyncio.run(scenario())


def test_missing_key_exception_carries_structured_rejection(monkeypatch):
    calls = []
    monkeypatch.setattr(state._claude_connection, "reject", lambda fingerprint: calls.append(fingerprint))

    def fail(_text):
        raise ValidationError(agent_credentials.CONNECT_MESSAGE)

    host = agent_host.AgentHost(lambda _p, _ctx: FakeAgentSession(script=fail), chat._make_tool_ctx)

    async def scenario():
        queue = state.hub.subscribe("video")
        try:
            await host.post_turn("video", "hello")
            while (await asyncio.wait_for(queue.get(), 5))["type"] != "turn_done":
                pass
            assert calls == [None]
        finally:
            state.hub.unsubscribe("video", queue)
            await host.aclose_all()

    asyncio.run(scenario())


def test_late_turn_rejection_does_not_reject_replacement_key(monkeypatch):
    connection = agent_credentials.ClaudeConnection()
    monkeypatch.setattr(state, "_claude_connection", connection)
    assert connection.connect(KEY)["state"] == "ready"
    started, finish = asyncio.Event(), asyncio.Event()

    class LateFailure(FakeAgentSession):
        async def send_turn(self, text, emit):
            fingerprint = agent_credentials.key_fingerprint(agent_credentials.resolve_key())
            started.set()
            await finish.wait()
            await emit(AgentEvent("agent_error", {"kind": "auth"},
                                 authentication_failed=True, key_fingerprint=fingerprint))

    host = agent_host.AgentHost(lambda _p, _ctx: LateFailure(), chat._make_tool_ctx)

    async def scenario():
        queue = state.hub.subscribe("video")
        try:
            await host.post_turn("video", "hello")
            await asyncio.wait_for(started.wait(), 5)
            assert connection.connect(KEY + "-replacement")["state"] == "ready"
            finish.set()
            while (await asyncio.wait_for(queue.get(), 5))["type"] != "turn_done":
                pass
            assert connection.status()["state"] == "ready"
            assert chat._provider_registry().providers[0].availability.available
        finally:
            finish.set()
            state.hub.unsubscribe("video", queue)
            await host.aclose_all()

    asyncio.run(scenario())


def test_turn_rejection_ignores_inflight_verification(monkeypatch):
    connection = agent_credentials.ClaudeConnection()
    assert connection.connect(KEY)["state"] == "ready"
    started, finish = threading.Event(), threading.Event()

    def respond(_request):
        started.set()
        assert finish.wait(5)
        return httpx.Response(200)

    monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    thread = threading.Thread(target=connection.verify)
    thread.start()
    try:
        assert started.wait(5)
        connection.reject(agent_credentials.key_fingerprint(KEY))
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert connection.status()["state"] == "error"


@pytest.mark.parametrize("model", ["bogus", 42, None])
@pytest.mark.parametrize("connection_state", ["error", "checking"])
def test_chat_requires_ready_before_model_validation(monkeypatch, model, connection_state):
    secret_store.set("anthropic-api-key", KEY)
    project.write_project(config.PROJECTS_DIR / "video.json", project.Project(name="video"))
    connection = agent_credentials.ClaudeConnection()
    monkeypatch.setattr(state, "_claude_connection", connection)
    if connection_state == "error":
        monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(401))))
        assert connection.verify()["state"] == "error"
    else:
        monkeypatch.setattr(connection, "status", lambda: {"connected": True, "state": "checking"})
    monkeypatch.setattr(chat, "_chat_host", lambda: pytest.fail("readiness must be checked before the host"))

    async def scenario():
        body = {"text": "hello"}
        if model is not None:
            body["model"] = model
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            response = await client.post("/api/chat/video", json=body)
            assert response.status_code == 409
            assert response.json()["code"] == "authentication_required"
            assert response.json()["error"] == (agent_credentials.VERIFYING_MESSAGE if connection_state == "checking"
                                                else agent_credentials.UNVERIFIED_MESSAGE)
            assert not chat._chat_log_path("video").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["draft", "describe"])
def test_one_shot_sdk_inputs_and_structured_output_are_redacted(monkeypatch, operation):
    secret_store.set("anthropic-api-key", KEY)
    prompts = []

    class Result:
        is_error = False
        result = None
        structured_output = {"name": KEY, "nested": [{"text": KEY}]}

    async def query(*, prompt, options):
        prompts.append(prompt)
        yield Result()

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", SimpleNamespace(
        ClaudeAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), ResultMessage=Result, query=query))
    monkeypatch.setattr(agent_host, "_bundled_cli", lambda: "synthetic-cli")
    if operation == "draft":
        output = asyncio.run(agent_host._draft_by_copilot(f"idea {KEY}", f"format {KEY}"))
    else:
        output = asyncio.run(agent_host._describe_by_copilot(f"describe {KEY}", "lens"))
    assert KEY not in str(prompts)
    assert "[redacted]" in str(prompts)
    assert output == {"name": "[redacted]", "nested": [{"text": "[redacted]"}]}


@pytest.mark.parametrize("operation,limit", [("draft", 500), ("describe", 200)])
def test_one_shot_redacts_before_input_truncation(monkeypatch, operation, limit):
    secret_store.set("anthropic-api-key", KEY)
    inputs = []

    async def completion(text, _hint_or_rig):
        inputs.append(text)
        if operation == "draft":
            return {"title": KEY, "scenes": [{"role": "Hook", "title": KEY, "body": KEY}]}
        return {"name": KEY, "spec": {"eyes": "side", "mouth": "flat"}}

    monkeypatch.setattr(agent_host, f"_{operation}_by_copilot", completion)
    text = "a" * (limit - 5) + KEY
    if operation == "draft":
        output = asyncio.run(agent_host.draft_script(text))
    else:
        output = asyncio.run(agent_host.describe_expression(text, "lens"))
    assert KEY[:5] not in inputs[0]
    assert KEY not in str(output)
    assert output["source"] == "copilot"


def test_rejection_and_all_note_decision_text_are_redacted():
    agent_credentials.set_key(KEY)
    project.write_project(config.PROJECTS_DIR / "video.json", project.Project(name="video"))
    proposal = proposals.write("video", base_revision=0, title=KEY, rationale=KEY, ops=[])
    chat._finish_proposal_status("video", proposal, "rejected", f"reason {KEY}")
    notes.append("video", KEY, KEY)
    notes.append_once("video", KEY, KEY, "unique-note")
    decisions.record("video", decision="manual", intent=KEY, rationale=KEY,
                     operation_ids=["operation"], evidence_ids=["evidence"])
    assert KEY not in notes.read("video")
    assert "[redacted]" in notes.read("video")
    persisted = list(config.DECISIONS_DIR.rglob("*.json"))
    assert persisted
    assert all(KEY not in path.read_text() for path in persisted)


@pytest.mark.parametrize("family", ["claude_agent_sdk._internal", "mcp", "asyncio", "unlisted"])
def test_process_logging_redacts_fresh_logger_names(family, caplog):
    agent_credentials.set_key(KEY)
    logger = logging.getLogger(f"{family}.{uuid.uuid4().hex}")
    with caplog.at_level(logging.DEBUG):
        try:
            raise RuntimeError(KEY)
        except RuntimeError:
            logger.exception("credential %s", KEY,
                             extra={"input": {"keys": [KEY]}})
    assert caplog.records
    assert "[redacted]" in caplog.text
    assert KEY not in caplog.text


def test_sdk_environment_backstops_and_gpu_child_key(monkeypatch, tmp_path):
    from claude_agent_sdk.types import McpStdioServerConfig

    agent_credentials.set_key(KEY)
    for name in ("CLAUDE_SECURESTORAGE_CONFIG_DIR", "ANTHROPIC_CONFIG_DIR", "CLAUDE_CODE_ENTRYPOINT"):
        monkeypatch.setenv(name, "inherited")
    options = agent_host.build_options(tmp_path, chat._make_tool_ctx("video"))
    directory = str(config.RUNTIME_DIR / "claude-agent")
    assert options.env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == directory
    assert options.env["ANTHROPIC_CONFIG_DIR"] == directory
    assert options.env["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"
    assert isinstance(options.mcp_servers, dict)
    gpu = cast(McpStdioServerConfig, options.mcp_servers["gpu"])
    assert gpu.get("type") == "stdio"
    gpu_env = gpu.get("env")
    assert gpu_env is not None
    assert gpu_env["ANTHROPIC_API_KEY"] == ""


def test_connect_and_status_reads_are_serialized(monkeypatch):
    agent_credentials.set_key(KEY)
    original_open = secret_store.os.open
    original_replace = secret_store.os.replace
    reading = threading.Event()
    reader_started = threading.Event()
    collisions = []
    replacements = []

    def open_file(path, flags, *args, **kwargs):
        if str(path).endswith("anthropic-api-key"):
            reading.set()
            reader_started.set()
            time.sleep(0.01)
            try:
                return original_open(path, flags, *args, **kwargs)
            finally:
                reading.clear()
        return original_open(path, flags, *args, **kwargs)

    def replace_file(source, destination):
        replacements.append(destination)
        if reading.is_set():
            collisions.append(destination)
            raise PermissionError("secret is open for reading")
        return original_replace(source, destination)

    monkeypatch.setattr(secret_store.os, "open", open_file)
    monkeypatch.setattr(secret_store.os, "replace", replace_file)
    connection = agent_credentials.ClaudeConnection()
    connection.verify()
    reader_started.clear()
    stop = threading.Event()

    def poll():
        reader_connection = agent_credentials.ClaudeConnection()
        while not stop.is_set():
            reader_connection.status()
            stop.wait(0.001)

    with ThreadPoolExecutor(max_workers=4) as pool:
        readers = [pool.submit(poll) for _ in range(3)]
        try:
            assert reader_started.wait(5)
            for index in range(5):
                assert connection.connect(f"{KEY}-{index}")["state"] == "ready"
        finally:
            stop.set()
        for reader in readers:
            reader.result(timeout=10)
    assert replacements
    assert not collisions


def test_settings_refusal_classifies_as_auth():
    with pytest.raises(ValidationError) as failure:
        agent_credentials.require_key()
    assert agent_host._classify_error(failure.value) == "auth"
