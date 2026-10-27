"""Character pack metadata and the source files that invalidate render caches."""
from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from collections.abc import Collection, Iterator
from pathlib import Path

_LOG = logging.getLogger(__name__)
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def _ids(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(s, str) and _ID.fullmatch(s) for s in value)


def _strings(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(s, str) for s in value.values())


def _valid_rig(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if (not isinstance(value.get("id"), str) or not _ID.fullmatch(value["id"])
            or not isinstance(value.get("name"), str) or not value["name"]
            or value.get("owner") not in ("brand", "client")
            or not _ids(value.get("palettes")) or not value["palettes"]
            or not isinstance(value.get("parts"), dict)
            or not all(isinstance(part, str) and _ID.fullmatch(part) and _ids(options) and options
                       for part, options in value["parts"].items())
            or not all(_ids(value.get(key)) for key in ("expressions", "poses", "moves"))
            or not value["expressions"]):
        return False
    if "labels" in value:
        labels = value["labels"]
        if (not isinstance(labels, dict) or not _strings(labels.get("titles"))
                or not isinstance(labels.get("options"), dict)
                or not all(_strings(options) for options in labels["options"].values())):
            return False
    if "head" in value:
        head = value["head"]
        if (not isinstance(head, dict)
                or not all(type(head.get(key)) in (int, float) and math.isfinite(head[key])
                           for key in ("rows", "foot", "span", "cols", "centre"))
                or not all(head[key] > 0 for key in ("rows", "span", "cols"))):
            return False
    return True


def _complete(dir: Path) -> Iterator[Path]:
    try:
        children = sorted(dir.iterdir())
    except FileNotFoundError:
        return
    except OSError as error:
        _LOG.warning("Skipping character packs: %s", error)
        return
    for path in children:
        try:
            if path.is_dir() and (path / "pack.json").is_file() and (path / "index.ts").is_file():
                yield path
        except OSError as error:
            _LOG.warning("Skipping character pack %s: %s", path.name, error)


def _packs(dir: Path, builtin_ids: Collection[str]) -> Iterator[tuple[Path, list[dict]]]:
    candidates: list[tuple[Path, list[dict]]] = []
    counts: Counter[str] = Counter()
    for path in _complete(dir):
        try:
            if not _ID.fullmatch(path.name):
                raise ValueError("invalid pack id")
            manifest = json.loads((path / "pack.json").read_text(encoding="utf-8"))
            if (not isinstance(manifest, dict) or not isinstance(manifest.get("characters"), list)
                    or not all(_valid_rig(rig) for rig in manifest["characters"])):
                raise ValueError("malformed manifest")
            rigs: list[dict] = manifest["characters"]
            ids = [rig["id"] for rig in rigs]
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate rig id in manifest")
            candidates.append((path, rigs))
            counts.update(ids)
        except (OSError, UnicodeError, ValueError, OverflowError, RecursionError) as error:
            _LOG.warning("Skipping character pack %s: %s", path.name, error)
    for path, rigs in candidates:
        clashes = [rig["id"] for rig in rigs if rig["id"] in builtin_ids or counts[rig["id"]] > 1]
        if clashes:
            _LOG.warning("Skipping character pack %s: rig id clashes: %s", path.name, ", ".join(clashes))
        else:
            yield path, rigs


def load(dir: Path, *, builtin_ids: Collection[str] = ("lens",)) -> list[dict]:
    """Valid manifest entries, with whole packs skipped on malformed metadata or id clashes."""
    return [rig for _path, rigs in _packs(dir, builtin_ids) for rig in rigs]


def files(dir: Path) -> list[Path]:
    """Every regular file under the packs directory, excluding test folders."""
    paths: list[Path] = []

    def visit(path: Path) -> None:
        try:
            for source in sorted(path.iterdir()):
                if source.is_dir():
                    if source.name != "test":
                        visit(source)
                elif source.is_file():
                    paths.append(source)
        except FileNotFoundError:
            return
        except OSError as error:
            _LOG.warning("Skipping character pack files in %s: %s", path.name, error)

    visit(dir)
    return sorted(paths)
