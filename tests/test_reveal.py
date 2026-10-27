"""POST /api/reveal: select one rendered artifact in the OS file explorer.

Loopback-only (require_loopback_client), path containment
under config.STATE_HOME for both the `{project}` and `{path}` request shapes, and the happy path
with subprocess.Popen mocked (never actually shells `explorer` in the test suite).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
import pytest

import config
from scripts import studio_app
from studio import compute, helpers
from studio.routes import reveal

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _do(fn: Callable[[], Awaitable[T]]) -> T:
    return _run(fn())


def _client(client_host: str = "127.0.0.1") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=studio_app.app, client=(client_host, 123))
    return httpx.AsyncClient(transport=transport, base_url="http://studio.test")


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SystemRoot", str(tmp_path / "system"))
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "outputs" / "final")


def test_refuses_a_non_loopback_client() -> None:
    async def scenario() -> httpx.Response:
        async with _client("10.0.0.5") as client:
            return await client.post("/api/reveal", json={"project": "demo-talk"})

    response = _do(scenario)
    assert response.status_code == 403


def test_refuses_a_path_outside_state_home(tmp_path) -> None:
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"\x00")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/reveal", json={"path": "../outside.mp4"})

    response = _do(scenario)
    assert response.status_code == 403


def test_refuses_traversal_through_a_project_name() -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/reveal", json={"project": "../../etc/passwd"})

    response = _do(scenario)
    assert response.status_code == 400


def test_missing_project_and_path_is_a_bad_request() -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/reveal", json={})

    response = _do(scenario)
    assert response.status_code == 400


def test_missing_file_is_not_found() -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/reveal", json={"project": "no-such-project"})

    response = _do(scenario)
    assert response.status_code == 404


def test_happy_path_selects_the_final_render(monkeypatch) -> None:
    config.FINAL_DIR.mkdir(parents=True, exist_ok=True)
    output = config.FINAL_DIR / "demo-talk__9x16.compiled.mp4"
    output.write_bytes(b"\x00\x00\x00\x18ftypisom")
    calls: list[tuple[list[str], dict]] = []

    class _FakeProcess:
        pid = 4242

    def _fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return _FakeProcess()

    monkeypatch.setattr(helpers.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(compute.sys, "platform", "win32")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/reveal", json={"project": "demo-talk__9x16"})

    response = _do(scenario)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == str(config.STATE_HOME / "system" / "explorer.exe")
    assert args[1] == f"/select,{output.resolve()}"
    assert kwargs.get("shell") is False


def test_happy_path_selects_an_explicit_path(monkeypatch) -> None:
    nested = config.OUTPUTS_DIR / "jobs" / "b2d77943.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(helpers.subprocess, "Popen", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(compute.sys, "platform", "win32")

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/reveal", json={"path": "outputs/jobs/b2d77943.json"})

    response = _do(scenario)
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][1] == f"/select,{nested.resolve()}"


@pytest.mark.parametrize("system_root", [None, "", "relative-system"])
def test_windows_requires_an_absolute_system_root(monkeypatch, system_root: str | None) -> None:
    config.FINAL_DIR.mkdir(parents=True, exist_ok=True)
    (config.FINAL_DIR / "demo-talk.compiled.mp4").write_bytes(b"media")
    monkeypatch.setattr(reveal.sys, "platform", "win32")
    if system_root is None:
        monkeypatch.delenv("SystemRoot", raising=False)
    else:
        monkeypatch.setenv("SystemRoot", system_root)
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch"))

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/reveal", json={"project": "demo-talk"})

    assert _do(scenario).status_code == 501


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_unavailable_file_explorer_is_not_implemented(monkeypatch, platform: str) -> None:
    config.FINAL_DIR.mkdir(parents=True, exist_ok=True)
    (config.FINAL_DIR / "demo-talk.compiled.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(reveal.sys, "platform", platform)
    monkeypatch.setattr(reveal, "_XDG_OPEN", None)
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch"))

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/reveal", json={"project": "demo-talk"})

    response = _do(scenario)
    assert response.status_code == 501


@pytest.mark.parametrize("use_project", [True, False])
def test_linux_opens_containing_folder(monkeypatch, tmp_path, use_project: bool) -> None:
    config.FINAL_DIR.mkdir(parents=True, exist_ok=True)
    output = config.FINAL_DIR / "demo-talk.compiled.mp4"
    output.write_bytes(b"media")
    executable = str((tmp_path / "bin/xdg-open").resolve())
    monkeypatch.setattr(reveal.sys, "platform", "linux")
    monkeypatch.setattr(reveal, "_XDG_OPEN", executable)
    calls = []
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))

    async def scenario() -> httpx.Response:
        async with _client() as client:
            body = {"project": "demo-talk"} if use_project else {"path": "outputs/final/demo-talk.compiled.mp4"}
            return await client.post("/api/reveal", json=body)

    response = _do(scenario)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls == [([executable, str(output.resolve().parent)], {"shell": False})]
