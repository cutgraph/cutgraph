"""Real pip cache reuse across interrupted installation and process restart."""
from __future__ import annotations

from dataclasses import replace
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
import subprocess
import sys
import threading
import time
from zipfile import ZipFile

import pytest

import config
from pipeline import addons, jobs


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "outputs/jobs")
    monkeypatch.setattr(addons, "_activated", {})
    monkeypatch.setattr(addons, "_threads", {})
    monkeypatch.setattr(addons, "_stop", threading.Event())
    monkeypatch.setattr(addons, "_sizes", {})
    monkeypatch.setattr(addons, "source_tier", lambda _: None)
    monkeypatch.setattr(jobs, "_registry", {})


def test_interrupted_install_restart_retry_reuses_verified_wheel(monkeypatch, tmp_path):
    repository = tmp_path / "repository"
    simple = repository / "simple/speech"
    simple.mkdir(parents=True)
    wheel = repository / "speech-1.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("speech.py", "answer = 42\n")
        archive.writestr("speech-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: speech\nVersion: 1.0\n")
        archive.writestr("speech-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("speech-1.0.dist-info/RECORD", "")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (simple / "index.html").write_text(f'<a href="../../{wheel.name}#sha256={digest}">speech</a>')
    lock = tmp_path / "requirements.txt"
    lock.write_text(f"speech==1.0 --hash=sha256:{digest}\n")
    addon = replace(addons.definition("transcription", "cpu"), lock=str(lock))
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()

        def end_headers(self):
            self.send_header("Cache-Control", "public, max-age=86400")
            super().end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(repository))) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            index = f"http://127.0.0.1:{server.server_port}/simple"
            monkeypatch.setattr(addons.dependency_locks, "package_index", lambda *args: index)
            command = addons.pip_command
            monkeypatch.setattr(addons, "pip_command", lambda *args, **kwargs:
                                [*command(*args, **kwargs), "--trusted-host", "127.0.0.1"])
            pip = addons._pip
            root = addons._root(addon.name)
            partial_file = root / "temporary/incomplete.whl"

            def interrupted(*args):
                pip(*args)
                partial_file.write_bytes(b"partial")
                raise addons.AddonError(addons.ERROR_MESSAGES["ADDON_INTERRUPTED"], code="ADDON_INTERRUPTED")

            monkeypatch.setattr(addons, "_pip", interrupted)
            first = jobs.new_job("addon_install", "addon-transcription", {"addon": addon.name})
            addons._run(addon, addon.acceptance(), first, lambda _: None)
            assert (jobs.get(first) or {})["code"] == "ADDON_INTERRUPTED"
            assert requests.count("/" + wheel.name) == 1
            assert list((root / "cache").rglob("*.body"))
            assert partial_file.exists()
            environment = {**os.environ, "CUTGRAPH_STATE_HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
            code = ("import sys; sys.path.insert(0,sys.argv[1]); "
                    "from pipeline import addons,state_lock; import config; "
                    "ownership=state_lock.launch(config.STATE_HOME); ownership.__enter__(); addons.startup()")
            restart = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(config.CODE_ROOT)],
                                     env=environment, capture_output=True, text=True, timeout=20)
            assert restart.returncode == 0, restart.stderr
            assert not partial_file.exists()
            assert list((root / "cache").rglob("*.body"))
            monkeypatch.setattr(addons, "_pip", pip)
            second = jobs.new_job("addon_install", "addon-transcription", {"addon": addon.name})
            addons._run(addon, addon.acceptance(), second, lambda _: None)
            assert (jobs.get(second) or {})["status"] == "done", (root / "install.log").read_text()
            assert requests.count("/" + wheel.name) == 1
            assert not (root / "cache").exists()
            assert not (root / "temporary").exists()
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert not thread.is_alive()


def test_cache_expires_old_wheels_and_discards_incomplete_entries():
    root = addons._root("transcription")
    cache = root / "cache/http-v2"
    cache.mkdir(parents=True)
    completed = cache / ("a" * 56)
    completed.write_bytes(b"metadata")
    completed.with_name(completed.name + ".body").write_bytes(b"wheel")
    expired = cache / ("b" * 56)
    expired.write_bytes(b"metadata")
    expired_body = expired.with_name(expired.name + ".body")
    expired_body.write_bytes(b"old wheel")
    expired_at = time.time() - addons.WHEEL_CACHE_MAX_AGE - 1
    for path in (expired, expired_body):
        os.utime(path, (expired_at, expired_at))
    incomplete = cache / ("c" * 56 + ".body")
    incomplete.write_bytes(b"incomplete pair")
    partial_file = cache / ("d" * 56 + ".tmp-random")
    partial_file.write_bytes(b"partial cache write")
    addons.startup()
    assert {path.name for path in cache.iterdir()} == {completed.name, completed.name + ".body"}
    addons.remove("transcription")
    assert not root.exists()


def test_publish_marker_purges_after_crash_but_keeps_a_newer_retry_cache(monkeypatch):
    addon = addons.definition("transcription", "cpu")
    def install(_addon, stage, *args):
        metadata = stage / "speech-1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Name: speech\nVersion: 1.0\n")
    monkeypatch.setattr(addons, "_pip", install)
    first = jobs.new_job("addon_install", "addon-transcription", {"addon": addon.name})
    addons._run(addon, addon.acceptance(), first, lambda _: None)
    record = addons.active(addon.name)
    assert record and record["cachePurge"]
    root = addons._root(addon.name)
    cache = root / "cache"
    cache.mkdir()
    (cache / "completed.whl").write_bytes(b"verified wheel")
    jobs.set(first, status="publishing")
    addons.startup()
    assert not cache.exists()
    second = jobs.new_job("addon_install", "addon-transcription", {"addon": addon.name})
    jobs.set(second, status="running", startedAt=record["installedAt"] + 1)
    cache.mkdir()
    (cache / "completed.whl").write_bytes(b"verified wheel")
    addons.startup()
    assert (jobs.get(second) or {})["code"] == "ADDON_INTERRUPTED"
    assert (cache / "completed.whl").read_bytes() == b"verified wheel"
    addons._activated[addon.name] = record["directory"]
    addons.remove(addon.name)
    assert not cache.exists()
    assert (root / "remove.json").exists()
    assert (root / record["directory"]).is_dir()
