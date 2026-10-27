"""Exercise the native launcher with a real server and a windowless fake browser."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from installer.launcher.build import LAUNCHER, compile_launcher
from installer.smoke import isolated_environment


FAKE_BROWSER = """import json, os, pathlib, subprocess, sys, time
state = pathlib.Path(os.environ['CUTGRAPH_STATE_HOME'])
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])
try:
    escaped = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'],
                               creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)
except OSError as error:
    if error.winerror != 5:
        raise
    breakaway = 'denied'
else:
    (state / 'escaped.json').write_text(json.dumps({'pid': escaped.pid}))
    raise RuntimeError('A child escaped the launcher job')
(state / 'browser.json').write_text(json.dumps({'pid': os.getpid(), 'child': child.pid,
                                               'url': sys.argv[1], 'breakaway': breakaway}))
while not (state / 'close-browser').exists():
    time.sleep(.05)
"""


def wait_for(check: Callable, description: str, timeout: float = 45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise RuntimeError("Timed out: " + description)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def assert_stopped(pids: list[int], port: int) -> None:
    wait_for(lambda: all(not alive(pid) for pid in pids), "all owned processes stopped", timeout=20)
    with socket.socket() as connection:
        connection.settimeout(1)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError("The Studio listener survived shutdown")


def cleanup_launchers(processes: list[subprocess.Popen], observed: list[tuple[list[int], int]],
                      failure: BaseException | None) -> None:
    errors = []
    for process in processes:
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=20)
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(str(error))
    for pids, port in observed:
        try:
            assert_stopped(pids, port)
        except (OSError, RuntimeError) as error:
            errors.append(str(error))
    if errors:
        message = "Launcher cleanup failed: " + "; ".join(errors)
        if failure is not None:
            failure.add_note(message)
        else:
            raise RuntimeError(message)


def launch(exe: Path, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen([str(exe), "--headless"], cwd=exe.parent, env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW)


def clean_stop(exe: Path, env: dict[str, str]) -> None:
    subprocess.run([str(exe), "--stop", "--headless"], env=env, check=True, timeout=35,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def ready(state: Path, process: subprocess.Popen) -> tuple[dict, dict]:
    def check():
        if process.poll() is not None:
            log = state / "runtime/launcher.log"
            raise RuntimeError("Launcher exited: " + (log.read_text() if log.exists() else str(process.returncode)))
        info, browser = read_json(state / "runtime/launcher.json"), read_json(state / "browser.json")
        return (info, browser) if (info and browser and info["launcherPid"] == process.pid
                                   and info["browserPid"] == browser["pid"]) else None
    return wait_for(check, "launcher health and fake browser")


def smoke(bundle: Path, temp_root: Path) -> dict:
    from installer.build import SOURCE, outside_checkout
    from installer.manifest import verify_manifest

    if sys.platform != "win32":
        raise ValueError("Launcher lifecycle checks require Windows")
    temp_root = outside_checkout(temp_root, SOURCE)
    manifest = verify_manifest(bundle)
    cases = []
    with tempfile.TemporaryDirectory(prefix="launcher-smoke-", dir=temp_root) as temporary:
        work = Path(temporary)
        copied = work / "bundle"
        shutil.copytree(bundle, copied)
        exe = copied / LAUNCHER
        if not exe.exists():
            compile_launcher(SOURCE, copied, revision=manifest["sourceRevision"])
        fake = work / "browser.py"
        fake.write_text(FAKE_BROWSER, encoding="utf-8")
        state = work / "state"
        state.mkdir()
        env = isolated_environment(state)
        env["CUTGRAPH_LAUNCHER_BROWSER"] = str(copied / "python/python.exe")
        env["CUTGRAPH_LAUNCHER_BROWSER_ARGS"] = json.dumps([str(fake), "{url}"])
        processes = []
        recorded: list[tuple[list[int], int]] = []
        blocker = socket.socket()
        try:
            # An existing listener may belong to another app. Never connect to or stop it.
            try:
                blocker.bind(("127.0.0.1", 8765))
                blocker.listen()
            except OSError:
                blocker.close()
            first = launch(exe, env)
            processes.append(first)
            info, browser = ready(state, first)
            if browser["breakaway"] != "denied":
                raise RuntimeError("The launcher allowed job breakaway")
            pids = [first.pid, info["serverPid"], browser["pid"], browser["child"]]
            recorded.append((pids, info["port"]))
            if info["port"] == 8765 or not browser["url"].startswith("http://127.0.0.1:"):
                raise RuntimeError("The launcher did not use a free loopback port")
            cases.append("occupied preferred port falls back to owned loopback listener")
            other_env = {**env, "CUTGRAPH_STATE_HOME": str(work / "different-state")}
            subprocess.run([str(exe), "--headless"], env=other_env, check=True, timeout=10)
            wait_for(lambda: "Activate requested." in (state / "runtime/launcher.log").read_text(), "second launch activation")
            if read_json(state / "runtime/launcher.json") != info:
                raise RuntimeError("Second launch replaced the running instance")
            cases.append("second launch activates the same instance")
            (state / "close-browser").touch()
            if first.wait(timeout=20) != 0:
                raise RuntimeError("Closing the fake browser failed")
            assert_stopped(pids, info["port"])
            cases.append("browser close kills server and browser descendant")
            cases.append("child breakaway request is denied and contained children die with the tree")
            blocker.close()
            (state / "close-browser").unlink()
            (state / "browser.json").unlink()
            second = launch(exe, env)
            processes.append(second)
            info, browser = ready(state, second)
            pids = [second.pid, info["serverPid"], browser["pid"], browser["child"]]
            recorded.append((pids, info["port"]))
            second.kill()
            second.wait(timeout=20)
            assert_stopped(pids, info["port"])
            cases.append("forced launcher kill closes job and every descendant")
            # Fake-browser coverage proves stale status recovery; Edge profiles need a real browser check.
            (state / "browser.json").unlink()
            third = launch(exe, env)
            processes.append(third)
            info, browser = ready(state, third)
            pids = [third.pid, info["serverPid"], browser["pid"], browser["child"]]
            recorded.append((pids, info["port"]))
            clean_stop(exe, other_env)
            if third.poll() is None:
                raise RuntimeError("--stop returned before the launcher exited")
            if third.wait(timeout=20) != 0:
                raise RuntimeError("Clean stop failed")
            assert_stopped(pids, info["port"])
            cases.append("crash relaunch recovers stale status and clean stop empties job")
            clean_stop(exe, env)
            cases.append("clean stop is idempotent")
            for delay in (0, 2, 3):
                original = launch(exe, env)
                processes.append(original)
                old_info, old_browser = ready(state, original)
                old_pids = [original.pid, old_info["serverPid"], old_browser["pid"], old_browser["child"]]
                recorded.append((old_pids, old_info["port"]))
                log = state / "runtime/launcher.log"
                closing = log.read_text().count("Closing; activation")
                stopping = subprocess.Popen([str(exe), "--stop", "--headless"], env=env)
                processes.append(stopping)
                wait_for(lambda: log.read_text().count("Closing; activation") > closing, "shutdown handoff")
                racing_stop = subprocess.Popen([str(exe), "--stop", "--headless"], env=env)
                processes.append(racing_stop)
                time.sleep(delay)
                replacement = launch(exe, env)
                processes.append(replacement)
                new_info, new_browser = ready(state, replacement)
                recorded.append(([replacement.pid, new_info["serverPid"], new_browser["pid"], new_browser["child"]],
                                 new_info["port"]))
                for process in (stopping, racing_stop, original):
                    if process.wait(timeout=35) != 0:
                        raise RuntimeError("A stop raced the closing owner unsuccessfully")
                if any(alive(pid) for pid in old_pids):
                    raise RuntimeError("The closing tree survived replacement")
                clean_stop(exe, env)
                replacement.wait(timeout=20)
                assert_stopped(recorded[-1][0], new_info["port"])
            cases.append("shortcut clicks during shutdown at 0, 2 and 3 seconds start a fresh instance")
            cases.append("concurrent stops tolerate owner exit without stopping the replacement")
            original = launch(exe, env)
            processes.append(original)
            info, browser = ready(state, original)
            recorded.append(([original.pid, info["serverPid"], browser["pid"], browser["child"]], info["port"]))
            (state / "close-browser").touch()
            wait_for(lambda: not alive(browser["pid"]), "browser window closure")
            (state / "close-browser").unlink()
            replacement = launch(exe, env)
            processes.append(replacement)
            new_info, new_browser = ready(state, replacement)
            recorded.append(([replacement.pid, new_info["serverPid"], new_browser["pid"], new_browser["child"]],
                             new_info["port"]))
            if original.wait(timeout=20) != 0:
                raise RuntimeError("Window close failed during activation")
            clean_stop(exe, env)
            replacement.wait(timeout=20)
            assert_stopped(recorded[-1][0], new_info["port"])
            cases.append("a shortcut click immediately after browser close is retained")
            (copied / "app/scripts/studio_app.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
            failed = launch(exe, env)
            processes.append(failed)
            if failed.wait(timeout=20) != 1:
                raise RuntimeError("Startup crash did not fail clearly")
            if "local editor did not start" not in (state / "runtime/launcher.log").read_text():
                raise RuntimeError("Startup crash was not logged")
            cases.append("startup crash is reported without an orphan process")
            (copied / "app/scripts/studio_app.py").unlink()
            failed = launch(exe, env)
            processes.append(failed)
            if failed.wait(timeout=20) != 1:
                raise RuntimeError("Missing application file did not fail")
            cases.append("missing application file is reported")
        finally:
            blocker.close()
            escaped = read_json(state / "escaped.json")
            if escaped and alive(escaped["pid"]):
                subprocess.run(["taskkill.exe", "/PID", str(escaped["pid"]), "/T", "/F"],
                               capture_output=True, check=False, timeout=20)
            cleanup_launchers(processes, recorded, sys.exception())
        result = {"passed": len(cases), "cases": cases, "zeroProcessesAndListeners": True}
    verify_manifest(bundle)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--temp-root", type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args()
    try:
        print(json.dumps(smoke(args.bundle.resolve(), args.temp_root), indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Launcher smoke failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
