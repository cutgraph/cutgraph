"""Startup ownership, upgrade compatibility and download lifecycle regressions."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

import config
from pipeline import addons, installer_process, jobs, state_lock, transcribe
from studio.app import app


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(addons, "_activated", {})
    monkeypatch.setattr(addons, "_threads", {})
    monkeypatch.setattr(addons, "_stop", threading.Event())
    monkeypatch.setattr(addons, "_sizes", {})
    monkeypatch.setattr(addons, "source_tier", lambda *args: None)
    monkeypatch.setattr(jobs, "_registry", {})
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    addons.shutdown()


def installed(monkeypatch):
    addon = addons.definition("transcription", "cpu")
    def pip(_addon, stage, *args):
        metadata = stage / "speech-1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Name: speech\nVersion: 1.0\n")
    monkeypatch.setattr(addons, "_pip", pip)
    job = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription"})
    addons._run(addon, addon.acceptance(), job, lambda _: None)
    record = addons.active("transcription")
    assert record is not None
    return addon, record


@pytest.mark.parametrize("change", ["removed", "added", "version"])
def test_core_drift_never_activates_an_excluded_dependency_set(monkeypatch, change):
    _, record = installed(monkeypatch)
    original = addons.core_versions()
    assert record["excludedCore"] == original
    changed = dict(original)
    name = next(iter(changed))
    if change == "removed":
        del changed[name]
    elif change == "added":
        changed["new-core-package"] = "1.0"
    else:
        changed[name] = "99.0"
    monkeypatch.setattr(addons, "core_versions", lambda: changed)
    addons.activate()
    assert not addons.available("transcription")
    assert addons.status("transcription")["state"] == "needs update"
    assert addons._activated == {}
    assert "update" in addons.unavailable_message("transcription").lower()


def test_interpreter_extensions_do_not_invalidate_the_fixed_abi_contract(monkeypatch):
    _, record = installed(monkeypatch)
    monkeypatch.setattr(addons, "interpreter", lambda: {**record["interpreter"], "diagnostic": "extra"})
    assert addons._compatible(record)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows protected PID access")
def test_inaccessible_pid_marker_is_logged_cleared_and_does_not_block_cleanup(caplog):
    root = addons._root("transcription")
    stage = root / ".staging-abandoned"
    stage.mkdir(parents=True)
    (root / "temporary").mkdir()
    (root / "cache").mkdir()
    addons._write(root / "process.json", {"pid": 4, "token": "previous-creation:previous-image"})
    addons.startup()
    assert "Discarding installer marker" in caplog.text
    assert all(record.levelname == "WARNING" for record in caplog.records)
    assert not (root / "process.json").exists()
    assert not stage.exists()
    assert not (root / "temporary").exists()
    assert (root / "cache").is_dir()
    addons.startup()


def test_second_lifespan_refuses_before_recovery_or_cleanup(monkeypatch):
    monkeypatch.setattr(addons, "startup", lambda: pytest.fail("must not clean a live instance"))
    monkeypatch.setattr(addons, "activate", lambda: pytest.fail("must not activate before ownership"))
    async def run():
        async with app.router.lifespan_context(app):
            pytest.fail("second instance must refuse")
    with state_lock.acquire(config.STATE_HOME):
        with pytest.raises(RuntimeError, match="already running with this state home"):
            asyncio.run(run())


def test_state_home_lock_is_cross_process_and_release_is_automatic():
    code = ("import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from pipeline.state_lock import acquire; "
            "with_lock=acquire(Path(sys.argv[2])); with_lock.__enter__(); print('owned')")
    command = [sys.executable, "-I", "-B", "-c", code, str(config.CODE_ROOT), str(config.STATE_HOME)]
    with state_lock.acquire(config.STATE_HOME):
        second = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert second.returncode != 0 and "already running" in second.stderr
        with state_lock.acquire(config.STATE_HOME / "independent"):
            pass
    first = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert first.returncode == 0 and first.stdout.strip() == "owned"
    with state_lock.acquire(config.STATE_HOME):
        pass


def test_unreadable_active_pointer_preserves_the_only_installed_tree(monkeypatch):
    _, record = installed(monkeypatch)
    root = addons._root("transcription")
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path == root / "active.json":
            raise PermissionError("temporarily locked")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    addons.startup()
    assert (root / record["directory"]).is_dir()


@pytest.mark.parametrize("bundled", [False, True])
def test_remove_keeps_source_and_process_shared_caches(monkeypatch, bundled):
    installed(monkeypatch)
    monkeypatch.setattr(addons, "bundle_root", lambda: config.STATE_HOME / "bundle" if bundled else None)
    for directory in ("models/torchaudio", "models/cache", "models/huggingface"):
        path = config.STATE_HOME / directory
        path.mkdir(parents=True)
        (path / "checkpoint").write_bytes(b"model")
    addons.remove("transcription")
    assert (config.STATE_HOME / "models/cache/checkpoint").exists()
    for directory in ("torchaudio", "huggingface"):
        assert (config.STATE_HOME / "models" / directory / "checkpoint").exists() == (not bundled)


def test_generic_cache_ownership_protects_other_addons_and_process_caches(monkeypatch):
    first = addons.definition("transcription", "cpu")
    second = replace(first, name="assistant", tier="standard", cache_directories=("models/cache", "models/shared"))
    first = replace(first, cache_directories=("models/cache", "models/shared", "models/owned"))
    monkeypatch.setattr(addons, "definitions", lambda: (first, second))
    assert addons._owned_caches(first) == ("models/owned",)
    assert addons._owned_caches(second) == ()


def test_failed_cache_quarantine_is_retried_even_without_a_quarantine_name(monkeypatch):
    root = addons._root("transcription")
    cache = root / "cache"
    cache.mkdir(parents=True)
    job = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription"})
    jobs.set(job, status="done")
    with monkeypatch.context() as patch:
        patch.setattr(addons, "_delete", lambda *args, **kwargs: False)
        patch.setattr(Path, "rename", lambda *args: (_ for _ in ()).throw(PermissionError("locked")))
        addons.startup()
    assert cache.is_dir()
    addons.startup()
    assert not cache.exists()


def test_pending_removal_reports_the_locked_file_and_retries(monkeypatch):
    addon, record = installed(monkeypatch)
    root = addons._root("transcription")
    locked = root / record["directory"] / "native.dll"
    locked.write_bytes(b"synthetic")
    original = addons.shutil.rmtree
    def remove(path, *args, **kwargs):
        if Path(path) == locked.parent:
            raise PermissionError(13, "locked", str(locked))
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(addons.shutil, "rmtree", remove)
        addons.remove("transcription")
        marker = json.loads((root / "remove.json").read_text())
        assert marker["blockedPaths"] == [locked.relative_to(config.STATE_HOME).as_posix()]
        with pytest.raises(addons.AddonError, match="native.dll"):
            addons.install("transcription", "cpu", addon.acceptance(), lambda _: None)
    addons.startup()
    assert not root.exists()


def test_size_is_lazy_cached_and_invalidated_by_install_and_remove(monkeypatch):
    installed(monkeypatch)
    size = addons.status("transcription")["installedBytes"]
    assert size > 0
    original = Path.rglob
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rglob", lambda *args: pytest.fail("polling must reuse the cached size"))
        for _ in range(5):
            assert addons.status("transcription")["installedBytes"] == size
    assert Path.rglob is original
    addons.remove("transcription")
    assert addons.status("transcription")["installedBytes"] == 0


def test_activation_processes_wheel_pth_paths_and_standard_import_lines(monkeypatch):
    _, record = installed(monkeypatch)
    root = addons._root("transcription") / record["directory"]
    extra = root / "extras"
    extra.mkdir()
    (extra / "optional_probe_module.py").write_text("answer = 42\n")
    (root / "wheel.pth").write_text("extras\nimport sys; sys._optional_pth_probe = True\n")
    previous = list(sys.path)
    try:
        addons.activate()
        assert sys.path[:len(previous)] == previous
        assert sys.path.index(str(root)) < sys.path.index(str(extra))
        module = importlib.import_module("optional_probe_module")
        assert module.answer == 42
        assert getattr(sys, "_optional_pth_probe", False)
    finally:
        sys.modules.pop("optional_probe_module", None)
        if hasattr(sys, "_optional_pth_probe"):
            delattr(sys, "_optional_pth_probe")


def test_missing_declaration_lock_is_unavailable_instead_of_raising(monkeypatch):
    addon = replace(addons.definition("transcription", "cpu"), name="assistant", tier="standard", lock="missing-lock.txt")
    monkeypatch.setattr(addons, "definitions", lambda: (addon,))
    from studio.routes.addons import list_addons

    monkeypatch.setattr(addons, "detect_gpus", lambda: [])
    result = list_addons()["addons"][0]
    assert result["state"] == "unavailable" and result["tiers"] == [] and not result["available"]


@pytest.mark.parametrize("bundled", [False, True])
def test_cuda_order_is_confined_to_bundle_inference_and_restored(monkeypatch, bundled):
    from types import SimpleNamespace
    from pipeline import runtime_tools

    monkeypatch.delenv("CUDA_DEVICE_ORDER", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(runtime_tools, "bundle_root", lambda: config.STATE_HOME if bundled else None)
    monkeypatch.setattr(addons, "detect_gpus", lambda: [])
    def initialize():
        assert os.environ.get("CUDA_DEVICE_ORDER") == ("PCI_BUS_ID" if bundled else None)
        return True
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(
        is_available=initialize, mem_get_info=lambda: (8 * 1024**3, 16 * 1024**3))))
    assert transcribe._inference_settings() == ("cuda", "float16", 8)
    assert "CUDA_DEVICE_ORDER" not in os.environ


def test_partition_merge_preserves_console_scripts_and_namespaces(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    for root, script in ((first, "one"), (second, "two")):
        (root / "bin").mkdir(parents=True)
        (root / "bin" / script).write_text(script)
    addons._merge_tree(second, first)
    assert {p.name for p in (first / "bin").iterdir()} == {"one", "two"}


def test_pip_shutdown_and_download_progress_are_observable(monkeypatch):
    addon = addons.definition("transcription", "cpu")
    root = addons._root("transcription")
    stage = root / "target"
    stage.mkdir(parents=True)
    lock = root / "lock.txt"
    lock.write_text("speech==1.0 --hash=sha256:" + "a" * 64)
    job = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription"})
    jobs.set(job, status="running")
    observations = []
    class Process:
        pid = 42
        returncode = None
        polls = 0
        def poll(self):
            self.polls += 1
            if self.polls == 4:
                self.returncode = 0
            return self.returncode
        def terminate(self):
            self.returncode = 1
        def wait(self, **kwargs):
            return self.returncode
    @contextmanager
    def start(*args, **kwargs):
        yield Process()
    monkeypatch.setattr(installer_process, "start", start)
    monkeypatch.setattr(addons, "_process_identity", lambda _: None)
    progress = iter([100, 300, 700])
    def advance(_):
        with (root / "install.log").open("a") as stream:
            stream.write(f"Progress {next(progress)} of 1000\n")
    monkeypatch.setattr(addons._stop, "wait", advance)
    addons._pip_install(addon, stage, lock, job, lambda _: observations.append(jobs.get(job)), "https://pypi.org/simple")
    values = [value["stageProgress"] for value in observations]
    assert len(values) >= 2 and all(a < b for a, b in zip(values, values[1:]))
    assert all("MiB received" in value["stageNote"] for value in observations)
    addons._stop.set()
    with pytest.raises(addons.AddonError) as error:
        addons._pip_install(addon, stage, lock, job, lambda _: None, "https://pypi.org/simple")
    assert error.value.code == "ADDON_INTERRUPTED"


def test_pip_unavailable_build_has_an_update_error(monkeypatch):
    addon = addons.definition("transcription", "cpu")
    root = addons._root("transcription")
    root.mkdir(parents=True)
    lock = root / "lock.txt"
    lock.write_text("speech==1.0 --hash=sha256:" + "a" * 64)
    @contextmanager
    def start(*args, **kwargs):
        from types import SimpleNamespace

        kwargs["stdout"].write("ERROR: No matching distribution found\n")
        kwargs["stdout"].flush()
        yield SimpleNamespace(pid=42, returncode=1, poll=lambda: 1)
    monkeypatch.setattr(installer_process, "start", start)
    monkeypatch.setattr(addons, "_process_identity", lambda _: None)
    with pytest.raises(addons.AddonError) as error:
        addons._pip_install(addon, root, lock, "job", lambda _: None, "https://pypi.org/simple")
    assert error.value.code == "ADDON_BUILD_UNAVAILABLE"


def test_download_byte_counter_tracks_resets_between_wheels():
    assert addons.download_bytes("Progress 0 of 100\nProgress 100 of 100\nProgress 0 of 1000\nProgress 700 of 1000") == 800


def test_real_protected_installer_process_is_resumed_and_reaped():
    with installer_process.start([sys.executable, "-I", "-B", "-c", "print('protected')"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0 and stdout.strip() == "protected", stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job ownership")
def test_installer_job_stops_a_child_when_the_owner_exits():
    code = ("import sys,os; sys.path.insert(0,sys.argv[1]); from pipeline.installer_process import start; "
            "owner=start([sys.executable,'-I','-c','import time; time.sleep(30)']); "
            "child=owner.__enter__(); print(child.pid,flush=True); os._exit(0)")
    owner = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(config.CODE_ROOT)],
                           capture_output=True, text=True, timeout=10)
    assert owner.returncode == 0, owner.stderr
    pid = int(owner.stdout.strip())
    deadline = time.monotonic() + 5
    while addons._process_identity(pid) is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert addons._process_identity(pid) is None


@pytest.mark.parametrize("version,progress", [("23.3.2", "off"), ("24.0", "off"), ("24.1", "raw"), ("26.0.1", "raw")])
def test_source_pip_progress_supports_older_environments(monkeypatch, version, progress):
    monkeypatch.setattr(addons, "bundle_root", lambda: None)
    monkeypatch.setattr(addons.importlib.metadata, "version", lambda _: version)
    addon = addons.definition("transcription", "cpu")
    command = addons.pip_command(addon, config.STATE_HOME, config.STATE_HOME / "lock.txt")
    assert command[command.index("--progress-bar") + 1] == progress


def test_core_lock_is_parsed_once_per_process_and_code_root(monkeypatch):
    original = Path.read_text
    reads = []
    def read(path, *args, **kwargs):
        if path == config.CODE_ROOT / "requirements-core.txt":
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    addons._core_versions.cache_clear()
    for _ in range(5):
        addons.status("transcription")
        assert addons.core_versions()
    assert len(reads) == 1
