"""Core precedence, portable caches, recovery and reusable add-on boundaries."""
from __future__ import annotations

import asyncio
import errno
import importlib.util
import os
import subprocess
import sys
import threading
from dataclasses import replace

import httpx
import pytest

import config
from pipeline import addons, jobs, models
from studio import compute
from studio.app import app


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(addons, "_activated", {})
    monkeypatch.setattr(addons, "_threads", {})
    monkeypatch.setattr(addons, "_stop", threading.Event())
    monkeypatch.setattr(addons, "source_tier", lambda *args: None)
    monkeypatch.setattr(jobs, "_registry", {})
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    addons.shutdown()


def installed(monkeypatch, tier="cpu"):
    addon = addons.definition("transcription", tier)
    def pip(_addon, stage, *args):
        metadata = stage / "speech-1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Name: speech\nVersion: 1.0\n")
        (stage / "speech.py").write_text("origin = 'addon'\n")
    monkeypatch.setattr(addons, "_pip", pip)
    job_id = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription", "tier": tier})
    addons._run(addon, addon.acceptance(), job_id, lambda _: None)
    record = addons.active("transcription")
    assert record is not None
    return addon, record


def test_addon_never_shadows_core(monkeypatch, tmp_path):
    _, record = installed(monkeypatch)
    core = tmp_path / "core"
    core.mkdir()
    (core / "speech.py").write_text("origin = 'core'\n")
    sys.path.append(str(core))
    addons.activate()
    spec = importlib.util.find_spec("speech")
    assert spec is not None and spec.origin == str(core / "speech.py")
    assert sys.path.index(str(core)) < sys.path.index(str(addons._root("transcription") / record["directory"]))
    assert record["interpreter"] == addons.interpreter()
    assert len(record["directory"]) == 16
    assert record["declaration"]["licenses"]
    assert record["acceptedAt"] > 0


@pytest.mark.parametrize("field,value", [("version", [3, 99]), ("cacheTag", "other"),
                                         ("abi", "other"), ("platform", "other")])
def test_foreign_interpreter_is_not_activated_and_can_be_updated(monkeypatch, field, value):
    addon, record = installed(monkeypatch)
    record["interpreter"][field] = value
    root = addons._root("transcription")
    addons._write(root / record["directory"] / "install.json", record)
    addons._write(root / "active.json", record)
    addons.activate()
    assert "transcription" not in addons._activated
    assert addons.status("transcription")["state"] == "needs update"
    assert not addons.status("transcription")["restartRequired"]
    assert not addons.available("transcription")
    assert addon.acceptance() == record["acceptance"]


def test_core_is_excluded_before_pip_without_pruning_record_files():
    addon = addons.definition("transcription", "cpu")
    payload = addons.install_requirements(addon.lock_path.read_text())
    core = {name for name, _, _ in addons._requirements((config.CODE_ROOT / "requirements-core.txt").read_text())}
    assert not core.intersection(name for name, _, _ in addons._requirements(payload))
    assert "whisperx==" in payload and "--hash=sha256:" in payload
    invalid = (config.CODE_ROOT / "requirements-core.txt").read_text().replace("anyio==", "anyio==0")
    with pytest.raises(addons.AddonError, match="conflicts"):
        addons.install_requirements(invalid)


@pytest.mark.parametrize("tier", ["cpu", "gpu"])
def test_pip_indexes_are_partitioned_by_package_family(monkeypatch, tmp_path, tier):
    addon = addons.definition("transcription", tier)
    lock = tmp_path / "requirements.txt"
    lock.write_text(addons.install_requirements(addon.lock_path.read_text()))
    calls = []
    def pip(_addon, stage, requirements, job_id, notify, index):
        names = {name for name, _, _ in addons._requirements(requirements.read_text())}
        calls.append((names, index))
    monkeypatch.setattr(addons, "_pip_install", pip)
    addons._pip(addon, tmp_path, lock, "job", lambda _: None)
    assert len(calls) == 2
    assert ("torchcodec" in next(names for names, index in calls if index == "https://pypi.org/simple"))
    assert ({"torch", "torchaudio", "torchvision"}, addon.index) in calls


def test_bundled_pip_uses_only_the_carried_wheel_with_isolated_python(monkeypatch, tmp_path):
    wheel = tmp_path / "addons" / "pip-1.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"wheel")
    monkeypatch.setattr(addons, "bundle_root", lambda: tmp_path)
    command = addons.pip_command(addons.definition("transcription", "cpu"), tmp_path / "stage", tmp_path / "lock")
    assert command[:4] == [sys.executable, "-I", "-B", "-c"]
    assert command[5] == str(wheel)
    assert "--require-hashes" in command and "--no-deps" in command


def test_existing_video_without_job_has_explicit_unavailable_code():
    lifecycle = compute._transcript_lifecycle("new-video")
    assert lifecycle["errorCode"] == "TRANSCRIPTION_NOT_INSTALLED"
    assert lifecycle["job"] is None


def test_success_cleans_cache_and_superseded_versions_after_restart(monkeypatch):
    monkeypatch.setattr(addons, "bundle_root", lambda: config.STATE_HOME / "bundle")
    _, cpu = installed(monkeypatch)
    addons.activate()
    root = addons._root("transcription")
    (root / "cache").mkdir()
    (root / "cache" / "wheel").write_bytes(b"wheel")
    _, gpu = installed(monkeypatch, "gpu")
    assert (root / cpu["directory"]).exists()
    assert not (root / "cache").exists()
    monkeypatch.setattr(addons, "_activated", {})
    addons.activate()
    addons.startup()
    assert not (root / cpu["directory"]).exists()
    assert (root / gpu["directory"]).exists()
    (config.STATE_HOME / "models" / "huggingface").mkdir(parents=True)
    (config.STATE_HOME / "models" / "huggingface" / "model").write_bytes(b"model")
    addons.invalidate_size("transcription")
    expected = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) + 5
    assert addons.status("transcription")["installedBytes"] == expected


def test_locked_startup_cleanup_is_quarantined_and_retried(monkeypatch):
    root = addons._root("transcription")
    stage = root / ".staging-abandoned"
    stage.mkdir(parents=True)
    (stage / "partial").write_bytes(b"partial")
    real_delete = addons.shutil.rmtree
    with monkeypatch.context() as patch:
        patch.setattr(addons.shutil, "rmtree", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError()))
        addons.startup()
    assert list(root.glob(".quarantine-*"))
    assert not stage.exists()
    addons.startup()
    assert not list(root.glob(".quarantine-*"))
    assert addons.shutil.rmtree is real_delete


def test_stale_pip_is_stopped_before_staging_cleanup(monkeypatch):
    root = addons._root("transcription")
    stage = root / ".staging-abandoned"
    stage.mkdir(parents=True)
    job_id = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription"})
    jobs.set(job_id, status="running")
    addons._write(root / "process.json", {"pid": 42, "token": "start", "jobId": job_id})
    def terminate(pid, token):
        assert (pid, token) == (42, "start") and stage.exists()
    monkeypatch.setattr(addons, "_terminate_orphan", terminate)
    addons.startup()
    assert not stage.exists() and not (root / "process.json").exists()
    job = jobs.get(job_id)
    assert job is not None and job["status"] == "error"
    assert compute._job_presentation(job)["error"] == addons.ERROR_MESSAGES["ADDON_INTERRUPTED"]


def test_real_orphan_termination_checks_creation_identity():
    process = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"])
    try:
        token = addons._process_identity(process.pid)
        assert token is not None
        addons._terminate_orphan(process.pid, "different")
        assert process.poll() is None
        addons._terminate_orphan(process.pid, token)
        assert process.wait(timeout=10) != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize("exception,code", [(RuntimeError("private details"), "ADDON_INSTALL_FAILED"),
    (OSError(errno.ENOSPC, "private details"), "ADDON_DISK_FULL"),
    (PermissionError("private details"), "ADDON_STORAGE_FAILED"),
    (addons.AddonError("details", code="ADDON_DOWNLOAD_FAILED"), "ADDON_DOWNLOAD_FAILED")])
def test_unexpected_failures_always_persist_safe_retry_reason(monkeypatch, exception, code):
    addon = addons.definition("transcription", "cpu")
    def fail(*args):
        raise exception
    monkeypatch.setattr(addons, "_pip", fail)
    job_id = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription"})
    addons._run(addon, addon.acceptance(), job_id, lambda _: None)
    job = jobs.get(job_id)
    assert job is not None and job["status"] == "error" and job["code"] == code
    assert job["error"] == addons.ERROR_MESSAGES[code]
    assert compute._job_presentation(job)["error"] == addons.ERROR_MESSAGES[code]
    assert compute._job_presentation(job)["canRetry"]


def test_declaration_change_requires_update_without_a_lock_change(monkeypatch):
    addon, _ = installed(monkeypatch)
    monkeypatch.setattr(addons, "definitions", lambda: (replace(addon, licenses=()),))
    assert addons.status("transcription")["state"] == "needs update"


def test_removal_prevents_reinstall_until_restart(monkeypatch):
    addon, _ = installed(monkeypatch)
    addons.activate()
    addons.remove("transcription")
    with pytest.raises(addons.AddonError, match="removing"):
        addons.install("transcription", "cpu", addon.acceptance(), lambda _: None)
    assert not addons.available("transcription")


def test_different_tier_cannot_adopt_active_job(monkeypatch):
    cpu, gpu = addons.transcription_addons()
    jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription", "tier": "gpu"})
    with pytest.raises(addons.AddonError, match="Another tier"):
        addons.install(cpu.name, cpu.tier, cpu.acceptance(), lambda _: None)
    assert gpu.tier == "gpu"


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_unknown_addon_returns_not_found(method):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, "/api/addons/unknown" + ("/install" if method == "POST" else ""),
                                        json={"tier": "cpu", "acceptance": "none"})
    assert asyncio.run(request()).status_code == 404


@pytest.mark.parametrize("overrides", [{}, {"HF_HOME": "custom-hub"}, {"TORCH_HOME": "custom-torch"},
                                      {"XDG_CACHE_HOME": "custom-cache"}])
def test_source_caches_keep_defaults_and_explicit_overrides(monkeypatch, overrides):
    monkeypatch.setattr(models, "bundle_root", lambda: None)
    for key in ("HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    models.configure_caches()
    assert dict(os.environ) == before


def test_second_addon_uses_its_own_tier_jobs_and_cache_ownership(monkeypatch):
    monkeypatch.setattr(addons, "bundle_root", lambda: config.STATE_HOME / "bundle")
    cpu, _ = installed(monkeypatch)
    second = replace(cpu, name="assistant", tier="standard", lock="requirements-agent.txt", index=None,
                     cache_directories=("models/assistant",), probe_modules=("assistant_module",),
                     busy_job_kinds=("assistant_task",), unavailable_message="Install the assistant add-on.")
    monkeypatch.setattr(addons, "definitions", lambda: (cpu, second))
    job_id = jobs.new_job("addon_install", "addon-assistant", {"addon": "assistant", "tier": "standard"})
    addons._run(second, second.acceptance(), job_id, lambda _: None)
    assert addons.active("assistant") is not None
    addons.activate()
    first_record, second_record = addons.active("transcription"), addons.active("assistant")
    assert first_record is not None and second_record is not None
    first_directory = addons._root("transcription") / first_record["directory"]
    second_directory = addons._root("assistant") / second_record["directory"]
    assert sys.path.index(str(first_directory)) < sys.path.index(str(second_directory))
    monkeypatch.setattr(addons, "_activated", {})
    assert addons.status("assistant")["tier"] == "standard"
    assert addons.unavailable_message("assistant") == "Install the assistant add-on."
    busy = jobs.new_job("assistant_task", "video", {})
    with pytest.raises(addons.AddonError, match="finish"):
        addons.remove("assistant")
    jobs.set(busy, status="done")
    owned = config.STATE_HOME / "models/assistant"
    owned.mkdir(parents=True)
    other = config.STATE_HOME / "models/huggingface"
    other.mkdir()
    (other / "model").write_bytes(b"other")
    addons.remove("assistant")
    assert not owned.exists() and (other / "model").exists()
    assert addons.active("transcription") is not None


def test_gpu_probe_is_cached_for_process_lifetime(monkeypatch):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="NVIDIA RTX A4000, 610.00, 16376")
    addons.detect_gpus.cache_clear()
    monkeypatch.setattr(addons.subprocess, "run", run)
    assert addons.detect_gpus() == addons.detect_gpus()
    assert len(calls) == 1
    addons.detect_gpus.cache_clear()
