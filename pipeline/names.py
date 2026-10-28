"""Portable names for request-derived files and directories."""
from __future__ import annotations

import os
import re
from collections.abc import Collection
from pathlib import Path

from pipeline.errors import ValidationError

_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,64}")
_EXISTING_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,200}")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])", re.IGNORECASE)


class NameConflict(FileExistsError):
    """A name would alias an existing entry on a case-insensitive filesystem."""


class NameNotFound(LookupError):
    """An existing path was requested with different spelling."""


def reserved_name(name: str) -> bool:
    return bool(_DEVICE.fullmatch(name.rstrip(". ").split(".", 1)[0].rstrip(" ")))


def safe_name(name: object) -> str | None:
    if not isinstance(name, str) or reserved_name(name) or not _SEGMENT.fullmatch(name):
        return None
    return name


def existing_name(name: object) -> str | None:
    if not isinstance(name, str) or reserved_name(name) or not _EXISTING_SEGMENT.fullmatch(name):
        return None
    return name


def validate_existing_name(name: str) -> str:
    if existing_name(name) is None:
        raise ValidationError("name must be 1-200 letters, digits, underscores or hyphens and cannot be a reserved device name")
    return name


def _default_root(path: Path) -> Path:
    """The deepest configured state folder holding `path`: a folder the user junctioned elsewhere is then the root
    itself, so its resolved location still contains the target."""
    import config

    roots = [Path(os.path.normpath(value.absolute())) for name, value in vars(config).items()
             if isinstance(value, Path) and (name == "STATE_HOME" or name.endswith("_DIR"))]
    holding = [root for root in roots if path.is_relative_to(root)]
    return max(holding, key=lambda root: len(root.parts)) if holding else Path(path.anchor)


def _realpath(path: Path) -> Path:
    """`path.resolve()` without the `\\\\?\\` prefix Windows can return when an entry changes during resolution."""
    real = os.path.realpath(path)
    if real.startswith("\\\\?\\") and not str(path).startswith("\\\\?\\"):
        real = "\\\\" + real[8:] if real.startswith("\\\\?\\UNC\\") else real[4:]
    return Path(real)


def _target(path: Path) -> Path:
    """Where `path` leads, without opening the entry itself: on Windows that open makes a concurrent atomic replace of
    it fail. Its folder is resolved; the entry only when directory metadata reports a link."""
    if path.name in ("", ".."):
        return _realpath(path)
    leaf = _realpath(path.parent) / path.name
    try:
        # Path-based link checks can open even regular files without delete sharing on Windows.
        with os.scandir(leaf.parent) as entries:
            linked = any(entry.name == leaf.name and (entry.is_symlink() or entry.is_junction())
                         for entry in entries)
    except OSError:
        linked = False
    return _realpath(leaf) if linked else leaf


def exact_path(path: Path, root: Path | None = None, *, resolved: Path | None = None) -> Path:
    """`path`, with every component below its trusted root spelled exactly as on disk and its resolved target inside
    the resolved root."""
    absolute = Path(os.path.normpath(path.absolute()))
    trusted = Path(os.path.normpath((root if root is not None else _default_root(absolute)).absolute()))
    target = resolved if resolved is not None else _target(path)
    try:
        requested_parts = absolute.relative_to(trusted).parts
        target.relative_to(_realpath(trusted))
    except ValueError:
        raise NameNotFound("The requested item was not found.") from None
    parent = trusted
    for part in requested_parts:
        try:
            entries = os.listdir(parent)
        except OSError:
            break
        if part not in entries:
            if any(entry.casefold() == part.casefold() for entry in entries):
                raise NameNotFound("The requested item was not found.")
            break
        parent /= part
    return path


def generated_name(name: str, suffix: str) -> bool:
    return bool(suffix) and name.endswith(suffix) and source_name(name[:-len(suffix)]) is not None


def source_name(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    if existing_name(name) is not None:
        return name
    match = re.fullmatch(r"(.+)(_take[1-9][0-9]*|_script)", name)
    if match:
        return name if existing_name(match[1]) else None
    return None


def project_name(name: object, *, creating: bool = False) -> str | None:
    if not isinstance(name, str):
        return None
    literal = safe_name(name) if creating else existing_name(name)
    if literal is not None:
        return literal
    match = re.fullmatch(r"(.+)__[1-9][0-9]*x[1-9][0-9]*", name)
    return name if match and existing_name(match[1]) else None


def stored_name(name: object) -> str | None:
    """A name an existing file or folder may carry: a source, take, script, Video or render."""
    return project_name(name) or source_name(name)


def existing_filename(raw: str, suffixes: Collection[str]) -> str | None:
    path = Path(raw)
    if "/" in raw or "\\" in raw or path.suffix.lower() not in suffixes or source_name(path.stem) is None:
        return None
    return raw


def validate_name(name: str) -> str:
    if safe_name(name) is None:
        raise ValidationError("name must be 1-64 letters, digits, underscores or hyphens and cannot be a reserved device name")
    return name


def filename(raw: str, suffixes: Collection[str]) -> str | None:
    if "/" in raw or "\\" in raw:
        return None
    path = Path(raw)
    ext = path.suffix.lower()
    if ext not in suffixes:
        return None
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_")[:64]
    return f"{stem}{ext}" if safe_name(stem) else None


def check_case_collision(path: Path, *, source_stem: bool = False) -> None:
    if path.parent.is_dir():
        for entry in path.parent.iterdir():
            if source_stem and entry.name != path.name and entry.stem == path.stem:
                raise NameConflict("a file with that name already exists")
            if entry.name != path.name and (
                entry.name.casefold() == path.name.casefold()
                or source_stem and entry.stem.casefold() == path.stem.casefold()
            ):
                raise NameConflict("name differs from an existing name only by case")


def check_display_collision(name: str, existing: list[str]) -> None:
    if any(value != name and value.casefold() == name.casefold() for value in existing):
        raise NameConflict("name differs from an existing name only by case")
