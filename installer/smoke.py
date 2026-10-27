"""Exercise the portable runtime with only Windows system directories on PATH.

For fallback validation, CUTGRAPH_TEST_ENCODERS restricts the H.264 ladder to
comma-separated names without changing its order or adding unavailable encoders.
Restricted runs require --expected-encoder to check scene and export selection.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def isolated_environment(state: Path) -> dict[str, str]:
    keys = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "OS", "NUMBER_OF_PROCESSORS"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keys}
    system = Path(env["SYSTEMROOT"])
    env["PATH"] = os.pathsep.join(map(str, (system / "System32", system)))
    for key in ("TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        directory = state.parent / key.lower()
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    env.update(CUTGRAPH_STATE_HOME=str(state), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    # The same test-only restriction reaches both the Python compiler and Node scene renderer.
    if "CUTGRAPH_TEST_ENCODERS" in os.environ:
        env["CUTGRAPH_TEST_ENCODERS"] = os.environ["CUTGRAPH_TEST_ENCODERS"]
    return env


def make_fixture(bundle: Path, state: Path, env: dict[str, str]) -> None:
    """Use the e2e fixture's transcript/track shape with a lossless synthetic source."""
    state.mkdir()
    source = state / "data/source/synthetic.mkv"
    source.parent.mkdir(parents=True)
    subprocess.run([
        str(bundle / "ffmpeg/ffmpeg.exe"), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "2", "-c:v", "ffv1", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(source),
    ], env=env, check=True, capture_output=True, timeout=60)
    words = [{"word": word, "start": 0.1 + i * 0.3, "end": 0.35 + i * 0.3, "score": 1.0}
             for i, word in enumerate("Moving shapes with burned captions".split())]
    transcripts = state / "outputs/transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "synthetic.words.json").write_text(json.dumps({
        "language": "en", "duration": 2.0,
        "segments": [{"start": 0.1, "end": 1.55, "text": "Moving shapes with burned captions", "words": words}],
    }), encoding="utf-8")
    document = {
        "schemaVersion": 3, "name": "portable-smoke", "title": "Synthetic export", "revision": 1,
        "fps": 30, "width": 640, "height": 360, "transcript": None,
        "captions": {"enabled": True},
        "tracks": [{"id": "speech", "kind": "speech", "clips": [{
            "id": "source", "media": {"source": "data/source/synthetic.mkv", "proxy": None},
            "srcInF": 0, "srcOutF": 60, "tlStartF": 0, "gainDb": 0.0,
        }]}],
        "scenes": [{"id": "scene-1", "title": "Moving shapes", "role": "Hook", "startWord": 0,
                    "shot": {"mode": "animation", "pose": "cheer", "headline": "Portable rendering"}}],
    }
    projects = state / "outputs/projects"
    projects.mkdir(parents=True)
    (projects / "portable-smoke.json").write_text(json.dumps(document), encoding="utf-8")


def request(base: str, path: str, body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=payload, headers={
        "Origin": base, "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def stop_tree(process: subprocess.Popen) -> None:
    system = Path(os.environ["SYSTEMROOT"])
    subprocess.run([str(system / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                   capture_output=True, timeout=30)
    process.wait(timeout=30)


def wait_for_health(base: str, process: subprocess.Popen, log: Path, deadline: float) -> dict:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Studio exited during startup: " + log.read_text(errors="replace")[-4000:])
        try:
            return request(base, "/api/health")
        except (OSError, http.client.HTTPException):
            time.sleep(0.2)
    raise RuntimeError("Studio startup timed out")


def successful_encoders(attempts: dict[str, list[str]], expected: str | None) -> dict[str, str]:
    if not all(attempts.values()):
        raise RuntimeError("The successful scene and export encoders were not logged")
    successful = {role: names[-1] for role, names in attempts.items()}
    if expected and any(encoder != expected for encoder in successful.values()):
        raise RuntimeError(f"Expected {expected} for scene and export, got {successful}")
    return successful


def smoke(bundle: Path, temp_root: Path, timeout: float = 180, *, expected_encoder: str | None = None) -> dict:
    from pipeline.encoder_ladder import ORDER, TEST_ENCODERS_ENV, select_encoders
    from installer.manifest import verify_manifest

    if sys.platform != "win32":
        raise ValueError("The portable smoke test requires Windows")
    restriction = os.environ.get(TEST_ENCODERS_ENV, "")
    if restriction and not expected_encoder:
        raise ValueError("Restricted encoder smoke requires --expected-encoder")
    if expected_encoder and expected_encoder not in select_encoders(ORDER, "export", restriction):
        # Check names against both build policies, without probing a host executable.
        if expected_encoder not in select_encoders(set(ORDER) - {"libx264"}, "export", restriction):
            raise ValueError("The expected encoder must be allowed by the encoder restriction")
    bundle = bundle.resolve()
    before = verify_manifest(bundle)
    with tempfile.TemporaryDirectory(prefix="portable-smoke-", dir=temp_root) as temporary:
        work = Path(temporary)
        state = work / "state"
        env = isolated_environment(state)
        for name in ("python", "node", "ffmpeg", "ffprobe"):
            if shutil.which(name, path=env["PATH"]):
                raise ValueError(f"System-only PATH unexpectedly supplies {name}")
        make_fixture(bundle, state, env)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        command = [str(bundle / "python/pythonw.exe"), "-B", str(bundle / "app/scripts/studio_app.py"),
                   "--no-browser", "--port", str(port)]
        log = work / "server.log"
        with log.open("wb") as output:
            process = subprocess.Popen(command, cwd=bundle / "app", env=env, stdout=output, stderr=output)
            try:
                started = time.monotonic()
                deadline = started + timeout
                health = wait_for_health(base, process, log, deadline)
                startup_seconds = time.monotonic() - started
                if not all(health["capabilities"].get(name) is True for name in ("ffmpeg", "ffprobe", "cpuRender", "packaged")):
                    raise RuntimeError(f"Missing health capabilities: {health}")
                with urllib.request.urlopen(base + "/editor/", timeout=15) as editor:
                    if editor.status != 200 or b"<html" not in editor.read().lower():
                        raise RuntimeError("The built editor did not serve HTML")
                job = request(base, "/api/jobs", {"kind": "render_final", "project": "portable-smoke", "params": {}})
                job_id = job["jobId"]
                request(base, f"/api/jobs/{job_id}/approve", {})
                while True:
                    status = request(base, f"/api/jobs/{job_id}")
                    if status["status"] == "done":
                        break
                    if status["status"] in {"error", "failed", "cancelled", "interrupted"}:
                        raise RuntimeError(f"Export failed: {status}")
                    if time.monotonic() > deadline:
                        raise RuntimeError(f"Export timed out: {status}")
                    time.sleep(0.2)
                with urllib.request.urlopen(base + status["output"], timeout=15) as video:
                    if video.status != 200 or len(video.read()) < 1000:
                        raise RuntimeError("The exported video is missing or empty")
                commands = (state / "run.log").read_text(encoding="utf-8")
                if "ass=" not in commands or "fontsdir=" not in commands or "app/assets/fonts/inter" not in commands:
                    raise RuntimeError("The export did not burn captions using the bundled font directory")
                captions = list((state / "outputs/captions").glob("*.ass"))
                if not captions or not any("Dialogue:" in path.read_text(encoding="utf-8") for path in captions):
                    raise RuntimeError("The export produced no caption events")
                scenes = list((state / "outputs/scenes").glob("*.mp4"))
                if not scenes:
                    raise RuntimeError("The export did not render an animated scene")
                attempts: dict[str, list[str]] = {"scene": [], "export": []}
                for command in commands.splitlines():
                    match = re.search(r"-c:v (h264_\w+|libx264)\b", command)
                    if match:
                        role = "scene" if "-f rawvideo" in command else "export"
                        attempts[role].append(match[1])
                successful = successful_encoders(attempts, expected_encoder)
                result = {"health": health, "export": "done", "animatedScenes": len(scenes),
                          "burnedCaptions": True, "systemOnlyPath": env["PATH"], "bytes": before["size"],
                          "startupSeconds": round(startup_seconds, 3),
                          "encoderAttempts": re.findall(r"-c:v (h264_\w+|libx264)\b", commands),
                          "encoderAttemptsByRole": attempts,
                          "successfulEncoders": successful}
            finally:
                stop_tree(process)
        verify_manifest(bundle)
    return result


def main() -> int:
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--temp-root", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--expected-encoder", help="Assert this successful encoder for both scene and export")
    args = parser.parse_args()
    try:
        result = smoke(args.bundle, args.temp_root, expected_encoder=args.expected_encoder)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Bundle smoke failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr,
                  file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
