"""Add-on acceptance, publication, recovery and hardware selection without downloads."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
import sys
import threading
from contextlib import nullcontext
from pathlib import Path

import httpx
import pytest

import config
from pipeline import addons, jobs, models, transcribe
from studio import compute
from studio.app import app


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(addons, "_activated", {})
    monkeypatch.setattr(addons, "_threads", {})
    monkeypatch.setattr(addons, "_stop", threading.Event())
    monkeypatch.setattr(addons, "source_tier", lambda *args: None)
    addons.detect_gpus.cache_clear()
    monkeypatch.setattr(jobs, "_registry", {})
    yield
    addons.shutdown()


def package(stage: Path) -> None:
    metadata = stage / "speech-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: speech\nVersion: 1.0\n", encoding="utf-8")


def current(job_id: str) -> dict:
    job = jobs.get(job_id)
    assert job is not None
    return job


def attempt(monkeypatch, behavior=None, tier="cpu"):
    addon = addons.definition("transcription", tier)
    job_id = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription", "tier": tier})
    monkeypatch.setattr(addons, "_pip", behavior or (lambda addon, stage, *args: package(stage)))
    events = []
    addons._run(addon, addon.acceptance(), job_id, lambda jid: events.append(current(jid)["status"]))
    return addon, current(job_id), events


@pytest.mark.parametrize("acceptance", [None, "", "outdated", True])
def test_api_requires_exact_acceptance_before_job_or_download(monkeypatch, acceptance):
    monkeypatch.setattr(addons, "_pip", lambda *args: pytest.fail("must not download"))

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/addons/transcription/install", json={"tier": "cpu", "acceptance": acceptance})

    response = asyncio.run(request())
    assert response.status_code == 409
    assert "licence" in response.json()["error"]
    assert not jobs.all_persisted()


@pytest.mark.parametrize("body", [None, [], True, "cpu"])
def test_install_api_rejects_non_object_bodies(body):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/addons/transcription/install", content=json.dumps(body),
                                     headers={"Content-Type": "application/json"})
    assert asyncio.run(request()).status_code == 400
    assert not jobs.all_persisted()


def test_gpu_declaration_and_lock_are_bound_to_acceptance():
    cpu, gpu = addons.transcription_addons()
    assert cpu.acceptance() != gpu.acceptance()
    assert {link["label"] for link in gpu.declaration()["licenses"]} >= {"NVIDIA CUDA EULA", "NVIDIA cuDNN licence"}
    assert all(model.id in str(cpu.declaration()) for model in (models.WHISPER, models.ALIGNMENT, models.SILERO))
    assert "NVIDIA" not in str(cpu.declaration())
    with pytest.raises(addons.AddonError, match="licence"):
        addons.install("transcription", "gpu", cpu.acceptance(), lambda _: None)


def test_gpu_install_refuses_unsupported_hardware(monkeypatch):
    addon = addons.definition("transcription", "gpu")
    monkeypatch.setattr(addons, "detect_gpus", lambda: [])
    with pytest.raises(addons.AddonError, match="supported NVIDIA"):
        addons.install("transcription", "gpu", addon.acceptance(), lambda _: None)
    assert not jobs.all_persisted()


def test_pip_uses_running_interpreter_and_binary_hash_lock(tmp_path):
    addon = addons.definition("transcription", "cpu")
    command = addons.pip_command(addon, tmp_path / "stage", tmp_path / "lock")
    assert command[:5] == [sys.executable, "-I", "-B", "-m", "pip"]
    assert {"--isolated", "--require-hashes", "--only-binary=:all:", "--no-compile"} <= set(command)
    assert command[command.index("--target") + 1] == str(tmp_path / "stage")
    assert "--no-deps" in command
    assert command[command.index("--index-url") + 1] == "https://pypi.org/simple"
    assert "--user" not in command
    assert "--extra-index-url" not in addons.pip_command(replace(addon, index=None), tmp_path, tmp_path / "lock")


def test_cancellation_terminates_pip_without_activating(monkeypatch, tmp_path):
    addon = addons.definition("transcription", "cpu")
    root = addons._root("transcription")
    root.mkdir(parents=True)
    lock = root / "lock.txt"
    lock.write_text("speech==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    (root / "stage").mkdir()
    job_id = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription"})
    jobs.compare_and_set_status(job_id, expected={"queued"}, status="running")
    jobs.cancel_job(job_id)
    calls = []
    class Process:
        pid = 42
        returncode = None
        def poll(self):
            return self.returncode
        def terminate(self):
            calls.append("terminate")
            self.returncode = 1
        def wait(self, **kwargs):
            calls.append("wait")
            return self.returncode
    def spawn(command, **kwargs):
        assert command[0] == sys.executable
        assert kwargs["env"]["PYTHONNOUSERSITE"] == "1"
        assert Path(kwargs["env"]["TEMP"]).is_relative_to(tmp_path)
        assert kwargs["cwd"].is_relative_to(root) and kwargs["cwd"].name == "target"
        return Process()
    monkeypatch.setattr(addons.subprocess, "Popen", spawn)
    monkeypatch.setattr(addons.installer_process, "start", lambda command, **kwargs: nullcontext(spawn(command, **kwargs)))
    monkeypatch.setattr(addons, "_process_identity", lambda pid: "creation")
    with pytest.raises(addons.AddonError, match="cancelled"):
        addons._pip(addon, root / "stage", lock, job_id, lambda _: None)
    assert calls == ["terminate", "wait"]
    assert addons.active("transcription") is None


def test_restart_retries_all_abandoned_download_cleanup():
    root = addons._root("transcription")
    for name in (".staging-abandoned", "temporary", "cache"):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "partial").write_bytes(b"partial")
    addons.startup()
    assert not (root / ".staging-abandoned").exists()
    assert not (root / "temporary").exists()
    assert not list((root / "cache").iterdir())


def test_install_records_versions_and_requires_restart(monkeypatch):
    addon, job, events = attempt(monkeypatch)
    record = addons.active("transcription")
    assert record is not None
    assert job["status"] == "done"
    assert events == ["running", "running", "publishing", "done"]
    assert record["packages"] == [{"name": "speech", "version": "1.0"}]
    assert record["acceptance"] == addon.acceptance()
    assert addons.status("transcription")["restartRequired"]
    assert not addons.available("transcription")
    monkeypatch.setattr(sys, "path", list(sys.path))
    addons.activate()
    assert addons.available("transcription")
    assert sys.path[-1] == str(addons._root("transcription") / record["directory"])
    assert not addons.status("transcription")["restartRequired"]


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_or_cancelled_install_never_activates(monkeypatch, cancel):
    def fail(addon, stage, lock, job_id, notify):
        package(stage)
        if cancel:
            jobs.cancel_job(job_id)
        else:
            raise OSError("download failed")

    _, job, _ = attempt(monkeypatch, fail)
    assert job["status"] == ("cancelled" if cancel else "error")
    assert addons.active("transcription") is None
    assert not list(addons._root("transcription").glob(".staging-*"))


def test_failed_update_preserves_previous_active_pointer(monkeypatch):
    cpu, _, _ = attempt(monkeypatch)
    before = (addons._root("transcription") / "active.json").read_bytes()
    def fail(*args):
        raise OSError("download failed")
    attempt(monkeypatch, fail, "gpu")
    assert (addons._root("transcription") / "active.json").read_bytes() == before
    record = addons.active("transcription")
    assert record is not None and record["lockSha256"] == cpu.digest


def test_changed_lock_reports_update_and_invalidates_acceptance(monkeypatch, tmp_path):
    addon, _, _ = attempt(monkeypatch)
    previous = addon.acceptance()
    (tmp_path / addon.lock).write_bytes(addon.lock_path.read_bytes() + b"\n")
    monkeypatch.setattr(config, "CODE_ROOT", tmp_path)
    assert addons.status("transcription")["state"] == "needs update"
    with pytest.raises(addons.AddonError, match="licence"):
        addons.install("transcription", "cpu", previous, lambda _: None)


def test_retry_is_a_new_attempt_and_reuses_wheel_cache(monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    def pip(addon, stage, lock, job_id, notify):
        entered.set()
        assert finish.wait(5)
        package(stage)
    monkeypatch.setattr(addons, "_pip", pip)
    addon = addons.definition("transcription", "cpu")
    first = addons.install("transcription", "cpu", addon.acceptance(), lambda _: None)
    assert entered.wait(5)
    assert addons.status("transcription")["state"] == "installing"
    assert addons.install("transcription", "cpu", addon.acceptance(), lambda _: None) == first
    assert jobs.cancel_job(first)[1]
    with pytest.raises(addons.AddonError, match="stopping"):
        addons.install("transcription", "cpu", addon.acceptance(), lambda _: None, retry=True)
    finish.set()
    for thread in list(addons._threads.values()):
        thread.join(5)
    monkeypatch.setattr(addons, "_pip", lambda addon, stage, *args: package(stage))
    cache = addons._root("transcription") / "cache"
    cache.mkdir()
    (cache / "verified-wheel").write_bytes(b"cache")
    second = addons.install("transcription", "cpu", addon.acceptance(), lambda _: None, retry=True)
    for thread in list(addons._threads.values()):
        thread.join(5)
    assert current(first)["status"] == "cancelled"
    assert current(second)["status"] == "done"
    assert current(second)["parentJobId"] == first
    assert not cache.exists()


def test_restart_recovery_does_not_activate_a_partial_target(monkeypatch):
    addon = addons.definition("transcription", "cpu")
    job_id = jobs.new_job("addon_install", "addon-transcription", {"addon": "transcription", "tier": "cpu"})
    jobs.compare_and_set_status(job_id, expected={"queued"}, status="running")
    jobs.recover_jobs()
    assert current(job_id)["status"] == "unknown_execution"
    assert not addons.available("transcription")
    assert addons.status("transcription")["state"] == "not installed"
    with pytest.raises(addons.AddonError):
        addons.install("../escape", "cpu", addon.acceptance(), lambda _: None)


def test_remove_frees_packages_and_cached_downloads(monkeypatch):
    attempt(monkeypatch)
    monkeypatch.setattr(addons, "bundle_root", lambda: config.STATE_HOME / "bundle")
    directory = config.STATE_HOME / "models" / "huggingface"
    directory.mkdir(parents=True)
    (directory / "checkpoint").write_bytes(b"model")
    addons.remove("transcription")
    assert not addons._root("transcription").exists()
    assert not directory.exists()
    assert addons.status("transcription")["state"] == "not installed"


def test_pending_removal_is_unavailable_in_health(monkeypatch):
    attempt(monkeypatch)
    addons.activate()
    addons.remove("transcription")
    from studio.routes.system import health

    status = health()["capabilities"]["transcription"]
    assert status["state"] == "removing"
    assert not status["available"]
    assert status["restartRequired"]
    assert status["installedBytes"] > 0


@pytest.mark.parametrize("output,expected", [
    ("", []),
    ("NVIDIA GeForce RTX 3080, 566.36, 10240", [False]),
    ("NVIDIA RTX A4000, 570.65, 16376", [True]),
    ("NVIDIA GeForce GTX 1650, 610.00, 4096", [False]),
    ("NVIDIA GeForce RTX 3070, 610.00, 8188", [True]),
    ("NVIDIA GeForce GTX 1650, 610.00, 4096\nNVIDIA RTX A4000, 610.00, 16376", [False, True]),
    ("invalid,N/A,N/A", []),
])
def test_recorded_gpu_outputs(output, expected):
    gpus = addons.parse_gpus(output, platform="win32")
    assert [gpu["supported"] for gpu in gpus] == expected
    assert [gpu["index"] for gpu in gpus] == list(range(len(expected)))


def test_missing_nvidia_smi_is_cpu(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(addons.subprocess, "run", missing)
    assert addons.detect_gpus() == []


def test_inference_selects_supported_gpu_in_pci_order(monkeypatch):
    from types import SimpleNamespace
    from pipeline import runtime_tools

    monkeypatch.setattr(runtime_tools, "bundle_root", lambda: config.STATE_HOME / "bundle")
    selected = []
    monkeypatch.setattr(addons, "detect_gpus", lambda: addons.parse_gpus(
        "NVIDIA GeForce GTX 1650, 610.00, 4096\nNVIDIA RTX A4000, 610.00, 16376", platform="win32"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True, set_device=selected.append,
        mem_get_info=lambda index=1: (8 * 1024**3, 16 * 1024**3),
    )))
    assert transcribe._inference_settings() == ("cuda:1", "float16", 8)
    assert selected == [1]


def test_caches_are_redirected_before_any_model_download(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "bundle_root", lambda: tmp_path / "bundle")
    import os
    for key in ("HF_HOME", "HF_HUB_CACHE", "HF_ASSETS_CACHE", "HF_XET_CACHE", "HUGGINGFACE_HUB_CACHE",
                "TRANSFORMERS_CACHE", "TORCH_HOME", "NLTK_DATA", "MPLCONFIGDIR", "NUMBA_CACHE_DIR"):
        monkeypatch.setenv(key, "previous")
    models.configure_caches()
    for key in ("HF_HOME", "HF_HUB_CACHE", "HF_ASSETS_CACHE", "HF_XET_CACHE", "TORCH_HOME", "NLTK_DATA"):
        assert Path(os.environ[key]).is_relative_to(tmp_path)


def test_without_addon_compute_explains_installation(monkeypatch, tmp_path):
    monkeypatch.setattr(compute.helpers, "_source", lambda _: tmp_path)
    result = compute._enqueue_compute_action("transcribe", "clip", source_slug="clip")
    assert result["code"] == "TRANSCRIPTION_NOT_INSTALLED"
    assert "Settings" in result["error"]
    assert not jobs.all_persisted()


def test_existing_transcript_job_stays_explainable_without_addon():
    job_id = jobs.new_job("transcribe", "clip", {})
    assert compute._transcript_lifecycle("clip")["state"] == "queued"
    jobs.set(job_id, status="error", code="DEPENDENCY_UNAVAILABLE")
    lifecycle = compute._transcript_lifecycle("clip")
    assert lifecycle["state"] == "failed"
    assert "unavailable" in lifecycle["message"].lower()


def test_retry_without_addon_explains_installation():
    job_id = jobs.new_job("transcribe", "clip", {"sourceSlug": "clip"}, status="error")
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(f"/api/jobs/{job_id}/retry")
    response = asyncio.run(request())
    assert response.status_code == 409
    assert response.json()["code"] == "TRANSCRIPTION_NOT_INSTALLED"
    assert "Settings" in response.json()["error"]
    assert len(jobs.all_persisted()) == 1
