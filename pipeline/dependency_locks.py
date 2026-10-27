"""Shared index policy for hash-locked optional dependencies."""
from __future__ import annotations

import re

PYPI_INDEX = "https://pypi.org/simple"
TORCH_PACKAGES = frozenset({"torch", "torchaudio", "torchvision"})


def requirements(payload: str) -> list[tuple[str, str, str]]:
    result = []
    pending = ""
    for raw in payload.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pending += line.removesuffix("\\").strip() + " "
        if line.endswith("\\"):
            continue
        match = re.match(r"([\w.-]+)==([^\s;]+)", pending)
        if not match or "--hash=sha256:" not in pending:
            raise ValueError("The dependency lock is invalid.")
        result.append((re.sub(r"[-_.]+", "-", match[1]).lower(), match[2], pending.strip()))
        pending = ""
    if pending:
        raise ValueError("The dependency lock is incomplete.")
    return result


def package_index(name: str, version: str, torch_index: str | None) -> str:
    if "+" in version and (name not in TORCH_PACKAGES or torch_index is None
                           or version.split("+", 1)[1] != torch_index.rsplit("/", 1)[1]):
        raise ValueError("A local package version has no matching reviewed index.")
    return torch_index if name in TORCH_PACKAGES and torch_index else PYPI_INDEX
