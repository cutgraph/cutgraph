"""Authenticated release checks, restart throttling and launcher-only installation."""
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import pytest

import config
from installer import release_manifest
from pipeline import updates, version

REPO = "example/studio"
BASE = f"https://github.com/{REPO}/releases/"


@pytest.fixture
def feed(tmp_path):
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"synthetic installer")
    data = release_manifest.generate(setup, version="0.3.0", url=BASE + "download/v0.3.0/setup.exe",
                                     minimum_version="0.1.0", release_notes_url=BASE + "tag/v0.3.0")
    content = {"release.json": data, "release.json.sig": key.sign(data), "setup.exe": setup.read_bytes()}
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=content[request.url.path.rsplit("/", 1)[-1]])
    manager = updates.Updates(tmp_path / "state", REPO, public, "0.2.0", clock=lambda: 100000,
                              transport=httpx.MockTransport(respond))
    yield manager, content, key, calls
    manager.close()
    updates.exit_requested.clear()


def wait(manager):
    assert manager.worker is not None
    manager.worker.join(10)
    assert not manager.worker.is_alive()
    return manager.snapshot()


def test_signed_release_and_restart_throttle(feed):
    manager, _, _, calls = feed
    manager.check()
    assert wait(manager)["state"] == "available"
    assert len(calls) == 2
    assert all(request.headers["user-agent"] == "Cutgraph-Studio/0.2.0" for request in calls)
    assert all("cookie" not in request.headers and "authorization" not in request.headers for request in calls)
    manager.check()
    restored = updates.Updates(manager.root.parent, REPO, manager.public_key, "0.2.0", clock=manager.clock,
                               transport=manager.transport)
    assert restored.check()["state"] == "available"
    assert len(calls) == 2
    manager.clock = lambda: 100000 + updates.DAY
    manager.check()
    assert wait(manager)["state"] == "available"
    assert len(calls) == 4


def test_disabled_means_no_network_even_manual_or_install(feed):
    manager, _, _, calls = feed
    manager.set_automatic(False)
    for _ in range(3):
        assert manager.check()["state"] == "disabled"
    with pytest.raises(ValueError, match="Enable"):
        manager.install()
    restarted = updates.Updates(manager.root.parent, REPO, manager.public_key, "0.2.0", transport=manager.transport)
    assert restarted.check()["state"] == "disabled"
    assert calls == []


@pytest.mark.parametrize("repository,key", [("", ""), (REPO, ""), ("", "invalid"), (REPO, "invalid"),
                                               (REPO, base64.b64encode(bytes(32)).decode())])
def test_unconfigured_feed_is_closed(tmp_path, repository, key):
    manager = updates.Updates(tmp_path, repository, key, "0.2.0",
                              transport=httpx.MockTransport(lambda _: pytest.fail("Network was called")))
    assert manager.check()["state"] == "not_configured"
    with pytest.raises(ValueError):
        manager.install()
    assert not manager.root.exists()


def test_public_feed_without_verification_key_never_checks_or_installs(tmp_path):
    assert config.UPDATE_REPOSITORY == "cutgraph/cutgraph"
    assert config.UPDATE_PUBLIC_KEY == ""
    manager = updates.Updates(tmp_path, config.UPDATE_REPOSITORY, config.UPDATE_PUBLIC_KEY,
                              version.__version__, launcher=tmp_path / "launcher.exe",
                              transport=httpx.MockTransport(lambda _: pytest.fail("Network was called")))
    try:
        manager.start()
        snapshot = manager.check()
        assert snapshot["state"] == "not_configured"
        assert snapshot["automatic"] is True
        assert snapshot["canInstall"] is False
        assert manager.worker is None
        with pytest.raises(ValueError):
            manager.install()
        assert not manager.root.exists()
    finally:
        manager.close()


@pytest.mark.parametrize("failure", ["tampered", "key", "channel", "downgrade", "replayed", "http", "other-repo"])
def test_bad_metadata_fails_closed_and_throttles(feed, failure):
    manager, content, key, calls = feed
    document = json.loads(content["release.json"])
    if failure == "key":
        manager.public_key = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    elif failure == "channel":
        document["channel"] = "preview"
    elif failure == "downgrade":
        document["version"] = "0.1.0"
    elif failure == "replayed":
        manager.saved["highestVersion"] = "0.4.0"
    elif failure == "http":
        document["installer"]["url"] = "http://github.com/example/studio/releases/download/v0.3.0/setup.exe"
    elif failure == "other-repo":
        document["releaseNotesUrl"] = "https://github.com/example/other/releases/tag/v0.3.0"
    content["release.json"] = json.dumps(document).encode()
    content["release.json.sig"] = key.sign(content["release.json"])
    if failure == "tampered":
        content["release.json"] += b" "
    manager.check()
    assert wait(manager)["state"] == "failed"
    assert not manager.snapshot()["canInstall"]
    manager.check()
    assert len(calls) == 2
    assert not list(manager.root.glob("*.exe"))


def test_minimum_supported_version_requires_manual_update(feed):
    manager, content, key, calls = feed
    document = json.loads(content["release.json"])
    document["minimumVersion"] = "0.3.0"
    content["release.json"] = json.dumps(document).encode()
    content["release.json.sig"] = key.sign(content["release.json"])
    manager.launcher = Path("launcher.exe")
    manager.check()
    status = wait(manager)
    assert status["state"] == "available" and not status["canInstall"]
    assert "manual" in status["reason"]
    with pytest.raises(ValueError, match="manually"):
        manager.install()
    assert len(calls) == 2


def test_source_checkout_never_downloads(feed):
    manager, _, _, calls = feed
    manager.check()
    assert wait(manager)["releaseNotesUrl"]
    with pytest.raises(ValueError, match="manually"):
        manager.install()
    assert len(calls) == 2


@pytest.mark.parametrize("corruption", [None, "hash", "short", "oversize", "signature"])
def test_download_is_verified_before_launcher_handoff(feed, monkeypatch, corruption):
    manager, content, _, calls = feed
    manager.launcher = Path("launcher.exe")
    manager.check()
    assert wait(manager)["canInstall"]
    if corruption == "signature":
        manager.data += b" "
    elif corruption == "hash":
        content["setup.exe"] = b"x" * len(content["setup.exe"])
    elif corruption == "short":
        content["setup.exe"] = b"short"
    elif corruption == "oversize":
        content["setup.exe"] += b"extra"
    launched = []
    def launch(command, **_kwargs):
        launched.append(command)
        request = json.loads((manager.root / "pending.json").read_text())
        path = Path(request["path"])
        assert path.parent == manager.root.resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == request["sha256"]
        assert path.stat().st_size == request["size"]
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(updates.subprocess, "run", launch)
    manager.install()
    status = wait(manager)
    assert status["state"] == ("installing" if corruption is None else "failed")
    assert launched == ([["launcher.exe", "--request-update"]] if corruption is None else [])
    assert not list(manager.root.glob("*.part"))


def test_no_concurrent_checks(feed):
    manager, _, _, calls = feed
    threads = [threading.Thread(target=manager.check) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert wait(manager)["state"] == "available"
    assert len(calls) == 2


def test_up_to_date_and_no_launch_on_refusal(feed, monkeypatch):
    manager, _, _, _ = feed
    manager.current = "0.3.0"
    manager.check()
    assert wait(manager)["state"] == "up_to_date"
    assert not manager.snapshot()["canInstall"]


def test_launcher_refusal_does_not_request_shutdown(feed, monkeypatch):
    manager, _, _, _ = feed
    manager.launcher = Path("launcher.exe")
    manager.check()
    wait(manager)
    monkeypatch.setattr(updates.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1))
    manager.install()
    status = wait(manager)
    assert status["state"] == "failed" and "launcher refused" in status["reason"]
    assert not updates.exit_requested.is_set()


def test_cache_is_reauthenticated_after_restart(feed):
    manager, _, _, _ = feed
    manager.check()
    wait(manager)
    manager.saved["manifest"] = base64.b64encode(manager.data + b" ").decode()
    manager._save()
    restarted = updates.Updates(manager.root.parent, REPO, manager.public_key, "0.2.0")
    assert restarted.snapshot()["state"] == "failed"
    assert restarted.document is None


def test_routes_persist_the_off_switch_and_refuse_source_install(feed, monkeypatch):
    from starlette.testclient import TestClient
    from studio.app import app
    manager, _, _, calls = feed
    monkeypatch.setattr(updates, "service", lambda: manager)
    client = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    origin = {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"}
    assert client.put("/api/updates/preferences", json={"automatic": False}, headers=origin).status_code == 200
    assert client.get("/api/updates").json()["automatic"] is False
    assert client.post("/api/updates/check", headers=origin).json()["state"] == "disabled"
    assert client.post("/api/updates/install", headers=origin).status_code == 409
    assert client.put("/api/updates/preferences", json={"automatic": "no"}, headers=origin).status_code == 400
    assert client.put("/api/updates/preferences", content="{", headers={**origin, "Content-Type": "application/json"}).status_code == 400
    assert calls == []
