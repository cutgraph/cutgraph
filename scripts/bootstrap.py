"""Offline, read-only environment doctor for a source checkout."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Check:
    name: str
    required: bool
    ok: bool
    detail: str
    hint: str = ""


def _run(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                                encoding="utf-8", errors="replace", check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _importable(module: str, *, load: bool) -> bool:
    # Optional inference imports can initialize drivers or fetch assets; only locate them.
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            if load:
                importlib.import_module(module)
                return True
            return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _core_modules(root: Path) -> list[str]:
    aliases = {"pillow": "PIL.Image", "sse-starlette": "sse_starlette"}
    pins = re.findall(r"(?m)^([A-Za-z0-9_.-]+)==", (root / "requirements-core.in").read_text(encoding="utf-8"))
    return [aliases[name.lower()] if name.lower() in aliases else name.replace("-", "_") for name in pins]


def _python_check(root: Path) -> Check:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = project["project"]["requires-python"]
    minimum = re.fullmatch(r">=(\d+)\.(\d+)(?:\.(\d+))?", requirement)
    ok = bool(minimum and sys.version_info[:3] >= tuple(int(part or 0) for part in minimum.groups()))
    version = ".".join(str(part) for part in sys.version_info[:3])
    return Check("python", True, ok, f"{version}; requires {requirement}",
                 "Use the Python version in .python-version and recreate the virtual environment.")


def _key_presence() -> tuple[bool, str]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, "configured; source: environment (not verified)"
    from pipeline import secret_store

    try:
        present = bool(secret_store.get("anthropic-api-key"))
    except secret_store.SecretStoreError:
        return False, "could not read protected key"
    return present, "configured; source: Settings (not verified)" if present else "not configured; source: none"


def _writable(path: Path) -> bool:
    parent = path
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)


def collect_checks(root: Path = ROOT) -> list[Check]:
    from pipeline import runtime_tools
    from runtime import resolve_runtime

    checks = [_python_check(root)]
    modules = _core_modules(root)
    missing = [module for module in modules if not _importable(module, load=True)]
    checks.append(Check("core imports", True, bool(modules) and not missing,
                        "missing or unusable: " + ", ".join(missing) if missing else "core imports available",
                        "Use this virtual environment's Python to install requirements-core.txt with --require-hashes."))

    expected = (root / ".nvmrc").read_text(encoding="utf-8").strip().lstrip("v").split(".")[0]
    try:
        node = runtime_tools.node_path(source_root=root)
    except (OSError, RuntimeError):
        node = None
    output = _run([str(node), "--version"]) if node else None
    version = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", output or "")
    checks.append(Check("node", True, bool(version and version[1] == expected),
                        f"{version[0].strip()}; expected major {expected}" if version else f"unavailable; expected major {expected}",
                        "Install the Node version in .nvmrc and restart the terminal."))
    for folder in ("ui", "motion-core"):
        checks.append(Check(f"{folder} dependencies", True, (root / folder / "node_modules").is_dir(),
                            "present" if (root / folder / "node_modules").is_dir() else "missing",
                            f"Run npm --prefix {folder} ci."))

    tools: dict[str, Path | None] = {}
    resolvers = {"ffmpeg": runtime_tools.ffmpeg_path, "ffprobe": runtime_tools.ffprobe_path}
    for name, resolver in resolvers.items():
        try:
            path = resolver(source_root=root)
        except (OSError, RuntimeError):
            path = None
        tools[name] = path
        output = _run([str(path), "-version"]) if path else None
        version = re.search(rf"^{name} version (\S+)", output or "")
        checks.append(Check(name, True, version is not None,
                            f"version {version[1]}" if version else "unavailable or version probe failed",
                            f"Put {name} on PATH or set CUTGRAPH_{name.upper()} to its absolute executable path."))

    fonts = [("sans", style) for style in ("regular", "semibold", "bold", "black")]
    fonts += [("overlay", "regular"), ("overlay", "bold"), ("mono", "regular")]
    missing_fonts = [f"{role}/{style}" for role, style in fonts
                     if not runtime_tools.font_path(style, role=role, source_root=root).is_file()]
    checks.append(Check("bundled fonts", True, not missing_fonts,
                        "missing: " + ", ".join(missing_fonts) if missing_fonts else "all seven font files present",
                        "Restore the tracked assets/fonts files from the source distribution."))

    ffmpeg = tools["ffmpeg"]
    encoders = _run([str(ffmpeg), "-hide_banner", "-encoders"]) if ffmpeg else None
    has_x264 = bool(re.search(r"(?m)^\s*V\S*\s+libx264\s", encoders or ""))
    checks.append(Check("libx264", False, has_x264, "available" if has_x264 else "not detected",
                        "Optional for source installs; portable builds use hardware or Windows video encoding."))
    gpu = shutil.which("nvidia-smi")
    gpu_output = _run([gpu, "--query-gpu=name", "--format=csv,noheader"]) if gpu else None
    checks.append(Check("NVIDIA GPU", False, bool(gpu_output and gpu_output.strip()),
                        "detected" if gpu_output and gpu_output.strip() else "not detected",
                        "For CUDA transcription, install compatible NVIDIA hardware and drivers."))
    for name, modules, lock in (
        ("transcription pack", ("torch", "torchaudio", "torchvision", "whisperx", "silero_vad", "mediapipe", "cv2"),
         "requirements-local-ai-nvidia.txt"),
        ("assistant pack", ("claude_agent_sdk", "mcp"), "requirements-agent.txt"),
    ):
        missing = [module for module in modules if not _importable(module, load=False)]
        checks.append(Check(name, False, not missing,
                            "missing: " + ", ".join(missing) if missing else "packages present (not exercised)",
                            f"Install {lock} with --require-hashes; see docs/getting-started.md."))
    key_ok, key_detail = _key_presence()
    checks.append(Check("assistant key", False, key_ok, key_detail,
                        "Connect your API key in Settings or set ANTHROPIC_API_KEY."))
    home = resolve_runtime(root).state_home
    checks.append(Check("state home", False, _writable(home), str(home),
                        "Set CUTGRAPH_STATE_HOME to a writable directory; no directory was created."))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print structured check results")
    args = parser.parse_args(argv)
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        checks = collect_checks()
    finally:
        sys.dont_write_bytecode = before
    ok = all(check.ok for check in checks if check.required)
    if args.json:
        print(json.dumps({"ok": ok, "checks": [asdict(check) for check in checks]}, indent=2))
    else:
        for check in checks:
            status = "OK" if check.ok else "FAIL" if check.required else "ADVISORY"
            hint = f" Fix: {check.hint}" if not check.ok else ""
            print(f"{status} {check.name}: {check.detail}{hint}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
