"""Resolve executable and font resources for source checkouts and source bundles.

Source bundles carry ``ffmpeg/`` and ``node/`` beside ``app/``.
Source checkouts fall back to PATH.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping

import config
from pipeline.runtime_bundle import BUNDLE_MANIFEST


FFMPEG_ENV = "CUTGRAPH_FFMPEG"
FFPROBE_ENV = "CUTGRAPH_FFPROBE"
NODE_ENV = "CUTGRAPH_NODE"
TOOLS_DIR_ENV = "CUTGRAPH_TOOLS_DIR"

_TOOL_NAMES = {"ffmpeg": "ffmpeg.exe", "ffprobe": "ffprobe.exe"}
_FONT_FILES = {
    "sans": {"regular": "inter/Inter-Regular.ttf", "semibold": "inter/Inter-SemiBold.ttf",
             "bold": "inter/Inter-Bold.ttf", "black": "inter/Inter-Black.ttf"},
    "overlay": {"regular": "liberation-sans/LiberationSans-Regular.ttf",
                "bold": "liberation-sans/LiberationSans-Bold.ttf"},
    "mono": {"regular": "cascadia-code/CascadiaCode-Regular.ttf"},
}


def _root(source_root: Path | None = None) -> Path:
    return (source_root or Path(__file__).resolve().parents[1]).resolve()


def bundle_root(source_root: Path | None = None) -> Path | None:
    """Recognize an installed source bundle without searching checkout siblings."""
    root = _root(source_root)
    if root.name == "app" and (root.parent / BUNDLE_MANIFEST).is_file():
        return root.parent
    return None


def _existing_file(candidate: str | Path | None) -> Path | None:
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    return path.resolve() if path.is_file() else None


def _tool_override(name: str, environ: Mapping[str, str]) -> Path | None:
    env_name = FFMPEG_ENV if name == "ffmpeg" else FFPROBE_ENV
    raw = environ.get(env_name)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"Studio could not resolve {name}; {env_name} must name an existing absolute executable path.")
    return path.resolve()


def resolve_tool(
    name: str,
    *,
    source_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Return the best available executable for ``name``.

    Resolution is deterministic: explicit per-tool override, explicit tools
    directory, packaged runtime directory, and finally PATH.  ``None`` is
    returned instead of inventing a path, allowing a caller to report a useful
    capability error before starting a subprocess.
    """
    if name not in _TOOL_NAMES:
        raise ValueError(f"unsupported Cutgraph runtime tool: {name!r}")
    env = os.environ if environ is None else environ
    overridden = _tool_override(name, env)
    if overridden:
        return overridden

    filename = _TOOL_NAMES[name]
    tool_dirs: list[Path] = []
    if env.get(TOOLS_DIR_ENV):
        explicit_dir = Path(env[TOOLS_DIR_ENV]).expanduser()
        if not explicit_dir.is_absolute() or not explicit_dir.is_dir():
            raise RuntimeError(f"{TOOLS_DIR_ENV} must name an existing absolute tools directory.")
        tool_dirs.append(explicit_dir)
    if bundle := bundle_root(source_root):
        tool_dirs.append(bundle / "ffmpeg")
    for directory in tool_dirs:
        found = _existing_file(directory / filename)
        if found:
            return found
        # Linux/macOS source checkouts and test fixtures may provide the
        # extensionless names while Windows packaging uses .exe.
        found = _existing_file(directory / name)
        if found:
            return found
    found = shutil.which(name)
    if found and not Path(found).is_absolute():
        env_name = FFMPEG_ENV if name == "ffmpeg" else FFPROBE_ENV
        raise RuntimeError(f"Studio could not resolve {name}; set {env_name} to an existing absolute executable path.")
    return _existing_file(found)


_TOOLS: dict[str, Path] = {}


def initialize_tools(*, source_root: Path | None = None) -> None:
    """Resolve both executables once, before Studio accepts requests."""
    if _TOOLS:
        return
    resolved: dict[str, Path] = {}
    for name in _TOOL_NAMES:
        path = resolve_tool(name, source_root=source_root)
        if path is None or not path.is_absolute() or not path.is_file():
            env_name = FFMPEG_ENV if name == "ffmpeg" else FFPROBE_ENV
            raise RuntimeError(f"Studio could not resolve {name}; set {env_name} to an existing absolute executable path.")
        resolved[name] = path
    _TOOLS.update(resolved)


def tool_path(name: str) -> str:
    """Use the startup paths; standalone pipeline commands initialize on first use."""
    if name not in _TOOL_NAMES:
        raise ValueError("unsupported runtime tool")
    initialize_tools()
    return str(_TOOLS[name])


def ffmpeg_path(*, source_root: Path | None = None, environ: Mapping[str, str] | None = None) -> Path | None:
    """Resolve FFmpeg using :func:`resolve_tool`."""
    return resolve_tool("ffmpeg", source_root=source_root, environ=environ)


def ffprobe_path(*, source_root: Path | None = None, environ: Mapping[str, str] | None = None) -> Path | None:
    """Resolve FFprobe using :func:`resolve_tool`."""
    return resolve_tool("ffprobe", source_root=source_root, environ=environ)


def node_path(*, source_root: Path | None = None, environ: Mapping[str, str] | None = None) -> Path | None:
    """Resolve Node from an absolute override, the portable bundle, then PATH."""
    env = os.environ if environ is None else environ
    raw = env.get(NODE_ENV)
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise RuntimeError(f"{NODE_ENV} must name an existing absolute executable path.")
        return path.resolve()
    if bundle := bundle_root(source_root):
        bundled = _existing_file(bundle / "node" / "node.exe")
        if bundled:
            return bundled
    found = shutil.which("node", path=env.get("PATH", ""))
    if found and not Path(found).is_absolute():
        raise RuntimeError(f"Studio could not resolve Node; set {NODE_ENV} to an absolute executable path.")
    return _existing_file(found)


def font_path(
    style: str = "bold",
    *,
    role: str = "overlay",
    source_root: Path | None = None,
) -> Path:
    """Return the one bundled file for a role and weight."""
    if role not in _FONT_FILES:
        raise ValueError(f"unsupported Cutgraph font role: {role!r}")
    if style not in _FONT_FILES[role]:
        raise ValueError(f"unsupported Cutgraph font style: {style!r}")
    return _root(source_root or config.CODE_ROOT) / "assets" / "fonts" / _FONT_FILES[role][style]


def font_filter_path(
    style: str = "bold",
    *,
    role: str = "overlay",
    source_root: Path | None = None,
    directory: bool = False,
) -> str:
    """Return a quoted filter-safe bundled font file or its libass directory."""
    path = font_path(style, role=role, source_root=source_root)
    if not path.is_file():
        raise FileNotFoundError(f"Bundled {role} {style} font is missing: {path.name}")
    if directory:
        path = path.parent
    escaped = path.as_posix().replace(":", r"\:")
    return f"'{escaped}'"


def runtime_environment(
    *,
    source_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child-process environment with packaged tools first on PATH.

    The returned mapping is a copy; this function never mutates the parent
    process environment.  Existing PATH entries remain available for tools
    that are intentionally not bundled (for example a user-supplied encoder).
    """
    env = dict(os.environ if environ is None else environ)
    ffmpeg = ffmpeg_path(source_root=source_root, environ=env)
    ffprobe = ffprobe_path(source_root=source_root, environ=env)
    directories: list[str] = []
    for path in (ffmpeg, ffprobe):
        if path and str(path.parent) not in directories:
            directories.append(str(path.parent))
    if directories:
        existing = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(directories + ([existing] if existing else []))
    return env


def configure_process_environment(*, source_root: Path | None = None) -> None:
    """Install the resolved tools for libraries that discover their own executables."""
    initialize_tools(source_root=source_root)
    directories = list(dict.fromkeys(str(path.parent) for path in _TOOLS.values()))
    preferred = {os.path.normcase(directory) for directory in directories}
    remaining = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                 if os.path.normcase(entry) not in preferred]
    os.environ["PATH"] = os.pathsep.join(directories + remaining)


def capability_report(*, source_root: Path | None = None) -> dict[str, object]:
    """Return a user-safe availability report with no host paths or secrets."""
    return {
        "ffmpeg": ffmpeg_path(source_root=source_root) is not None,
        "ffprobe": ffprobe_path(source_root=source_root) is not None,
        "font": font_path(source_root=source_root).is_file(),
    }
