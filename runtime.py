"""Runtime identity and state-home resolution for Cutgraph Studio.

The standalone local Studio owns its state home and executes jobs locally.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


STATE_HOME_ENV = "CUTGRAPH_STATE_HOME"
APPLICATION_ID = "standalone"


@dataclass(frozen=True)
class RuntimePaths:
    """The immutable code location and mutable runtime location for one process."""

    code_root: Path
    state_home: Path


def default_state_home(
    environ: Mapping[str, str] | None = None, *, platform: str | None = None,
) -> Path:
    """Return the per-user state location for the host platform."""
    env = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cutgraph Studio"
    if platform != "win32":
        xdg_state = env.get("XDG_STATE_HOME", "")
        base = Path(xdg_state) if xdg_state and Path(xdg_state).is_absolute() else Path.home() / ".local" / "state"
        return base / "cutgraph-studio"
    local_app_data = env.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Cutgraph Studio"


def resolve_runtime(
    code_root: Path, environ: Mapping[str, str] | None = None, *, platform: str | None = None,
) -> RuntimePaths:
    """Resolve and validate the process runtime without touching the filesystem."""
    env = os.environ if environ is None else environ
    raw_home = env.get(STATE_HOME_ENV, "").strip()
    state_home = Path(raw_home).expanduser() if raw_home else default_state_home(env, platform=platform)
    return RuntimePaths(code_root=Path(code_root).resolve(), state_home=state_home.resolve())
