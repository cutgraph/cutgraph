"""H.264 profiles shared with the scene renderer through schema/encoder-ladder.json.

For isolated tests and portable smokes only, CUTGRAPH_TEST_ENCODERS can restrict
the ladder to comma-separated encoder names. It never changes order, adds an
unavailable encoder, or changes the alpha renderer. An unset/empty value uses
the full ladder; unknown names are rejected.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from threading import Lock

import config
from pipeline.errors import ValidationError

DEFINITION_PATH = Path(__file__).resolve().parents[1] / "schema/encoder-ladder.json"
DEFINITION = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
ORDER: tuple[str, ...] = tuple(DEFINITION["order"])
TEST_ENCODERS_ENV = "CUTGRAPH_TEST_ENCODERS"
UNAVAILABLE_MESSAGE: str = DEFINITION["unavailableMessage"]
PROBE_TIMEOUT_MESSAGE: str = DEFINITION["probeTimeoutMessage"]
PROBE_FAILURE_MESSAGE: str = DEFINITION["probeFailureMessage"]
_ENCODER_LINE = re.compile(r"^\s*V[A-Z.]{5}\s+(\w+)\s", re.MULTILINE)
_PROBES: dict[str, frozenset[str]] = {}
_PROBE_LOCK = Lock()
_FAILED_INITS: dict[str, tuple[str, set[str]]] = {}
_FAILURE_LOCK = Lock()
PROBE_TIMEOUT = 30


class NoH264Encoder(ValidationError):
    def __init__(self) -> None:
        super().__init__(UNAVAILABLE_MESSAGE)


class EncoderDiscoveryError(ValidationError):
    pass


def log_ffmpeg(cmd: list[str]) -> None:
    with open(config.RUN_LOG, "a", encoding="utf-8") as stream:
        stream.write(" ".join(cmd) + "\n")


def parse_encoders(output: str) -> frozenset[str]:
    return frozenset(_ENCODER_LINE.findall(output))


def probe_encoders(ffmpeg: str) -> frozenset[str]:
    """Cache successful listings per executable path; transient failures can be retried."""
    with _PROBE_LOCK:
        if ffmpeg not in _PROBES:
            cmd = [ffmpeg, "-hide_banner", "-encoders"]
            log_ffmpeg(cmd)
            try:
                result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
            except subprocess.TimeoutExpired as error:
                raise EncoderDiscoveryError(PROBE_TIMEOUT_MESSAGE) from error
            except (OSError, subprocess.CalledProcessError) as error:
                raise EncoderDiscoveryError(PROBE_FAILURE_MESSAGE) from error
            _PROBES[ffmpeg] = parse_encoders(result.stdout)
        return _PROBES[ffmpeg]


def select_encoders(available: Collection[str], role: str, restriction: str = "") -> list[str]:
    allowed = set(ORDER)
    if restriction:
        allowed = {name.strip() for name in restriction.split(",")}
        if not allowed <= set(ORDER):
            raise ValueError(f"{TEST_ENCODERS_ENV} must contain only H.264 ladder encoder names")
    orders = DEFINITION["orders"]
    order = orders["withLibx264"][role] if "libx264" in available else orders["withoutLibx264"]
    return [name for name in order if name in available and name in allowed]


def uses_portable_profiles(ffmpeg: str) -> bool:
    return "libx264" not in probe_encoders(ffmpeg)


def encoder_args(role: str, encoder: str, values: Mapping[str, str] | None = None) -> list[str]:
    params = DEFINITION["defaults"] | dict(values or {})
    return ["-c:v", encoder, *(arg.format_map(params) for arg in DEFINITION["profiles"][role][encoder])]


def is_init_failure(encoder: str, stderr: str) -> bool:
    # Bare encoder names also appear in every configuration banner; they prove nothing.
    low = stderr.lower()
    unknown = f"unknown encoder '{encoder}'" in low or f'unknown encoder "{encoder}"' in low
    return unknown or any(phrase in low for phrase in DEFINITION["initFailures"][encoder])


def run_h264(
    ffmpeg: str, role: str, command: Callable[[str], list[str]], *, cwd: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Retry device/init failures only; leave input, filter, option and write errors intact."""
    available = probe_encoders(ffmpeg)
    encoders = select_encoders(available, role, os.environ.get(TEST_ENCODERS_ENV, ""))
    failed: set[str] = set()
    portable = "libx264" not in available
    with _FAILURE_LOCK:
        if role in _FAILED_INITS and _FAILED_INITS[role][0] != ffmpeg:
            del _FAILED_INITS[role]
        if portable and role not in _FAILED_INITS:
            _FAILED_INITS[role] = (ffmpeg, set())
        if portable:
            failed = _FAILED_INITS[role][1]
    for encoder in encoders:
        with _FAILURE_LOCK:
            if encoder in failed and encoder != encoders[-1]:
                continue
        cmd = command(encoder)
        log_ffmpeg(cmd)
        try:
            done = subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=cwd)
            return done, encoder
        except subprocess.CalledProcessError as error:
            if not is_init_failure(encoder, error.stderr or ""):
                raise
            if portable:
                with _FAILURE_LOCK:
                    failed.add(encoder)
    raise NoH264Encoder()
