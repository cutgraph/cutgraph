"""Process-owned token files for the local compute bridge.

A file lives exactly as long as its Studio process: written at startup (or when the assistant first needs it) and removed
when the process stops. Sessions never remove it, so a closing session cannot pull it from one still running. A file left
by a killed process holds a dead token and is swept by the next start.
"""
from __future__ import annotations

import atexit
import os
import tempfile
import threading
from pathlib import Path

import config
from pipeline import agent_credentials, web_security

_lock = threading.RLock()
_files: set[Path] = set()
_PREFIX = "internal-token-"


def _running(pid: int) -> bool:
    """Whether a process with this id exists. Never signals it: on Windows `os.kill(pid, 0)` would terminate it."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists but belongs to someone else
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sweep(directory: Path) -> None:
    for stale in directory.glob(f"{_PREFIX}*"):
        suffix = stale.name.removeprefix(_PREFIX)
        if not (suffix.isascii() and suffix.isdigit() and 0 < int(suffix) < 2**31) or int(suffix) == os.getpid():
            continue
        try:
            if not _running(int(suffix)):
                stale.unlink(missing_ok=True)
        except (OSError, ValueError, OverflowError):
            continue


def create() -> Path:
    """Write once per process, including when an assistant reconnects after shutdown."""
    with _lock:
        directory = config.RUNTIME_DIR / "claude-agent"
        path = directory / f"{_PREFIX}{os.getpid()}"
        if path in _files and path.exists():
            return path
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "nt":
            agent_credentials._protect_directory(str(directory))
        else:
            directory.chmod(0o700)
        _sweep(directory)
        fd, filename = tempfile.mkstemp(dir=directory)
        temporary = Path(filename)
        try:
            if os.name == "nt":
                # Its own owner-only DACL, set before the token is written, rather than relying on inheritance.
                agent_credentials._protect_directory(filename, "D:P(A;;FA;;;OW)")
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(web_security.INTERNAL_TOKEN)
            os.replace(temporary, path)
            _files.add(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path


def cleanup() -> None:
    """Remove this process's file; one a reader holds open on Windows stays tracked for the exit retry."""
    with _lock:
        for path in list(_files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            _files.remove(path)


atexit.register(cleanup)
