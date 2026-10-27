"""On-demand, hash-locked capabilities with durable jobs and atomic activation."""
from __future__ import annotations

import csv
import errno
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import re
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import config
from pipeline import dependency_locks, installer_process, jobs, models
from pipeline.runtime_tools import bundle_root

# CUDA 12.8 GA's full driver support, rather than its restricted compatibility mode.
GPU_DRIVER_SOURCE = "https://docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/"
MIN_GPU_DRIVER = {"win32": (570, 65), "linux": (570, 26)}
MIN_GPU_MEMORY_MIB = 7680
ACTIVE_STATUSES = frozenset({"queued", "running", "publishing"})
EVENTS_CHANNEL = "@addons"
WHEEL_CACHE_MAX_AGE = 7 * 24 * 60 * 60
ERROR_MESSAGES = {
    "ADDON_BUILD_UNAVAILABLE": "This add-on build is unavailable for this computer. Update Studio.",
    "ADDON_INSTALL_FAILED": "Installation failed. Retry the download.",
    "ADDON_DOWNLOAD_FAILED": "Download or hash verification failed. Check the connection and retry. Completed downloads are kept for up to seven days.",
    "ADDON_HASH_FAILED": "A download failed hash verification. Retry to fetch the reviewed files.",
    "ADDON_NETWORK_FAILED": "The download connection failed. Check the connection and retry.",
    "ADDON_ACCEPTANCE_CHANGED": "The licence declaration changed. Accept it again before retrying.",
    "ADDON_DISK_FULL": "Installation ran out of disk space. Free space and retry.",
    "ADDON_STORAGE_FAILED": "Installation could not write its files. Check free space and access, then retry.",
    "ADDON_INTERRUPTED": "Studio closed during installation. Retry to reuse completed downloads kept for up to seven days.",
}
logger = logging.getLogger(__name__)
ASSISTANT_INCOMPLETE = "Assistant software is incomplete. Reinstall the assistant in Settings, Add-ons."


class AssistantUnavailable(RuntimeError):
    pass


class AddonError(ValueError):
    def __init__(self, message: str, *, code: str = "ADDON_INSTALL_FAILED", status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class Addon:
    name: str
    tier: str
    lock: str
    index: str | None
    approximate_bytes: int
    licenses: tuple[tuple[str, str], ...]
    probe_modules: tuple[str, ...] = ()
    busy_job_kinds: tuple[str, ...] = ()
    cache_directories: tuple[str, ...] = ()
    unavailable_message: str = "This add-on is unavailable. Open Settings, Add-ons."
    title: str = ""
    description: str = "Optional local capability"
    download_note: str = ""
    tier_label: str = ""
    tier_note: str = ""
    source_version_tiers: tuple[tuple[str, str, str], ...] = ()
    probe_files: tuple[str, ...] = ()

    @property
    def lock_path(self) -> Path:
        return config.CODE_ROOT / self.lock

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.lock_path.read_bytes()).hexdigest()

    def declaration(self) -> dict:
        return {
            "name": self.name, "tier": self.tier, "lockSha256": self.digest,
            "licenses": [{"label": label, "url": url} for label, url in self.licenses],
            "description": self.description, "downloadNote": self.download_note,
            "models": models.manifest()["models"] if self.name == "transcription" else [],
        }

    def acceptance(self) -> str:
        return hashlib.sha256(json.dumps(self.declaration(), sort_keys=True).encode()).hexdigest()


def transcription_addons() -> tuple[Addon, ...]:
    licenses = (("PyTorch licence and notices", "https://github.com/pytorch/pytorch/blob/v2.8.0/LICENSE"),
                ("faster-whisper (MIT)", "https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/LICENSE"),
                *models.license_links())
    options = {"probe_modules": ("torch", "whisperx", "silero_vad"),
               "busy_job_kinds": ("transcribe", "reframe"),
               "cache_directories": ("models/huggingface", "models/torch", "models/torchaudio",
                                     "models/nltk", "models/matplotlib", "models/numba"),
               "title": "Transcription", "description": "Local speech recognition and word timing",
               "source_version_tiers": (("torch", "+cu", "gpu"),),
               "download_note": "Speech models download on first transcription (about 3.5 GB). No media leaves this computer.",
               "unavailable_message": "Transcription is not installed. Open Settings, Add-ons to install it, then restart Studio."}
    return (
        Addon("transcription", "cpu", "requirements-local-ai-cpu.txt",
              "https://download.pytorch.org/whl/cpu", 4_000_000_000, licenses,
              tier_label="CPU", tier_note="CPU uses int8. Transcription can take longer than the recording; speed varies.", **options),
        Addon("transcription", "gpu", "requirements-local-ai-nvidia.txt",
              "https://download.pytorch.org/whl/cu128", 8_500_000_000,
              (*licenses, ("NVIDIA CUDA EULA", "https://docs.nvidia.com/cuda/archive/12.8.0/eula/index.html"),
               ("NVIDIA cuDNN licence", "https://docs.nvidia.com/deeplearning/cudnn/backend/v9.10.2/reference/eula.html")),
              tier_label="NVIDIA GPU", **options),
    )


def definitions() -> tuple[Addon, ...]:
    return (*transcription_addons(), Addon(
        "assistant", "standard", "requirements-agent.txt", None, 93_946_241,
        (("Anthropic Commercial Terms", "https://www.anthropic.com/legal/commercial-terms"),
         ("Anthropic Usage Policy", "https://www.anthropic.com/legal/aup")),
        probe_modules=("claude_agent_sdk", "mcp"),
        probe_files=(f"claude_agent_sdk/_bundled/{'claude.exe' if sys.platform == 'win32' else 'claude'}",),
        title="Assistant", description="Contains Anthropic software. Use your own Anthropic API key.",
        download_note="Measured on Windows x64: 94 MB download, 284 MB installed. Downloads from PyPI. API use sends prompts and requested context to Anthropic and is billed by the provider.",
        tier_label="Assistant", unavailable_message="Assistant isn't installed. Open Settings, Add-ons.",
    ))


def definition(name: str, tier: str) -> Addon:
    if name not in {addon.name for addon in definitions()}:
        raise AddonError("Unknown add-on.", status_code=404)
    for addon in definitions():
        if (addon.name, addon.tier) == (name, tier):
            return addon
    raise AddonError("Unknown add-on or tier.")


def unavailable_message(name: str) -> str:
    record = active(name)
    addon = next(addon for addon in definitions() if addon.name == name)
    if record and (not addon.lock_path.is_file() or not _compatible(record)
                   or record["acceptance"] != definition(name, record["tier"]).acceptance()):
        return "This add-on needs an update. Open Settings, Add-ons to update it, then restart Studio."
    if addon.probe_files:
        if record:
            if _activated.get(name) != _directory(record):
                return "Restart Studio to enable the assistant. Open Settings, Add-ons."
            if not _probe_files(addon, _child(_root(name), _directory(record))):
                return ASSISTANT_INCOMPLETE
        elif (directory := _source_directory(addon)) and not _probe_files(addon, directory):
            return ASSISTANT_INCOMPLETE
    return addon.unavailable_message


def _root(name: str) -> Path:
    if name not in {addon.name for addon in definitions()}:
        raise AddonError("Unknown add-on.", status_code=404)
    home = config.STATE_HOME.resolve()
    root = (home / "addons" / name).resolve()
    if not root.is_relative_to(home) or root == home:
        raise AddonError("Add-on storage is outside the state home.")
    return root


def _child(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise AddonError("Invalid add-on storage path.")
    return path


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def active(name: str) -> dict | None:
    root = _root(name)
    try:
        record = json.loads(_child(root, "active.json").read_text(encoding="utf-8"))
        digest = record["lockSha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None
        definition(name, record["tier"])
        installed = json.loads((_child(root, _directory(record)) / "install.json").read_text(encoding="utf-8"))
        if any(installed.get(key) != record.get(key) for key in ("name", "tier", "lockSha256", "acceptance")):
            return None
        return installed
    except (OSError, ValueError, KeyError, TypeError):
        return None


_activated: dict[str, str] = {}
_dll_handles: list[object] = []
_lock = threading.RLock()
_threads: dict[str, threading.Thread] = {}
_stop = threading.Event()
_sizes: dict[tuple[str, str], tuple[float, int]] = {}


def interpreter() -> dict:
    return {"version": list(sys.version_info[:2]), "cacheTag": sys.implementation.cache_tag,
            "abi": sysconfig.get_config_var("SOABI") or sys.implementation.cache_tag,
            "extensionSuffix": sysconfig.get_config_var("EXT_SUFFIX"), "debug": hasattr(sys, "gettotalrefcount"),
            "platform": sysconfig.get_platform()}


def _directory(record: dict) -> str:
    directory = record.get("directory", record["lockSha256"])
    if not isinstance(directory, str) or not re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{64}", directory):
        raise AddonError("Invalid add-on directory.")
    return directory


def _compatible(record: dict) -> bool:
    expected = interpreter()
    recorded = record.get("interpreter", {})
    keys = ("version", "cacheTag", "abi", "extensionSuffix", "debug", "platform")
    try:
        return (isinstance(recorded, dict) and all(recorded.get(key) == expected[key] for key in keys)
                and record.get("excludedCore") == core_versions())
    except (OSError, ValueError):
        return False


def core_versions() -> dict[str, str]:
    return _core_versions(config.CODE_ROOT)


@cache
def _core_versions(root: Path) -> dict[str, str]:
    return {name: version for name, version, _ in _requirements(
        (root / "requirements-core.txt").read_text(encoding="utf-8"))}


def activate(*names: str) -> None:
    """Run once before dependency imports. Never hot-swap loaded native modules."""
    models.configure_caches()
    for name in dict.fromkeys(names or (addon.name for addon in definitions())):
        record = active(name)
        if record and _compatible(record) and name not in _activated:
            directory = _child(_root(name), _directory(record))
            # Process standard wheel .pth files while preserving core and earlier add-on precedence.
            site.addsitedir(str(directory))
            _activated[name] = _directory(record)
            if sys.platform == "win32" and record["tier"] == "gpu":
                for libraries in (directory / "torch" / "lib", *directory.glob("nvidia/*/bin")):
                    if libraries.is_dir():
                        _dll_handles.append(os.add_dll_directory(str(libraries)))
                        # CTranslate2 locates torch's CUDA DLLs through PATH, inherited by Node and ffmpeg.
                        os.environ["PATH"] = str(libraries) + os.pathsep + os.environ.get("PATH", "")
    importlib.invalidate_caches()


def source_tier(name: str) -> str | None:
    """Keep existing source installations usable without silently adopting an add-on."""
    addon = next(a for a in definitions() if a.name == name)
    if _activated.get(name) or not addon.probe_modules:
        return None
    try:
        if not all(importlib.util.find_spec(module) for module in addon.probe_modules):
            return None
        if addon.probe_files:
            directory = _source_directory(addon)
            if not directory or not _probe_files(addon, directory):
                return None
        for distribution, marker, tier in addon.source_version_tiers:
            if marker in importlib.metadata.version(distribution):
                return tier
        return addon.tier
    except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
        return None


def _probe_files(addon: Addon, directory: Path) -> bool:
    return all((directory / relative).is_file() for relative in addon.probe_files)


def _source_directory(addon: Addon) -> Path | None:
    try:
        spec = importlib.util.find_spec(addon.probe_modules[0])
        return Path(spec.origin).parent.parent if spec and spec.origin else None
    except (ImportError, ValueError):
        return None


def available(name: str) -> bool:
    if not next(addon for addon in definitions() if addon.name == name).lock_path.is_file():
        return False
    record = active(name)
    if record:
        addon = definition(name, record["tier"])
        return (_compatible(record) and _activated.get(name) == _directory(record)
                and record["acceptance"] == addon.acceptance()
                and _probe_files(addon, _child(_root(name), _directory(record))))
    return name not in _activated and source_tier(name) is not None


def parse_gpus(output: str, *, platform: str | None = None) -> list[dict]:
    minimum = MIN_GPU_DRIVER.get(platform or sys.platform)
    result = []
    for index, row in enumerate(csv.reader(output.splitlines(), skipinitialspace=True)):
        if len(row) != 3:
            continue
        name, driver, memory = (part.strip() for part in row)
        try:
            version = tuple(int(part) for part in driver.split("."))
            memory_mib = int(memory)
        except ValueError:
            continue
        supported = minimum is not None and version >= minimum and memory_mib >= MIN_GPU_MEMORY_MIB
        result.append({"index": index, "name": name, "driver": driver, "memoryMiB": memory_mib,
                       "supported": supported})
    return result


@cache
def detect_gpus() -> list[dict]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_gpus(result.stdout) if result.returncode == 0 else []


def latest_job(name: str) -> dict | None:
    candidates = [job for job in jobs.all_persisted().values()
                  if job.get("kind") == "addon_install" and job.get("meta", {}).get("addon") == name]
    return max(candidates, key=lambda job: (job.get("createdAt", ""), job.get("createdOrdinal", 0)), default=None)


def status(name: str, *, job: dict | None = None, read_job: bool = True) -> dict:
    record = active(name)
    if read_job:
        job = latest_job(name)
    installed_tier = record["tier"] if record else source_tier(name)
    restart = bool(record and _compatible(record) and _activated.get(name) != _directory(record))
    state = installed_tier or "not installed"
    declared = next(a for a in definitions() if a.name == name)
    lock_available = declared.lock_path.is_file()
    if not lock_available:
        state = "unavailable"
    elif record and (not _compatible(record) or record["acceptance"] != definition(name, record["tier"]).acceptance()):
        state = "needs update"
    if job and job.get("status") in ACTIVE_STATUSES:
        state = "installing"
    removing = _child(_root(name), "remove.json").exists()
    if removing:
        state = "removing"
    return {"name": name, "state": state, "tier": installed_tier,
            "available": not removing and lock_available and available(name),
            "restartRequired": restart or (not record and name in _activated),
            "installedBytes": disk_usage(name),
            "sourceInstallation": bool(not record and installed_tier),
            "jobId": job["id"] if job else None}


def disk_usage(name: str) -> int:
    key = (str(config.STATE_HOME.resolve()), name)
    cached = _sizes.get(key)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    addon = next(a for a in definitions() if a.name == name)
    roots = [_root(name)]
    if bundle_root():
        roots.extend(config.STATE_HOME / directory for directory in _owned_caches(addon))
    total = 0
    for root in roots:
        if not root.resolve().is_relative_to(config.STATE_HOME.resolve()):
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
    _sizes[key] = (time.monotonic(), total)
    return total


def invalidate_size(name: str) -> None:
    _sizes.pop((str(config.STATE_HOME.resolve()), name), None)


def _requirements(payload: str) -> list[tuple[str, str, str]]:
    try:
        return dependency_locks.requirements(payload)
    except ValueError as error:
        raise AddonError(str(error)) from error


def install_requirements(payload: str) -> str:
    """Exclude core before installation so wheel RECORDs and namespaces remain intact."""
    core = core_versions()
    entries = _requirements(payload)
    for name, version, _ in entries:
        if name in core and core[name] != version:
            raise AddonError("The add-on lock conflicts with the core. Update Studio before installing.")
    return "\n".join(entry for name, _, entry in entries if name not in core) + "\n"


def pip_command(addon: Addon, stage: Path, lock: Path, *, index: str = "https://pypi.org/simple") -> list[str]:
    command = [sys.executable, "-I", "-B", "-m", "pip"]
    progress = "raw"
    if bundle := bundle_root():
        wheels = list((bundle / "addons").glob("pip-*.whl"))
        if len(wheels) != 1:
            raise AddonError("The bundled installer is missing. Update Studio before downloading add-ons.")
        command = [sys.executable, "-I", "-B", "-c",
                   "import runpy,sys; sys.path.append(sys.argv.pop(1)); runpy.run_module('pip',run_name='__main__')",
                   str(wheels[0])]
    else:
        try:
            version = importlib.metadata.version("pip")
        except importlib.metadata.PackageNotFoundError:
            version = "0.0"
        numbers = re.match(r"(\d+)\.(\d+)", version)
        if numbers is None or tuple(map(int, numbers.groups())) < (24, 1):
            progress = "off"
    command.extend(["--isolated", "--disable-pip-version-check", "install",
            "--require-hashes", "--only-binary=:all:", "--no-compile", "--progress-bar", progress,
            "--no-deps",
            "--target", str(stage), "--cache-dir", str(_root(addon.name) / "cache"),
               "--index-url", index, "-r", str(lock)])
    return command


def _process_identity(pid: int, *, terminate_token: str | None = None) -> str | None:
    """Verify the process creation time before terminating a persisted pip PID."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000 | 0x100000 | (1 if terminate_token is not None else 0), False, pid)
        if not handle:
            logger.warning("Discarding installer marker: recorded process cannot be opened (error %s).", ctypes.get_last_error())
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                logger.warning("Discarding installer marker: recorded process creation time is inaccessible.")
                return None
            kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            image = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(image))
            if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
                logger.warning("Discarding installer marker: recorded process image is inaccessible.")
                return None
            token = f"{(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}:{image.value.casefold()}"
            if token == terminate_token:
                if not kernel.TerminateProcess(handle, 1) or kernel.WaitForSingleObject(handle, 10000) != 0:
                    raise OSError("The interrupted installer is still stopping.")
            return token
        finally:
            kernel.CloseHandle(handle)
    try:
        ticks = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return f"{boot}:{ticks}:{Path(f'/proc/{pid}/exe').readlink()}"
    except (FileNotFoundError, PermissionError):
        return None


def _terminate_orphan(pid: int, token: str) -> None:
    if _process_identity(pid) != token:
        logger.warning("Discarding installer marker: process is absent, inaccessible or has a different identity.")
        return
    if sys.platform == "win32":
        _process_identity(pid, terminate_token=token)
    else:
        import signal

        os.kill(pid, signal.SIGKILL)


def _delete(path: Path, *, blocked: list[str] | None = None) -> bool:
    failed = None
    for _ in range(3):
        try:
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            return True
        except OSError as error:
            failed = Path(error.filename) if error.filename else path
            time.sleep(0.02)
    if blocked is not None and failed is not None:
        try:
            blocked.append(failed.relative_to(config.STATE_HOME).as_posix())
        except ValueError:
            blocked.append(path.name)
    return False


def _quarantine(path: Path) -> None:
    if _delete(path):
        return
    try:
        if not path.name.startswith(".quarantine-"):
            path.rename(path.with_name(".quarantine-" + path.name))
    except OSError:
        # The original staging name is also excluded from activation and retried next start.
        pass


def _cancelled(job_id: str) -> bool:
    return _stop.is_set() or (jobs.get(job_id) or {}).get("status") != "running"


def _pip(addon: Addon, stage: Path, lock: Path, job_id: str, notify: Callable[[str], None]) -> None:
    entries = _requirements(lock.read_text(encoding="utf-8"))
    partitions: dict[str, list[str]] = {}
    for name, version, entry in entries:
        index = dependency_locks.package_index(name, version, addon.index)
        partitions.setdefault(index, []).append(entry)
    with tempfile.TemporaryDirectory(prefix=".requirements-", dir=stage.parent) as directory:
        for ordinal, (index, selected) in enumerate(partitions.items()):
            selected_lock = Path(directory) / f"packages-{ordinal}.txt"
            selected_lock.write_text("\n".join(selected) + "\n", encoding="utf-8")
            # Merge separately installed partitions; shared console-script directories must not be skipped.
            target = Path(directory) / "target"
            target.mkdir()
            _pip_install(addon, target, selected_lock, job_id, notify, index)
            _merge_tree(target, stage)
            shutil.rmtree(target)


def _merge_tree(source: Path, target: Path) -> None:
    for path in source.iterdir():
        destination = target / path.name
        if path.is_dir() and destination.is_dir():
            _merge_tree(path, destination)
            path.rmdir()
        elif destination.exists():
            raise AddonError("The dependency partitions contain conflicting files. Update Studio.")
        else:
            os.replace(path, destination)


def _pip_install(addon: Addon, stage: Path, lock: Path, job_id: str,
                 notify: Callable[[str], None], index: str) -> None:
    root = _root(addon.name)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PIP_", "PYTHON")) and key not in {"VIRTUAL_ENV", "UV_INDEX"}}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["TEMP"] = environment["TMP"] = str(root / "temporary")
    Path(environment["TEMP"]).mkdir(exist_ok=True)
    with _child(root, "install.log").open("a", encoding="utf-8") as log:
        offset = log.tell()
        observed = 0
        with installer_process.start(
            pip_command(addon, stage, lock, index=index), stdout=log, stderr=subprocess.STDOUT, env=environment, cwd=stage,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        ) as process:
            try:
                token = _process_identity(process.pid)
                if token is not None:
                    _write(_child(root, "process.json"), {"pid": process.pid, "token": token, "jobId": job_id})
                while process.poll() is None:
                    if _cancelled(job_id):
                        process.terminate()
                        process.wait(timeout=10)
                        if _stop.is_set():
                            raise AddonError(ERROR_MESSAGES["ADDON_INTERRUPTED"], code="ADDON_INTERRUPTED")
                        raise AddonError("Installation cancelled.")
                    text = _child(root, "install.log").read_text(encoding="utf-8", errors="replace")
                    downloaded = download_bytes(text)
                    if downloaded != observed:
                        observed = downloaded
                        jobs.set(job_id, stageNote=f"Downloading verified wheels · {downloaded / 1024**2:.1f} MiB received",
                                 stageProgress=0.1 + 0.65 * downloaded / (downloaded + addon.approximate_bytes))
                        notify(job_id)
                    _stop.wait(0.25)
                if process.returncode:
                    text = _child(root, "install.log").read_text(encoding="utf-8", errors="replace")[offset:].lower()
                    code = ("ADDON_DISK_FULL" if "no space left" in text or "not enough space" in text or "winerror 112" in text
                            else "ADDON_BUILD_UNAVAILABLE" if "no matching distribution" in text or "could not find a version" in text
                            else "ADDON_HASH_FAILED" if "do not match the hashes" in text or "hash mismatch" in text
                            else "ADDON_NETWORK_FAILED" if any(word in text for word in ("connectionerror", "connection broken", "timed out", "proxyerror"))
                            else "ADDON_DOWNLOAD_FAILED")
                    raise AddonError(ERROR_MESSAGES[code], code=code)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                _child(root, "process.json").unlink(missing_ok=True)


def download_bytes(text: str) -> int:
    completed = previous = 0
    for match in re.finditer(r"Progress (\d+) of (\d+)", text):
        current = int(match[1])
        if current < previous:
            completed += previous
        previous = current
    return completed + previous


def _run(addon: Addon, acceptance: str, job_id: str, notify: Callable[[str], None]) -> None:
    stage: Path | None = None
    try:
        root = _root(addon.name)
        _, claimed = jobs.compare_and_set_status(job_id, expected={"queued"}, status="running",
                                                stageNote="Downloading verified wheels", stageProgress=0.1,
                                                startedAt=time.time())
        if not claimed:
            return
        notify(job_id)
        root.mkdir(parents=True, exist_ok=True)
        _cleanup_cache(addon.name)
        _child(root, "install.log").write_text("", encoding="utf-8")
        # Wheels remain cached across attempts; partial targets are never reused.
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
        payload = addon.lock_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if acceptance != addon.acceptance():
            raise AddonError(ERROR_MESSAGES["ADDON_ACCEPTANCE_CHANGED"], code="ADDON_ACCEPTANCE_CHANGED")
        with tempfile.TemporaryDirectory(prefix=".requirements-", dir=root) as directory:
            lock = Path(directory) / "requirements.txt"
            lock.write_text(install_requirements(payload.decode("utf-8")), encoding="utf-8")
            _pip(addon, stage, lock, job_id, notify)
        jobs.set(job_id, stageNote="Verifying installed packages", stageProgress=0.85)
        notify(job_id)
        packages = sorted([{"name": dist.metadata["Name"], "version": dist.version}
                           for dist in importlib.metadata.distributions(path=[str(stage)])], key=lambda p: p["name"])
        if not packages:
            raise AddonError("No installed packages were found.")
        size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
        identity = {"lockSha256": digest, "acceptance": acceptance, "interpreter": interpreter(),
                    "excludedCore": core_versions()}
        directory = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        record = {"name": addon.name, "tier": addon.tier, **identity, "directory": directory,
                  "declaration": addon.declaration(),
                  "acceptedAt": (jobs.get(job_id) or {}).get("meta", {}).get("acceptedAt", time.time()),
                  "installedBytes": size, "packages": packages, "installedAt": time.time(), "cachePurge": True,
                  "models": addon.declaration()["models"], "modelDelivery": "on first use"}
        _write(stage / "install.json", record)
        with _lock:
            if _stop.is_set():
                raise AddonError(ERROR_MESSAGES["ADDON_INTERRUPTED"], code="ADDON_INTERRUPTED")
            _, claimed = jobs.compare_and_set_status(job_id, expected={"running"}, status="publishing")
            if not claimed:
                return
            notify(job_id)
            target = _child(root, directory)
            if target.exists():
                shutil.rmtree(target)
            os.replace(stage, target)
            stage = None
            _write(_child(root, "active.json"), record)
            _quarantine(root / "cache")
            _quarantine(root / "temporary")
            _cleanup_versions(addon.name, record)
            jobs.compare_and_set_status(job_id, expected={"publishing"}, status="done", completedAt=time.time())
    except Exception as error:
        code = (error.code if isinstance(error, AddonError) else "ADDON_DISK_FULL"
                if isinstance(error, OSError) and error.errno == errno.ENOSPC else "ADDON_STORAGE_FAILED"
                if isinstance(error, OSError) else "ADDON_INSTALL_FAILED")
        if code not in ERROR_MESSAGES:
            code = "ADDON_INSTALL_FAILED"
        jobs.compare_and_set_status(job_id, expected={"queued", "running", "publishing"}, status="error",
                                    code=code, error=ERROR_MESSAGES[code], completedAt=time.time())
    finally:
        invalidate_size(addon.name)
        if stage is not None:
            _quarantine(stage)
        with _lock:
            _threads.pop(job_id, None)
        notify(job_id)


def install(name: str, tier: str, acceptance: str, notify: Callable[[str], None], *, retry: bool = False) -> str:
    addon = definition(name, tier)
    if not addon.lock_path.is_file():
        raise AddonError(ERROR_MESSAGES["ADDON_BUILD_UNAVAILABLE"], code="ADDON_BUILD_UNAVAILABLE")
    if acceptance != addon.acceptance():
        raise AddonError("Accept the current licence declaration before downloading this add-on.")
    if tier == "gpu" and not any(gpu["supported"] for gpu in detect_gpus()):
        raise AddonError("The GPU tier needs a supported NVIDIA driver and at least 8 GiB VRAM. Choose CPU instead.")
    if tier == "gpu" and os.environ.get("CUDA_VISIBLE_DEVICES") is not None:
        raise AddonError("GPU selection is overridden by CUDA_VISIBLE_DEVICES. Choose CPU or clear that override before restarting Studio.")
    with _lock:
        if _stop.is_set():
            raise AddonError("Studio is shutting down.")
        previous = latest_job(name)
        if previous and previous.get("status") in ACTIVE_STATUSES:
            if previous.get("meta", {}).get("tier") != tier:
                raise AddonError("Another tier is already installing. Cancel it before changing tiers.")
            return previous["id"]
        if any((jobs.get(job_id) or {}).get("meta", {}).get("addon") == name for job_id in _threads):
            raise AddonError("The previous download is stopping. Retry in a moment.")
        record = active(name)
        if name in _activated and not record or _child(_root(name), "remove.json").exists():
            try:
                blocked = json.loads((_root(name) / "remove.json").read_text(encoding="utf-8")).get("blockedPaths", [])
            except (OSError, ValueError):
                blocked = []
            detail = f" Locked files: {', '.join(blocked)}." if blocked else ""
            raise AddonError("Studio is still removing this add-on. Close apps holding its files, restart Studio and retry." + detail)
        if _child(_root(name), "process.json").exists():
            raise AddonError("The interrupted installer is still stopping. Restart Studio and retry.")
        if record and _compatible(record) and record["acceptance"] == addon.acceptance():
            raise AddonError("This add-on is already installed. Restart Studio to activate it.")
        if record and _compatible(record) and _activated.get(name) != _directory(record):
            raise AddonError("Restart Studio before changing the installed add-on again.")
        job_id = jobs.new_job("addon_install", EVENTS_CHANNEL, {"addon": name, "tier": tier,
                                                                  "acceptance": acceptance, "acceptedAt": time.time()})
        if retry and previous:
            jobs.set(job_id, parentJobId=previous["id"])
            jobs.set(previous["id"], retryChildJobId=job_id)
        thread = threading.Thread(target=_run, args=(addon, acceptance, job_id, notify), daemon=True,
                                  name=f"addon-{name}")
        _threads[job_id] = thread
        try:
            notify(job_id)
            thread.start()
        except Exception:
            _threads.pop(job_id, None)
            jobs.compare_and_set_status(job_id, expected={"queued"}, status="error", code="ADDON_INSTALL_FAILED",
                                        error=ERROR_MESSAGES["ADDON_INSTALL_FAILED"])
            notify(job_id)
        return job_id


def remove(name: str) -> None:
    addon = next((a for a in definitions() if a.name == name), None)
    if addon is None:
        raise AddonError("Unknown add-on.", status_code=404)
    with _lock:
        invalidate_size(name)
        job = latest_job(name)
        stopping = any((jobs.get(job_id) or {}).get("meta", {}).get("addon") == name for job_id in _threads)
        if job and job.get("status") in ACTIVE_STATUSES or stopping:
            raise AddonError("Cancel the installation and wait for it to stop before removing add-ons.")
        if any(job.get("kind") in addon.busy_job_kinds
                                          and job.get("status") in ACTIVE_STATUSES
                                          for job in jobs.all_persisted().values()):
            raise AddonError("Wait for jobs using this add-on to finish before removing it.")
        if not active(name) and source_tier(name):
            raise AddonError("Manage this source installation's packages in its Python environment.")
        root = _root(name)
        _quarantine(_child(root, "cache"))
        _child(root, "active.json").unlink(missing_ok=True)
        if root.exists() or name in _activated:
            _write(_child(root, "remove.json"), {"pending": True})
            if name not in _activated:
                _finish_removal(addon)


def _owned_caches(addon: Addon) -> tuple[str, ...]:
    shared = [Path("models/cache")]
    shared.extend(Path(directory) for other in definitions() if other.name != addon.name
                  for directory in other.cache_directories)
    return tuple(directory for directory in addon.cache_directories
                 if not any(Path(directory).is_relative_to(path) or path.is_relative_to(directory) for path in shared))


def _finish_removal(addon: Addon) -> None:
    root = _root(addon.name)
    complete = True
    blocked: list[str] = []
    for directory in _owned_caches(addon) if bundle_root() else ():
        path = config.STATE_HOME / directory
        if not path.resolve().is_relative_to(config.STATE_HOME.resolve()):
            complete = False
        elif not _delete(path, blocked=blocked):
            complete = False
    for path in root.iterdir() if root.exists() else ():
        if path.name != "remove.json" and not _delete(_child(root, path.name), blocked=blocked):
            complete = False
    if complete:
        _delete(root)
    else:
        _write(root / "remove.json", {"pending": True, "blockedPaths": blocked})


def _cleanup_versions(name: str, record: dict | None) -> None:
    if record is None:
        return
    keep = {_directory(record) if record else "", _activated.get(name, "")}
    root = _root(name)
    try:
        for path in root.iterdir() if root.exists() else ():
            if re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{64}", path.name) and path.name not in keep:
                _quarantine(_child(root, path.name))
    except (OSError, ValueError):
        pass


def _cleanup_cache(name: str) -> None:
    root = _root(name)
    cache_root = _child(root, "cache")
    record = active(name)
    job = latest_job(name)
    # The publish record covers a crash before the durable job reaches done.
    if ((job and job.get("status") == "done")
            or (record and record.get("cachePurge") and
                (not job or job.get("startedAt", 0) <= record.get("installedAt", 0)))):
        _quarantine(cache_root)
        return
    cutoff = time.time() - WHEEL_CACHE_MAX_AGE
    for path in cache_root.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(cache_root.resolve()):
            continue
        if not path.is_file():
            continue
        # pip publishes HTTP metadata/body pairs atomically. Adjacent temporary
        # files and incomplete pairs cannot be reused, even if they are recent.
        complete = (path.suffix == ".whl" or path.name == "origin.json"
                    or (path.suffix == ".body" and path.with_suffix("").is_file())
                    or (re.fullmatch(r"[0-9a-f]{56}|[0-9a-f]{64}", path.name)
                        and path.with_name(path.name + ".body").is_file()))
        if not complete or path.stat().st_mtime < cutoff:
            _delete(path)
    for path in sorted(cache_root.rglob("*"), reverse=True):
        if path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError:
                pass


def startup() -> None:
    _stop.clear()
    for name in dict.fromkeys(addon.name for addon in definitions()):
        try:
            root = _root(name)
        except (OSError, ValueError):
            continue
        try:
            for job in jobs.all_persisted().values():
                if job.get("kind") == "addon_install" and job.get("meta", {}).get("addon") == name:
                    jobs.compare_and_set_status(job["id"], expected=ACTIVE_STATUSES, status="error",
                                                code="ADDON_INTERRUPTED", error=ERROR_MESSAGES["ADDON_INTERRUPTED"])
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Interrupted add-on job recovery will be retried on restart.")
        process_record = root / "process.json"
        try:
            _child(root, "process.json")
            if process_record.exists():
                try:
                    process = json.loads(process_record.read_text(encoding="utf-8"))
                    _terminate_orphan(int(process["pid"]), str(process["token"]))
                except (ValueError, KeyError, TypeError):
                    logger.warning("Discarding an invalid installer marker.")
                process_record.unlink()
        except ValueError:
            logger.warning("Discarding an invalid installer marker path.")
            _delete(process_record)
        except OSError:
            # An identified child that has not stopped still owns its staging directory.
            logger.warning("Interrupted installer cleanup will be retried on restart.")
        try:
            if not process_record.exists() and _child(root, "remove.json").exists() and name not in _activated:
                _finish_removal(next(a for a in definitions() if a.name == name))
        except (OSError, ValueError):
            logger.warning("Add-on removal will be retried on restart.")
        if root.exists() and not process_record.exists():
            try:
                paths = (*root.glob(".staging-*"), *root.glob(".requirements-*"),
                         *root.glob(".quarantine-*"), root / "temporary")
            except OSError:
                paths = ()
                logger.warning("Add-on storage inspection will be retried on restart.")
            for path in paths:
                try:
                    _quarantine(_child(root, path.name))
                except (OSError, ValueError):
                    logger.warning("Add-on storage cleanup will be retried on restart.")
            try:
                _cleanup_cache(name)
            except (OSError, ValueError, TypeError):
                logger.warning("Add-on download cache cleanup will be retried on restart.")
            _cleanup_versions(name, active(name))
        invalidate_size(name)


def shutdown() -> None:
    with _lock:
        _stop.set()
        threads = list(_threads.values())
    for thread in threads:
        thread.join(timeout=15)
