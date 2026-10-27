"""Local secrets protected by current-user DPAPI or private POSIX files."""
from __future__ import annotations

import ctypes
import os
import re
import stat
import tempfile
import threading
from pathlib import Path

import config

_ENTROPY = b"cutgraph-studio-secret-store-v1"
_WINDOWS = os.name == "nt"
_lock = threading.RLock()


class SecretStoreError(RuntimeError):
    pass


def _path(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        raise ValueError("invalid secret name")
    return config.RUNTIME_DIR / "secrets" / name


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    loader = getattr(ctypes, "WinDLL")
    crypt = loader("crypt32", use_last_error=True)
    kernel = loader("kernel32", use_last_error=True)
    buffers = [ctypes.create_string_buffer(value) for value in (data, _ENTROPY)]
    source, entropy = [_Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
                       for value, buffer in zip((data, _ENTROPY), buffers)]
    output = _Blob()
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_Blob)]
    fn.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise SecretStoreError("secret protection failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def metadata(name: str) -> tuple[str, int, int, int] | None:
    """Identity of the protected file, without opening or decrypting its contents."""
    path = _path(name)
    try:
        info = path.stat()
        return str(path), info.st_mtime_ns, info.st_size, info.st_ino
    except FileNotFoundError:
        return None


def get(name: str) -> str | None:
    with _lock:
        path = _path(name)
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as stream:
                mode = os.fstat(stream.fileno()).st_mode
                if not stat.S_ISREG(mode) or (not _WINDOWS and mode & 0o077):
                    raise SecretStoreError("secret file permissions are unsafe")
                data = stream.read()
            if _WINDOWS:
                data = _dpapi(data, decrypt=True)
            return data.decode("utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            raise SecretStoreError("could not read secret") from None


def set(name: str, value: str) -> None:
    with _lock:
        path = _path(name)
        if not isinstance(value, str):
            raise ValueError("secret must be a string")
        tmp: Path | None = None
        try:
            data = value.encode("utf-8")
            if _WINDOWS:
                data = _dpapi(data, decrypt=False)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not _WINDOWS:
                path.parent.chmod(0o700)
            fd, filename = tempfile.mkstemp(prefix=".secret-", dir=path.parent)
            tmp = Path(filename)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        except (OSError, UnicodeError):
            raise SecretStoreError("could not store secret") from None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)


def delete(name: str) -> None:
    with _lock:
        try:
            _path(name).unlink(missing_ok=True)
        except OSError:
            raise SecretStoreError("could not delete secret") from None
