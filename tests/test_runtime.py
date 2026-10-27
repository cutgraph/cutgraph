"""Standalone runtime identity and state-home contract."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

import config
from pipeline import runtime_tools
from runtime import default_state_home, resolve_runtime
from scripts import studio_app
from studio.routes import system


def test_explicit_local_runtime_keeps_code_and_state_separate(tmp_path: Path) -> None:
    code_root = tmp_path / "checkout"
    state_home = tmp_path / "operator-state"
    paths = resolve_runtime(code_root, {
        "CUTGRAPH_STATE_HOME": str(state_home),
    })

    assert paths.code_root == code_root.resolve()
    assert paths.state_home == state_home.resolve()
    assert paths.code_root != paths.state_home


def test_runtime_defaults_to_local_app_data(tmp_path: Path) -> None:
    paths = resolve_runtime(
        tmp_path / "checkout", {"LOCALAPPDATA": str(tmp_path / "AppData")}, platform="win32",
    )

    assert paths.state_home == (tmp_path / "AppData" / "Cutgraph Studio").resolve()


@pytest.mark.parametrize("platform,relative", [
    ("win32", "AppData/Local/Cutgraph Studio"),
    ("linux", ".local/state/cutgraph-studio"),
    ("darwin", "Library/Application Support/Cutgraph Studio"),
])
def test_state_home_platform_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform, relative) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert default_state_home({}, platform=platform) == tmp_path / relative


@pytest.mark.parametrize("xdg_state", ["", "relative/state", None])
def test_linux_ignores_missing_or_relative_xdg_state_home(monkeypatch, tmp_path: Path, xdg_state) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    env = {"LOCALAPPDATA": str(tmp_path / "windows")}
    if xdg_state is not None:
        env["XDG_STATE_HOME"] = xdg_state
    assert default_state_home(env, platform="linux") == tmp_path / ".local/state/cutgraph-studio"


def test_linux_uses_xdg_state_home(tmp_path: Path) -> None:
    paths = resolve_runtime(tmp_path / "checkout", {"XDG_STATE_HOME": str(tmp_path / "state")}, platform="linux")
    assert paths.state_home == (tmp_path / "state/cutgraph-studio").resolve()


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_explicit_state_home_takes_precedence_on_every_platform(tmp_path: Path, platform: str) -> None:
    paths = resolve_runtime(tmp_path, {
        "CUTGRAPH_STATE_HOME": str(tmp_path / "explicit"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "LOCALAPPDATA": str(tmp_path / "windows"),
    }, platform=platform)
    assert paths.state_home == (tmp_path / "explicit").resolve()



def _health_response() -> httpx.Response:
    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=studio_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
            return await client.get("/api/health")

    return asyncio.run(scenario())


@pytest.mark.parametrize("packaged", [False, True])
def test_health_reports_only_safe_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, packaged: bool,
) -> None:
    private_path = tmp_path / "private-probe-token"
    monkeypatch.setattr(config, "STATE_HOME", private_path)
    monkeypatch.setenv("PEXELS_API_KEY", "secret-probe-token")
    monkeypatch.setattr(runtime_tools, "bundle_root", lambda: private_path if packaged else None)
    monkeypatch.setattr(system.addons, "bundle_root", lambda: None)
    monkeypatch.setattr(runtime_tools, "tool_path", lambda name: str(private_path / f"{name}.exe"))
    monkeypatch.setattr(system.addons, "source_tier", lambda name: "gpu" if name == "transcription" else "standard")

    response = _health_response()
    assert response.status_code == 200
    body = response.json()
    assert str(private_path) not in response.text
    assert "private-probe-token" not in response.text
    assert "secret-probe-token" not in response.text
    assert body == {
        "status": "ok",
        "runtime": {"application": "standalone"},
        "capabilities": {
            "schemaVersion": 1,
            "application": "standalone",
            "packaged": packaged,
            "ffmpeg": True,
            "ffprobe": True,
            "cpuRender": True,
            "localInference": True,
            "transcription": {"name": "transcription", "state": "gpu", "tier": "gpu", "available": True,
                              "restartRequired": False, "installedBytes": 0, "sourceInstallation": True,
                              "jobId": None},
            "assistant": {"name": "assistant", "state": "standard", "tier": "standard", "available": True,
                          "restartRequired": False, "installedBytes": 0, "sourceInstallation": True,
                          "jobId": None, "keySource": "environment", "verification": "ready"},
        },
    }


@pytest.mark.parametrize("executables", [{"ffmpeg"}, {"ffprobe"}, set()])
def test_startup_requires_both_media_tools(
    monkeypatch: pytest.MonkeyPatch, executables: set[str], tmp_path: Path,
) -> None:
    monkeypatch.setattr(runtime_tools, "_TOOLS", {})
    missing = "ffprobe" if "ffmpeg" in executables else "ffmpeg"
    paths = {name: tmp_path / f"{name}.exe" for name in executables}
    for path in paths.values():
        path.write_bytes(b"fixture")
    monkeypatch.setattr(runtime_tools, "resolve_tool", lambda name, **kwargs: paths.get(name))
    with pytest.raises(RuntimeError, match=f"CUTGRAPH_{missing.upper()}"):
        runtime_tools.initialize_tools()
    assert runtime_tools._TOOLS == {}


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "example.invalid"])
def test_non_loopback_bind_is_refused(monkeypatch, capsys, host: str) -> None:
    monkeypatch.setattr(sys, "argv", ["studio", "--host", host, "--no-browser"])
    monkeypatch.setattr(studio_app, "_serve", lambda *args, **kwargs: pytest.fail("must refuse before binding"))
    assert studio_app.main() == 2
    assert "loopback addresses only" in capsys.readouterr().out


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_bind_is_accepted(monkeypatch, host: str) -> None:
    monkeypatch.setattr(sys, "argv", ["studio", "--host", host, "--port", "9123", "--no-browser"])
    calls = []
    monkeypatch.setattr(studio_app, "_serve", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(studio_app.web_security, "configure", lambda _port: None)
    monkeypatch.setenv("CUTGRAPH_STUDIO_URL", "http://127.0.0.1:9123")
    assert studio_app.main() == 0
    assert calls[0]["host"] == host
