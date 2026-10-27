"""Process-held ownership of a Studio state home, released even after a crash."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sys

_launcher_home: Path | None = None


class StateHomeInUse(RuntimeError):
    pass


@contextmanager
def launch(home: Path):
    global _launcher_home

    with acquire(home):
        _launcher_home = home.resolve()
        try:
            (home / "runtime" / "studio.url").unlink(missing_ok=True)
            yield
        finally:
            _launcher_home = None
            (home / "runtime" / "studio.url").unlink(missing_ok=True)


@contextmanager
def startup(home: Path):
    if _launcher_home == home.resolve():
        yield
    else:
        with acquire(home):
            yield


@contextmanager
def acquire(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    with (home / "studio.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise StateHomeInUse("Studio is already running with this state home. Close it before starting another instance.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def running_url(home: Path) -> str | None:
    try:
        url = (home / "runtime" / "studio.url").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    match = re.fullmatch(r"http://(?:127\.0\.0\.1|\[::1\]):([0-9]{1,5})/editor/", url)
    return url if match and 0 < int(match[1]) <= 65535 else None


def record_url(home: Path, host: str, port: int) -> str:
    host = "127.0.0.1" if host == "localhost" else "[::1]" if host == "::1" else host
    url = f"http://{host}:{port}/editor/"
    if host not in {"127.0.0.1", "[::1]"} or not 0 < port <= 65535:
        raise ValueError("Studio's running URL must use a loopback address and bound port.")
    directory = home / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "studio.url.tmp"
    temporary.write_text(url, encoding="utf-8")
    temporary.replace(directory / "studio.url")
    return url
