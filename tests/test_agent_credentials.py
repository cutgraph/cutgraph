from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import threading
import sys
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest

import config
from pipeline import secret_store
from scripts import agent_host, studio_app
from studio.routes import chat
from scripts.public_markdown import reachable_files

from pipeline import agent_credentials as _credentials

# Captured at collection, before the root conftest replaces the client factory for every test.
_REAL_NEW_CLIENT = _credentials._new_client


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "CHAT_DIR", tmp_path / "chat")


def test_no_key_disables_chat_and_sdk(monkeypatch):
    from pipeline import project

    project.write_project(config.PROJECTS_DIR / "video.json", project.Project(name="video"))
    monkeypatch.setattr(chat, "_chat_host", lambda: pytest.fail("must refuse before creating a host"))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            status = await client.get("/api/agent/connection")
            assert status.json() == {"connected": False, "state": "not_connected", "source": None}
            response = await client.post("/api/chat/video", json={"text": "hello"})
            assert response.status_code == 409
            assert response.json()["code"] == "authentication_required"
            assert "Settings" in response.json()["error"]
        with pytest.raises(ValueError, match="Settings"):
            await agent_host.SdkAgentSession("video", chat._make_tool_ctx("video")).aconnect()
        with pytest.raises(ValueError, match="Settings"):
            await agent_host._draft_by_copilot("an idea", None)
        with pytest.raises(ValueError, match="Settings"):
            await agent_host._describe_by_copilot("happy", "narrator")

    asyncio.run(scenario())


@pytest.mark.parametrize("override", [False, True])
def test_key_reaches_isolated_sdk_environment(tmp_path, monkeypatch, override):
    secret_store.set("anthropic-api-key", "stored-test-secret-value")
    expected = "stored-test-secret-value"
    if override:
        expected = "override-test-secret"
        monkeypatch.setenv("ANTHROPIC_API_KEY", expected)
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.setenv(name, "inherited-value")
    options = agent_host.build_options(tmp_path, chat._make_tool_ctx("video"))
    assert options.env["ANTHROPIC_API_KEY"] == expected
    directory = Path(options.env["CLAUDE_CONFIG_DIR"])
    assert directory == config.RUNTIME_DIR / "claude-agent"
    assert directory.is_dir()
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        assert options.env[name] == ""


def test_sdk_configuration_directory_is_private():
    from pipeline import agent_credentials

    secret_store.set("anthropic-api-key", "test-api-secret-value")
    path = Path(agent_credentials.sdk_environment()["CLAUDE_CONFIG_DIR"])
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o700
        return
    security = getattr(ctypes, "WinDLL")("advapi32", use_last_error=True)
    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    get_security = security.GetFileSecurityW
    get_security.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p,
                             ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    get_security.restype = ctypes.c_int
    size = ctypes.c_uint32()
    get_security(str(path), 4, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    assert get_security(str(path), 4, buffer, size.value, ctypes.byref(size))
    convert = security.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_void_p]
    convert.restype = ctypes.c_int
    descriptor = ctypes.c_wchar_p()
    assert convert(buffer, 1, 4, ctypes.byref(descriptor), None)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        assert descriptor.value == "D:P(A;OICI;FA;;;OW)"
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.parametrize("outcome,expected,message", [
    (200, "ready", None), (401, "error", "That API key was rejected."),
    (403, "error", "That API key was rejected."),
    ("network", "error", "Couldn't reach Anthropic."),
])
def test_connection_routes_verify_without_disclosing_key(monkeypatch, caplog, outcome, expected, message):
    from pipeline import agent_credentials

    key = "test-api-secret-value"
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url == "https://api.anthropic.com/v1/models"
        assert request.headers["x-api-key"] == key
        assert request.headers["anthropic-version"] == "2023-06-01"
        if outcome == "network":
            raise httpx.ConnectError(key, request=request)
        return httpx.Response(outcome, text=key)

    monkeypatch.setattr(agent_credentials, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    caplog.set_level(logging.DEBUG)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            for response in (
                await client.post("/api/agent/connection", json={"key": key}),
                await client.get("/api/agent/connection"),
                await client.post("/api/agent/connection/verify"),
            ):
                assert response.status_code == 200
                assert response.json()["state"] == expected
                assert response.json().get("message") == message
                assert key not in response.text
            chat._make_tool_ctx("video").emit({"type": "agent_error", "kind": "runtime", "message": key})
            log = await client.get("/api/chat/video/log")
            assert key not in log.text
            response = await client.delete("/api/agent/connection")
            assert response.json() == {"connected": False, "state": "not_connected", "source": None}

    asyncio.run(scenario())
    assert len(requests) == (1 if outcome in (401, 403) else 2)
    assert key not in caplog.text


@pytest.mark.parametrize("value", ["", "has space", "line\n", "é", "x" * 513, None, 12])
def test_invalid_key_is_not_stored(value):
    from pipeline import agent_credentials

    with pytest.raises(ValueError):
        agent_credentials.set_key(value)
    assert secret_store.get("anthropic-api-key") is None


def test_product_has_no_cli_login_or_cli_branding():
    # Walks the tree and its links rather than asking git, so an archive without a checkout is scanned too.
    root = Path(__file__).resolve().parents[1]
    paths = []
    for folder in ("scripts", "pipeline", "studio", "ui/src"):
        paths.extend(path for path in (root / folder).rglob("*") if path.suffix in {".py", ".ts", ".tsx", ".md"})
    paths.extend((root / "motion-core").glob("*.md"))
    paths.extend(root / name for name in reachable_files(root) if name.endswith(".md"))
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in ("claude /login", "claude auth", "claude code", "claude login", "signed-in claude"):
            assert forbidden not in text, path.relative_to(root)


def test_copy_scan_needs_no_git_checkout(monkeypatch):
    import subprocess

    def no_git(*_args, **_kwargs):
        raise AssertionError("the copy scan started a process")

    monkeypatch.setattr(subprocess, "Popen", no_git)
    test_product_has_no_cli_login_or_cli_branding()


@pytest.mark.parametrize("name", ["SECURITY.md", "docs/getting-started.md", "docs/privacy.md",
                                  "pipeline/prompts/copilot.md", "motion-core/CHARACTERS.md"])
def test_copy_scan_checks_public_markdown(monkeypatch, name):
    root = Path(__file__).resolve().parents[1]
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == root / name:
            return original(path, *args, **kwargs) + "\nclaude /login"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(AssertionError, match=name):
        test_product_has_no_cli_login_or_cli_branding()


def test_checking_state_and_disconnect_ignore_inflight_verification(monkeypatch):
    from pipeline import agent_credentials

    connection = agent_credentials.ClaudeConnection()
    started = threading.Event()
    finish = threading.Event()
    secret_store.set("anthropic-api-key", "test-api-secret-value")

    def respond(request):
        started.set()
        assert finish.wait(5)
        return httpx.Response(200)

    monkeypatch.setattr(agent_credentials, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    thread = threading.Thread(target=connection.verify)
    thread.start()
    try:
        assert started.wait(5)
        assert connection.status() == {"connected": True, "state": "checking", "source": "settings"}
        assert connection.disconnect() == {"connected": False, "state": "not_connected", "source": None}
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert connection.status() == {"connected": False, "state": "not_connected", "source": None}


def test_failed_turn_does_not_leak_key(monkeypatch, caplog):
    from pipeline import agent_credentials, project
    from pipeline.agent_session import FakeAgentSession
    from studio import state

    secret_store.set("anthropic-api-key", "test-api-secret-value")
    agent_credentials.resolve_key()
    state._claude_connection.verify()
    project.write_project(config.PROJECTS_DIR / "video.json", project.Project(name="video"))
    caplog.set_level(logging.DEBUG)

    def fail_turn(_text):
        raise RuntimeError("test-api-secret-value")

    host = agent_host.AgentHost(make_session=lambda _project, _ctx: FakeAgentSession(script=fail_turn),
                                make_ctx=chat._make_tool_ctx)
    monkeypatch.setattr(state, "_agent_host_override", host)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            queue = state.hub.subscribe("video")
            try:
                response = await client.post("/api/chat/video", json={"text": "hello"})
                assert response.json() == {"ok": True}
                events = []
                while not any(event["type"] == "turn_done" for event in events):
                    events.append(await asyncio.wait_for(queue.get(), 5))
                assert any(event["type"] == "agent_error" for event in events)
                assert "test-api-secret-value" not in str(events)
                log = await client.get("/api/chat/video/log")
                assert "test-api-secret-value" not in log.text
            finally:
                state.hub.unsubscribe("video", queue)
                await host.aclose_all()

    asyncio.run(scenario())
    assert "test-api-secret-value" not in caplog.text


def test_environment_override_wins_and_disconnect_is_refused(monkeypatch):
    from pipeline import agent_credentials

    secret_store.set("anthropic-api-key", "stored-test-secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "override-test-secret")
    connection = agent_credentials.ClaudeConnection()
    with pytest.raises(ValueError, match="takes precedence"):
        connection.disconnect()
    assert agent_credentials.require_key() == "override-test-secret"
    assert secret_store.get("anthropic-api-key") == "stored-test-secret-value"


def test_warm_sdk_session_rebuilds_for_replaced_key_and_refuses_after_disconnect(monkeypatch):
    clients = []

    class Client:
        def __init__(self, options):
            self.env = options.env
            self.closed = False
            self.turns = []
            clients.append(self)

        async def connect(self, _prompt):
            pass

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
    monkeypatch.setattr(agent_host, "build_options",
                        lambda _directory, _ctx, key: SimpleNamespace(env=agent_host._sdk_environment(key)))
    session = agent_host.SdkAgentSession("video", chat._make_tool_ctx("video"))

    async def emit(_event):
        pass

    async def scenario():
        secret_store.set("anthropic-api-key", "first-test-secret-value")
        await session.aconnect()
        await session.send_turn("first", emit)
        secret_store.set("anthropic-api-key", "second-test-secret-value")
        await session.send_turn("second", emit)
        secret_store.delete("anthropic-api-key")
        with pytest.raises(ValueError, match="Settings"):
            await session.send_turn("third", emit)
        await session.aclose()

    asyncio.run(scenario())
    assert len(clients) == 2
    assert clients[0].env["ANTHROPIC_API_KEY"] == "first-test-secret-value"
    assert clients[1].env["ANTHROPIC_API_KEY"] == "second-test-secret-value"
    assert clients[0].turns == ["first"]
    assert clients[1].turns == ["second"]
    assert all(client.closed for client in clients)


def _proxy_environment(monkeypatch, env: dict[str, str]) -> None:
    for name in list(os.environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("env,expected", [
    ({"HTTPS_PROXY": "http://proxy.test:8080"}, "http://proxy.test:8080"),
    ({"HTTP_PROXY": "http://plain.test:3128"}, "http://plain.test:3128"),
    ({"HTTPS_PROXY": "http://proxy.test:8080", "HTTP_PROXY": "http://plain.test:3128"}, "http://proxy.test:8080"),
    ({"HTTPS_PROXY": "http://proxy.test:8080", "NO_PROXY": "api.anthropic.com"}, None),
    ({"HTTPS_PROXY": "http://proxy.test:8080", "NO_PROXY": ".anthropic.com"}, None),
    ({}, None),
])
def test_verify_takes_the_environment_proxy_without_trusting_the_environment(monkeypatch, env, expected):
    from pipeline import agent_credentials

    _proxy_environment(monkeypatch, env)
    built = []
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: built.append(kwargs) or SimpleNamespace())
    assert agent_credentials._api_proxy() == expected
    _REAL_NEW_CLIENT()
    assert built == [{"timeout": 5.0, "trust_env": False, "follow_redirects": False, "proxy": expected}]


@pytest.mark.parametrize("proxy", ["ftp://proxy.test:21", "not a url"])
def test_unusable_proxy_reports_unreachable_instead_of_stalling(monkeypatch, proxy):
    from pipeline import agent_credentials

    _proxy_environment(monkeypatch, {"HTTPS_PROXY": proxy})
    monkeypatch.setattr(agent_credentials, "_new_client", _REAL_NEW_CLIENT)
    secret_store.set("anthropic-api-key", "test-api-secret-value")
    connection = agent_credentials.ClaudeConnection()
    status = connection.verify()
    assert status["state"] == "error" and status["message"] == "Couldn't reach Anthropic."
    assert "test-api-secret-value" not in str(status)
    assert connection.status()["state"] == "error"
