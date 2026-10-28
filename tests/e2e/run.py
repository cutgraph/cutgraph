"""Run with the repository Python: python tests/e2e/run.py.

Prerequisites: ffmpeg and node on PATH, system Chrome, locked ui dependencies,
and a current UI build (run `npm run build` in ui first). This runner builds no UI.
It generates a synthetic fixture, copies it to temporary state, and runs each
spec with the Playwright library from ui/devDependencies. No pytest collection.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check_build() -> None:
    index = ROOT / "ui/dist/index.html"
    inputs = [ROOT / "ui/package.json", ROOT / "ui/package-lock.json"]
    for folder in ("ui/src", "ui/public", "motion-core/src"):
        inputs.extend(p for p in (ROOT / folder).rglob("*") if p.is_file())
    inputs.extend((ROOT / "ui").glob("*config*"))
    inputs.append(ROOT / "ui/index.html")
    if not index.is_file() or any(p.stat().st_mtime > index.stat().st_mtime for p in inputs):
        raise RuntimeError("UI build missing or stale: run npm run build in ui first")


def taskkill_command(pid: int) -> list[str]:
    # A bare program name would let CreateProcess search the working directory and PATH first.
    system_root = Path(os.environ.get("SystemRoot", ""))
    if not system_root.is_absolute():
        raise OSError("SystemRoot is not an absolute path")
    return [str(system_root / "System32" / "taskkill.exe"), "/PID", str(pid), "/T", "/F"]


def stop_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(taskkill_command(process.pid), check=True, capture_output=True, timeout=20)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def stop_server(process: subprocess.Popen, port: int) -> None:
    try:
        stop_tree(process)
    finally:
        with socket.socket() as probe:
            probe.settimeout(2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Studio port still accepts connections after cleanup")


def run_child(command: list[str], env: dict[str, str], timeout: int) -> int:
    process = subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=os.name != "nt")
    try:
        return process.wait(timeout=timeout)
    finally:
        stop_tree(process)


def minimal_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
    # Playwright finds system Chrome through these installation directories.
    allowed.update({"PROGRAMFILES", "PROGRAMFILES(X86)"})
    allowed.add("E2E_SCREENSHOT_DIR")
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def run_specs(temp: Path, state: Path, specs: list[Path]) -> int:
    env = {**minimal_environment(), "CUTGRAPH_STATE_HOME": str(state), "PYTHONDONTWRITEBYTECODE": "1"}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env["E2E_BASE_URL"] = base
    env["PYTHONUNBUFFERED"] = "1"
    with (temp / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "scripts/studio_app.py", "--no-browser", "--port", str(port)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError(f"Studio exited with code {server.returncode}")
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.25)
            else:
                raise TimeoutError("Studio health probe timed out")
            results = []
            for spec in specs:
                code = run_child(["node", str(spec)], env, 420 if spec.stem == "export" else 120)
                print(f"{spec.name}: exit {code}", flush=True)
                results.append(code)
            return int(any(results))
        finally:
            stop_server(server, port)
            log.flush()
            print((temp / "server.log").read_text(encoding="utf-8"), end="")
            print("Studio process tree stopped", flush=True)


def run() -> int:
    check_build()
    specs = sorted((ROOT / "tests/e2e/specs").glob("*.mjs"))
    if not specs:
        raise RuntimeError("No browser specs found")
    with tempfile.TemporaryDirectory(prefix="studio-e2e-") as temporary:
        temp = Path(temporary)
        fixture = temp / "fixture"
        env = {**minimal_environment(), "CUTGRAPH_STATE_HOME": str(fixture), "PYTHONDONTWRITEBYTECODE": "1"}
        if run_child([sys.executable, str(ROOT / "tests/e2e/fixture.py"), str(fixture)], env, 120):
            return 1
        state = temp / "state"
        shutil.copytree(fixture, state)
        result = run_specs(temp, state, [spec for spec in specs if spec.stem != "first-run"])
        fresh = temp / "fresh"
        fresh.mkdir()
        (fresh / "fixture.json").write_text("{}", encoding="utf-8")
        first_run = run_specs(temp, fresh, [spec for spec in specs if spec.stem == "first-run"])
        return int(bool(result or first_run))


if __name__ == "__main__":
    raise SystemExit(run())
