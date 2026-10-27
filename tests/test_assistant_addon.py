"""Assistant installation, readiness and SDK isolation with synthetic packages."""
from __future__ import annotations

import importlib
import importlib.util
import asyncio
import json
import os
import runpy
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

import config
from installer import manifest
from pipeline import addons, agent_credentials, jobs, secret_store
from scripts import agent_host
from studio import helpers, state
from studio.routes import chat, events, system


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(addons, "_activated", {})
    monkeypatch.setattr(addons, "_threads", {})
    monkeypatch.setattr(addons, "_stop", threading.Event())
    monkeypatch.setattr(addons, "source_tier", lambda name: None)
    monkeypatch.setattr(jobs, "_registry", {})
    monkeypatch.setattr(sys, "path", list(sys.path))


def test_assistant_declaration_excludes_core_and_requires_terms(isolated, monkeypatch):
    addon = addons.definition("assistant", "standard")
    assert addon.lock == "requirements-agent.txt" and addon.index is None
    assert "Anthropic software" in addon.description
    assert ("Anthropic Commercial Terms", "https://www.anthropic.com/legal/commercial-terms") in addon.licenses
    assert ("Anthropic Usage Policy", "https://www.anthropic.com/legal/aup") in addon.licenses
    requirements = addons.install_requirements(addon.lock_path.read_text())
    assert "claude-agent-sdk==" in requirements and "mcp==" in requirements
    assert not addons.core_versions().keys() & {name for name, _, _ in addons._requirements(requirements)}
    monkeypatch.setattr(addons, "_pip", lambda *args: pytest.fail("must accept before downloading"))
    with pytest.raises(addons.AddonError, match="Accept"):
        addons.install("assistant", "standard", "", lambda _: None)
    assert not jobs.all_persisted()


@pytest.mark.parametrize("source", ["none", "saved", "environment"])
def test_health_exposes_only_presence_and_known_verification(isolated, monkeypatch, source):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    synthetic_key = "synthetic-assistant-key-value"
    if source == "saved":
        secret_store.set("anthropic-api-key", synthetic_key)
    monkeypatch.setattr(secret_store, "get", lambda name: pytest.fail("health must not decrypt keys"))
    if source == "environment":
        monkeypatch.setenv("ANTHROPIC_API_KEY", synthetic_key)
    connection = agent_credentials.ClaudeConnection()
    monkeypatch.setattr(state, "_claude_connection", connection)
    monkeypatch.setattr(agent_credentials, "_new_client", lambda: pytest.fail("health must stay offline"))
    result = system.health()
    assistant = result["capabilities"]["assistant"]
    assert assistant["keySource"] == source
    assert assistant["verification"] == ("not_connected" if source == "none" else "unknown")
    assert assistant["state"] == "not installed" and not assistant["available"]
    assert synthetic_key not in json.dumps(result)
    assert "claudeProvider" not in result["capabilities"]
    assert str(config.STATE_HOME) not in json.dumps(result)


@pytest.mark.parametrize("installed,connected,verification,code,message", [
    (False, False, "not_connected", "assistant_not_installed", "Assistant isn't installed"),
    (True, False, "not_connected", "authentication_required", "Connect your key"),
    (True, True, "unknown", "authentication_required", "Key not verified"),
    (True, True, "checking", "authentication_checking", "Verifying your key"),
    (True, True, "error", "authentication_required", "Key not verified"),
    (True, True, "ready", None, None),
])
def test_provider_readiness(isolated, monkeypatch, installed, connected, verification, code, message):
    monkeypatch.setattr(addons, "available", lambda name: installed)
    monkeypatch.setattr(state._claude_connection, "status", lambda **_: {"connected": connected, "state": verification})
    provider = chat._provider_registry().providers[0]
    assert provider.availability.available == (installed and verification == "ready")
    assert provider.availability.error_code == code
    if message:
        assert message in (provider.availability.error_message or "")


@pytest.mark.parametrize("source", ["saved", "environment"])
@pytest.mark.parametrize("installed,presence_only", [(False, False), (False, True), (True, True)])
def test_connection_presence_never_verifies_without_available_assistant(
    isolated, monkeypatch, source, installed, presence_only,
):
    from scripts import studio_app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key = "synthetic-assistant-api-key"
    if source == "saved":
        secret_store.set("anthropic-api-key", key)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    if installed:
        monkeypatch.setattr(addons, "available", lambda _: True)
    else:
        assert addons.active("assistant") is None
        assert not addons.available("assistant")
    connection = agent_credentials.ClaudeConnection()
    monkeypatch.setattr(state, "_claude_connection", connection)
    requests = []

    def refuse(request):
        requests.append(request)
        pytest.fail("presence must not make an HTTP request")

    monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(refuse)))
    monkeypatch.setattr(secret_store, "get", lambda _: pytest.fail("presence must not decrypt a saved key"))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app),
                                     base_url="http://studio.test") as client:
            response = await client.get("/api/agent/connection" + ("?verify=0" if presence_only else ""))
            assert response.status_code == 200
            assert response.json() == {"connected": True, "state": "unknown",
                                       "source": "settings" if source == "saved" else "environment"}
            response = await client.get("/api/chat/config?verify=0")
            assert response.status_code == 200
            if not installed:
                response = await client.post("/api/agent/connection/verify", json={})
                assert response.json()["state"] == "unknown"

    asyncio.run(scenario())
    assert not requests


@pytest.mark.parametrize("key", ["short", "synthetic-assistant-api-key\n"])
def test_invalid_environment_key_is_not_connected(isolated, monkeypatch, key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    monkeypatch.setattr(agent_credentials, "_new_client", lambda: pytest.fail("invalid keys must stay offline"))
    connection = agent_credentials.ClaudeConnection()
    expected = {"connected": False, "state": "not_connected", "source": "environment"}
    assert connection.snapshot() == expected
    monkeypatch.setattr(addons, "available", lambda _: True)
    assert connection.status() == expected
    assert connection.verify() == expected


@pytest.mark.parametrize("mode", ["default", "plan"])
def test_addon_sdk_uses_its_own_cli_and_preserves_isolation(isolated, monkeypatch, mode):
    addon = addons.definition("assistant", "standard")

    def fake_install(_addon, stage, *args):
        metadata = stage / "claude_agent_sdk-1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Name: claude-agent-sdk\nVersion: 1.0\n")
        package = stage / "claude_agent_sdk"
        package.mkdir()
        (package / "__init__.py").write_text("from types import SimpleNamespace\nClaudeAgentOptions = SimpleNamespace\n")
        (package / "_bundled").mkdir()
        (package / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")).write_bytes(b"synthetic")

    monkeypatch.setattr(addons, "_pip", fake_install)
    job_id = jobs.new_job("addon_install", "addon-assistant", {"addon": "assistant", "tier": "standard"})
    addons._run(addon, addon.acceptance(), job_id, lambda _: None)
    assert addons.status("assistant")["restartRequired"]
    assert not addons.available("assistant")
    record = addons.active("assistant")
    assert record is not None
    directory = addons._root("assistant") / record["directory"]
    addons.activate()
    assert addons.available("assistant")
    # This process's development SDK is absent from an installed core runtime.
    original = importlib.util.find_spec("claude_agent_sdk")
    assert original is not None and original.origin is not None
    existing_site = str(Path(original.origin).parents[1])
    sys.path[:] = [path for path in sys.path if path != existing_site]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    del sys.modules["claude_agent_sdk"]
    sdk = importlib.import_module("claude_agent_sdk")
    assert sdk.__file__ is not None
    assert Path(sdk.__file__).is_relative_to(directory)
    monkeypatch.setattr(agent_host, "build_editor_server", lambda ctx: {})
    monkeypatch.setenv("SERVICE_TOKEN", "synthetic-other-secret")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "inherited-config")
    ctx = agent_host.ToolCtx("video", lambda: (None, 0), lambda: None, lambda: 30, lambda _: None,
                             settings=agent_host.AgentSettings(mode=mode))
    options = agent_host.build_options(config.STATE_HOME / "video", ctx, "synthetic-api-key-value")
    assert isinstance(options.cli_path, str)
    assert Path(options.cli_path).parent == directory / "claude_agent_sdk" / "_bundled"
    assert options.tools == [] and options.setting_sources == [] and options.strict_mcp_config
    assert options.disallowed_tools == list(agent_host._BUILTIN_TOOLS)
    assert options.env["SERVICE_TOKEN"] == "" and options.env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert Path(options.env["CLAUDE_CONFIG_DIR"]).is_relative_to(config.STATE_HOME)
    assert options.env["ANTHROPIC_API_KEY"] == "synthetic-api-key-value"
    assert vars(options)["mcp_servers"]["gpu"]["env"]["CUTGRAPH_STATE_HOME"] == str(config.STATE_HOME)
    assert os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] == "1"
    assert "Read" not in options.allowed_tools and "Bash" not in options.allowed_tools
    Path(options.cli_path).unlink()
    assert not addons.available("assistant")
    assert "Reinstall the assistant" in addons.unavailable_message("assistant")
    readiness = chat._provider_registry().providers[0].availability
    assert readiness.error_code == "assistant_not_installed"
    assert "Reinstall the assistant" in (readiness.error_message or "")
    with pytest.raises(RuntimeError, match="Reinstall"):
        agent_host._bundled_cli()


@pytest.mark.parametrize("relative", [
    "python/Lib/site-packages/claude_agent_sdk/__init__.py",
    "python/Lib/site-packages/claude_agent_sdk-1.0.dist-info/METADATA",
    "python/Lib/site-packages/mcp/__init__.py",
    "python/Lib/site-packages/mcp-1.0.dist-info/METADATA",
    "app/bin/claude.exe", "app/bin/claude",
    "app/tools/claude-code", "python/tools/claude-code.exe",
    "app/tools/claude.cmd", "app/tools/claude-code.ps1",
    "app/motion-core/node_modules/@anthropic-ai/claude-code/cli.js",
    "app/motion-core/node_modules/@anthropic-ai/claude-agent-sdk/cli.js",
    "python/Lib/site-packages/claude_code_cli/bin/claude-code.exe",
    "python/Lib/site-packages/Claude.Agent.SDK-1.0.dist-info/METADATA",
    "python/Lib/site-packages/MCP-1.0.dist-info/METADATA",
    "python/Lib/site-packages/_claude_agent_sdk.pth",
    "python/Lib/site-packages/__editable___claude_agent_sdk_finder.py",
    "python/Lib/site-packages/unrelated.pth",
    "python/Lib/site-packages/__editable___other_finder.py",
])
def test_bundle_manifest_refuses_assistant_redistribution(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="Forbidden"):
        manifest.inventory(tmp_path, {relative: "pinned input:requirements-core.txt"})


def test_acceptance_covers_description():
    addon = addons.definition("assistant", "standard")
    assert replace(addon, description=addon.description + " Revised terms.").acceptance() != addon.acceptance()


def test_real_core_locks_exclude_assistant():
    core = addons.core_versions()
    assert not {"claude-agent-sdk", "mcp", "claude-code"} & core.keys()
    for package in ("ui", "motion-core"):
        lock = json.loads((config.CODE_ROOT / package / "package-lock.json").read_text())
        for name, metadata in lock["packages"].items():
            assert "@anthropic-ai/" not in name
            assert not {"claude-agent-sdk", "mcp", "claude-code"} & set(name.split("/"))
            assert metadata.get("name") not in {"claude-agent-sdk", "mcp", "claude-code"}


@pytest.mark.parametrize("name", ["claude_agent_sdk", "MCP", "claude-code-cli"])
def test_bundle_rejects_renamed_distribution_by_metadata(tmp_path, name):
    relative = "python/Lib/site-packages/other-1.0.dist-info/METADATA"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(f"Name: {name}\nVersion: 1.0\n")
    with pytest.raises(ValueError, match="Forbidden bundle distribution"):
        manifest.inventory(tmp_path, {relative: "pinned input:requirements-core.txt"})


@pytest.mark.parametrize("module", ["scripts.agent_host", "scripts.mcp_gpu", "studio.errors", "studio.routes.chat", "studio.routes.cast"])
def test_import_after_install_keeps_restart_required(isolated, monkeypatch, module):
    addon = addons.definition("assistant", "standard")
    addons.activate()
    def pip(_addon, stage, *args):
        metadata = stage / "synthetic-1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Name: synthetic\nVersion: 1.0\n")

    monkeypatch.setattr(addons, "_pip", pip)
    job = jobs.new_job("addon_install", "addon-assistant", {"addon": "assistant", "tier": "standard"})
    addons._run(addon, addon.acceptance(), job, lambda _: None)
    assert addons.status("assistant")["restartRequired"]
    monkeypatch.setattr(addons.models, "configure_caches", lambda: pytest.fail("import must not activate add-ons"))
    spec = importlib.util.find_spec(module)
    assert spec and spec.origin
    runpy.run_path(spec.origin, run_name="import_regression")
    assert addons.status("assistant")["restartRequired"]
    assert not addons.available("assistant")


def test_health_reuses_verified_metadata_without_decrypting(isolated, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(addons, "available", lambda _: True)
    connection = agent_credentials.ClaudeConnection()
    monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))))
    assert connection.connect("synthetic-assistant-api-key")["state"] == "ready"
    monkeypatch.setattr(state, "_claude_connection", connection)
    monkeypatch.setattr(secret_store, "get", lambda _: pytest.fail("cached health must not decrypt"))
    for _ in range(5):
        assert system.health()["capabilities"]["assistant"]["verification"] == "ready"
    secret_store.set("anthropic-api-key", "synthetic-replacement-api-key")
    assert connection.snapshot()["state"] == "unknown"


@pytest.mark.parametrize("operation", ["draft", "describe"])
def test_oneshot_uses_bundled_cli_and_skips_unfiltered_version_probe(isolated, monkeypatch, tmp_path, operation):
    from types import SimpleNamespace

    package = tmp_path / "sdk" / "claude_agent_sdk"
    binary = package / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"bundled")
    decoys = tmp_path / "profile"
    for directory in (decoys / "bin", decoys / ".local" / "bin", decoys / ".claude" / "local"):
        directory.mkdir(parents=True)
        (directory / binary.name).write_bytes(b"decoy")
    monkeypatch.setenv("PATH", str(decoys / "bin"))
    monkeypatch.setenv("USERPROFILE", str(decoys))
    monkeypatch.setenv("HOME", str(decoys))
    monkeypatch.setenv("SERVICE_TOKEN", "synthetic-other-secret")
    monkeypatch.delenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", raising=False)
    captured = []

    async def query(*, prompt, options):
        captured.append(options)
        assert os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] == "1"
        yield SimpleNamespace(is_error=False, structured_output={"synthetic": True})

    sdk = SimpleNamespace(__file__=str(package / "__init__.py"), ClaudeAgentOptions=SimpleNamespace,
                          ResultMessage=SimpleNamespace, query=query)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    if operation == "draft":
        asyncio.run(agent_host._draft_by_copilot("idea", None))
    else:
        monkeypatch.setattr(agent_host.cast_mod, "RIGS", {"synthetic": {"parts": {"mouth": ["open"]}, "expressions": []}})
        asyncio.run(agent_host._describe_by_copilot("happy", "synthetic"))
    assert captured[0].cli_path == str(binary)
    assert captured[0].env["SERVICE_TOKEN"] == ""
    assert Path(captured[0].env["CLAUDE_CONFIG_DIR"]).is_relative_to(config.STATE_HOME)
    binary.unlink()
    with pytest.raises(addons.AssistantUnavailable):
        agent_host._bundled_cli()
    assert len(captured) == 1


def test_missing_cli_is_installation_error():
    class CLINotFoundError(RuntimeError):
        pass

    from studio.errors import agent_error_data

    assert agent_host._classify_error(CLINotFoundError("missing")) == "assistant_not_installed"
    assert "Reinstall the assistant" in agent_error_data({"kind": "assistant_not_installed"})["message"]


def test_source_readiness_requires_bundled_cli(monkeypatch, tmp_path):
    from types import SimpleNamespace

    addon = addons.definition("assistant", "standard")
    package = tmp_path / "claude_agent_sdk"
    package.mkdir()
    monkeypatch.setattr(addons, "active", lambda _: None)
    monkeypatch.setattr(addons, "_activated", {})
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: SimpleNamespace(origin=str(package / "__init__.py")))
    assert addons.source_tier("assistant") is None
    assert not addons.available("assistant")
    assert "Reinstall the assistant" in addons.unavailable_message("assistant")
    cli = tmp_path / addon.probe_files[0]
    cli.parent.mkdir()
    cli.write_bytes(b"synthetic")
    assert addons.source_tier("assistant") == "standard"


def test_addon_events_share_one_catalog_channel(isolated, monkeypatch):
    from studio import compute

    events = []
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda project, event: events.append((project, event)))
    for name in ("transcription", "assistant"):
        job = jobs.new_job("addon_install", f"addon-{name}", {"addon": name})
        compute._emit_job_update_threadsafe(f"addon-{name}", job)
    assert helpers._safe(state.ADDON_EVENTS_CHANNEL) is None
    assert len(events) == 2
    assert all(project == state.ADDON_EVENTS_CHANNEL for project, _ in events)
    assert {event["id"] for _, event in events} == set(jobs._registry)


@pytest.mark.parametrize("crash_status,recovered_status", [("queued", "interrupted"), ("running", "error")])
def test_reserved_addon_jobs_cancel_and_recover_on_one_channel(isolated, monkeypatch, crash_status, recovered_status):
    from studio import compute
    from studio.routes import addons as addon_routes
    from studio.routes import jobs as job_routes

    class QueuedThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(addons.threading, "Thread", QueuedThread)
    updates = []
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda project, event: updates.append((project, event)))
    addon = addons.definition("assistant", "standard")
    first = addons.install(addon.name, addon.tier, addon.acceptance(), addon_routes._notify)
    job = jobs.get(first)
    assert job and job["project"] == state.ADDON_EVENTS_CHANNEL
    assert helpers._safe(job["project"]) is None
    response = asyncio.run(job_routes.api_cancel_job(first))
    assert response.status_code == 200
    assert json.loads(bytes(response.body))["status"] == "cancelled"
    addons._threads.clear()
    second = addons.install(addon.name, addon.tier, addon.acceptance(), addon_routes._notify, retry=True)
    addons._threads.clear()
    if crash_status == "running":
        assert jobs.compare_and_set_status(second, expected={"queued"}, status="running")[1]
    jobs._registry.clear()
    addons.startup()
    jobs.recover_jobs()
    recovered = jobs.get(second)
    assert recovered and recovered["project"] == state.ADDON_EVENTS_CHANNEL
    assert recovered["status"] == recovered_status
    if crash_status == "running":
        assert recovered["code"] == "ADDON_INTERRUPTED"
    else:
        assert recovered["recoveryReason"] == "application_restarted_before_execution"
    assert recovered["parentJobId"] == first
    addon_routes._notify(second)
    assert [(channel, event["id"], event["status"]) for channel, event in updates] == [
        (state.ADDON_EVENTS_CHANNEL, first, "queued"),
        (state.ADDON_EVENTS_CHANNEL, first, "cancelled"),
        (state.ADDON_EVENTS_CHANNEL, second, "queued"),
        (state.ADDON_EVENTS_CHANNEL, second, recovered_status),
    ]


@pytest.mark.parametrize("operation", ["draft", "describe"])
def test_unavailable_assistant_gates_oneshots_before_credentials_or_sdk(isolated, monkeypatch, operation):
    monkeypatch.setattr(addons, "available", lambda _: False)
    monkeypatch.setattr(agent_credentials, "resolve_key", lambda: pytest.fail("unavailable assistant must not read keys"))

    async def forbidden(*args):
        pytest.fail("unavailable assistant must not invoke the SDK")

    monkeypatch.setattr(agent_host, f"_{operation}_by_copilot", forbidden)
    if operation == "draft":
        with pytest.raises(agent_host.ScriptDraftUnavailable, match="Assistant isn't installed"):
            asyncio.run(agent_host.draft_script("A short cooking lesson"))
    else:
        assert asyncio.run(agent_host.describe_expression("happy", "lens"))["source"] == "keywords"


def test_addon_event_route_uses_reserved_channel_without_aliasing_video(isolated):
    from starlette.requests import Request

    async def scenario():
        request = Request({"type": "http"})
        video_queue = state.hub.subscribe("addons")
        response = await events.api_events(request, state.ADDON_EVENTS_CHANNEL)
        stream = getattr(response, "body_iterator")
        try:
            state.hub.publish("addons", 1)
            state.hub.publish_event(state.ADDON_EVENTS_CHANNEL, {"type": "job", "id": "download"})
            event = await asyncio.wait_for(anext(stream), 5)
            assert json.loads(event["data"]) == {"type": "job", "id": "download"}
            assert video_queue.qsize() == 1
            assert (await video_queue.get())["type"] == "project"
        finally:
            await stream.aclose()
            state.hub.unsubscribe("addons", video_queue)
        rejected = await events.api_events(request, "@other")
        assert rejected.status_code == 404

    asyncio.run(scenario())


def test_probe_files_gate_unavailability_for_any_addon(isolated, monkeypatch, tmp_path):
    addon = replace(addons.definition("assistant", "standard"), name="optional")
    monkeypatch.setattr(addons, "definitions", lambda: (addon,))
    monkeypatch.setattr(addons, "active", lambda _: None)
    monkeypatch.setattr(addons, "_source_directory", lambda _: tmp_path)
    assert addons.unavailable_message("optional") == addons.ASSISTANT_INCOMPLETE


def test_activate_only_selected_addons(isolated, monkeypatch):
    visited = []
    monkeypatch.setattr(addons, "active", lambda name: visited.append(name))
    addons.activate("assistant")
    assert visited == ["assistant"]
    visited.clear()
    addons.activate()
    assert visited == ["transcription", "assistant"]


def test_changed_declaration_disables_active_assistant(isolated, monkeypatch):
    addon = addons.definition("assistant", "standard")
    record = {"tier": "standard", "directory": "a" * 16, "lockSha256": addon.digest,
              "acceptance": addon.acceptance()}
    monkeypatch.setattr(addons, "active", lambda _: record)
    monkeypatch.setattr(addons, "_compatible", lambda _: True)
    monkeypatch.setattr(addons, "_probe_files", lambda *args: True)
    monkeypatch.setitem(addons._activated, "assistant", record["directory"])
    assert addons.available("assistant")
    revised = replace(addon, description=addon.description + " Revised terms.")
    monkeypatch.setattr(addons, "definitions", lambda: (revised,))
    assert not addons.available("assistant")
    assert "needs an update" in addons.unavailable_message("assistant")
    monkeypatch.setattr(config, "CODE_ROOT", config.STATE_HOME)
    assert not addons.available("assistant")
    assert "needs an update" in addons.unavailable_message("assistant")


def test_compute_child_activates_synthetic_addon_with_core_mcp_absent(isolated, monkeypatch, tmp_path):
    addon = addons.definition("assistant", "standard")

    def pip(_addon, stage, *args):
        metadata = stage / "mcp-1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Name: mcp\nVersion: 1.0\n")
        package = stage / "mcp" / "server"
        package.mkdir(parents=True)
        (package.parent / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "fastmcp.py").write_text(
            "import json\nfrom pathlib import Path\nimport config\n"
            "class FastMCP:\n"
            " def __init__(self, name): pass\n"
            " def tool(self, *args, **kwargs): return lambda fn: fn\n"
            " def run(self, *args, **kwargs):\n"
            "  print(json.dumps({'state': str(config.STATE_HOME), 'module': __file__}))\n")

    monkeypatch.setattr(addons, "_pip", pip)
    job = jobs.new_job("addon_install", "addon-assistant", {"addon": "assistant", "tier": "standard"})
    addons._run(addon, addon.acceptance(), job, lambda _: None)
    runner = tmp_path / "child.py"
    runner.write_text(
        "import importlib.abc, importlib.machinery, runpy, sys\n"
        "from pathlib import Path\n"
        "class CoreWithoutMcp(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self, fullname, path=None, target=None):\n"
        "  if fullname == 'mcp':\n"
        "   candidates = [p for p in sys.path if 'addons' in Path(p).parts]\n"
        "   spec = importlib.machinery.PathFinder.find_spec(fullname, candidates)\n"
        "   if spec is None: raise ModuleNotFoundError('core has no mcp')\n"
        "   return spec\n"
        "sys.meta_path.insert(0, CoreWithoutMcp())\n"
        "sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[1]))\n"
        "from pipeline import addons\n"
        "activate = addons.activate\n"
        "def activate_assistant(*names):\n"
        " assert names == ('assistant',)\n"
        " activate(*names)\n"
        "addons.activate = activate_assistant\n"
        "runpy.run_path(sys.argv[1], run_name='__main__')\n")
    environment = {name: value for name, value in os.environ.items()
                   if name in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
    environment.update(CUTGRAPH_STATE_HOME=str(config.STATE_HOME), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-I", str(runner), str(config.CODE_ROOT / "scripts" / "mcp_gpu.py")],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["state"] == str(config.STATE_HOME)
    assert Path(output["module"]).is_relative_to(config.STATE_HOME / "addons" / "assistant")
