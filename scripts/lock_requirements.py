"""Regenerate universal, hash-locked dependency sets with uv on PATH."""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit, parse_qs
from urllib.request import urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.dependency_locks import PYPI_INDEX, TORCH_PACKAGES, package_index, requirements


ROOT = Path(__file__).resolve().parents[1]
ADDON_LOCKS = {"requirements-local-ai-cpu": "https://download.pytorch.org/whl/cpu",
               "requirements-local-ai-nvidia": "https://download.pytorch.org/whl/cu128",
               "requirements-agent": None}


def compile_command(uv: str, source: Path) -> list[str]:
    command = [
        uv, "pip", "compile", f"{source.stem}/pyproject.toml" if ADDON_LOCKS.get(source.stem) else source.name,
        "--universal", "--python-version", "3.12", "--generate-hashes",
        "--no-header", "--annotation-style", "line",
        "--default-index", PYPI_INDEX,
        "--output-file", source.with_suffix(".txt").name,
    ]
    if source.stem in {"requirements-local-ai-nvidia", "requirements-local-ai-cpu"}:
        command.extend(["--constraint", "requirements-core.txt"])
    if ADDON_LOCKS.get(source.stem):
        command.extend(["--constraint", "addon-shared-constraints.txt"])
    return command


def compile_project(source: Path) -> str:
    """Read .in ownership into a temporary project with exactly three explicit index sources."""
    def pins(path: Path) -> list[str]:
        result = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.startswith("-r "):
                result.extend(pins(path.parent / line[3:]))
            elif line and not line.startswith("-c "):
                result.append(line)
        return result

    dependencies = ",\n".join(json.dumps(pin) for pin in pins(source))
    sources = "\n".join(f'{name} = {{index = "pytorch"}}' for name in sorted(TORCH_PACKAGES))
    return (f'[project]\nname = "optional-dependency-lock"\nversion = "0"\nrequires-python = ">=3.12"\n'
            f'dependencies = [\n{dependencies}\n]\n[tool.uv.sources]\n{sources}\n'
            f'[[tool.uv.index]]\nname = "pytorch"\nurl = "{ADDON_LOCKS[source.stem]}"\nexplicit = true\n')


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.wheels: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        url = dict(attrs).get("href", "")
        if tag == "a" and url:
            parts = urlsplit(url)
            digest = parse_qs(parts.fragment).get("sha256", [""])[0]
            self.wheels.append((unquote(parts.path.rsplit("/", 1)[-1]), digest))


def check_wheels() -> int:
    """Check Windows CPython wheels and hashes using metadata only, never wheel downloads."""
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.tags import compatible_tags, cpython_tags
    from packaging.utils import parse_wheel_filename

    environment = {**default_environment(), "sys_platform": "win32", "os_name": "nt",
                   "platform_system": "Windows", "platform_machine": "AMD64", "platform_release": "10",
                   "platform_version": "10.0", "python_version": "3.12", "python_full_version": "3.12.10",
                   "implementation_name": "cpython", "implementation_version": "3.12.10"}
    tags = set(cpython_tags((3, 12), platforms=["win_amd64"]))
    tags.update(compatible_tags((3, 12), interpreter="cp312", platforms=["win_amd64"]))

    def check(item):
        name, version, entry, index = item
        hashes = set(re.findall(r"--hash=sha256:([a-f0-9]{64})", entry))
        try:
            if index == PYPI_INDEX:
                with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as response:
                    wheels = [(file["filename"], file["digests"]["sha256"])
                              for file in json.load(response)["urls"] if not file.get("yanked")]
            else:
                with urlopen(f"{index}/{name}/", timeout=60) as response:
                    parser = _Links()
                    parser.feed(response.read().decode())
                    wheels = parser.wheels
            for filename, digest in wheels:
                if not filename.endswith(".whl"):
                    continue
                _, wheel_version, _, wheel_tags = parse_wheel_filename(filename)
                if str(wheel_version) == version and wheel_tags & tags and digest in hashes:
                    return True, f"PASS {name}=={version} [{index}]: {filename} sha256:{digest}"
            return False, f"FAIL {name}=={version} [{index}]: no hash-matching Windows CPython 3.12 wheel"
        except Exception as error:
            return False, f"FAIL {name}=={version} [{index}]: {error}"

    failed = total = 0
    for stem, torch_index in ADDON_LOCKS.items():
        entries = []
        for name, version, entry in requirements((ROOT / f"{stem}.txt").read_text(encoding="utf-8")):
            requirement = Requirement(entry.partition("--hash=")[0].strip())
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            entries.append((name, version, entry, package_index(name, version, torch_index)))
        print(f"Checking {stem}.txt: {len(entries)} Windows entries", flush=True)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(check, entries))
        for passed, message in results:
            print(message, flush=True)
            failed += not passed
        total += len(results)
        print(f"{stem}.txt: {sum(passed for passed, _ in results)} passed, {sum(not passed for passed, _ in results)} failed", flush=True)
    print(f"Wheel metadata check: {total - failed} passed, {failed} failed; no wheels downloaded", flush=True)
    return int(failed > 0)


def pins_only(lock: str) -> str:
    """A lock with its hash lines removed: the pinned versions and markers, as plain requirement lines."""
    lines = []
    for line in lock.splitlines():
        line = re.sub(r"\s*--hash=\S+", "", line).rstrip().rstrip("\\").rstrip()
        if line:
            lines.append(line)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="diff regenerated locks without changing files")
    parser.add_argument("--check-wheels", action="store_true", help="verify Windows wheels and locked hashes on all add-on indexes (metadata only)")
    args = parser.parse_args(argv)
    if args.check_wheels:
        return check_wheels()
    uv = shutil.which("uv")
    if uv is None:
        print("error: uv must be installed and available on PATH to compile dependency locks", file=sys.stderr)
        return 1

    sources = sorted(ROOT.glob("requirements-*.in"))
    if not sources:
        print("error: no requirements inputs found", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="dependency-locks-") as directory:
        temporary = Path(directory)
        shared_constraints = temporary / "addon-shared-constraints.txt"
        shared_constraints.write_text("", encoding="utf-8")
        priorities = {"requirements-core": 0, "requirements-agent": 1,
                      "requirements-local-ai-nvidia": 2, "requirements-local-ai-cpu": 3}
        for source in sorted(sources, key=lambda path: (priorities.get(path.stem, 4), path.name)):
            shutil.copyfile(source, temporary / source.name)
            if ADDON_LOCKS.get(source.stem):
                project = temporary / source.stem / "pyproject.toml"
                project.parent.mkdir()
                project.write_text(compile_project(source), encoding="utf-8")
            lock = source.with_suffix(".txt")
            if lock.exists():
                # Seed with the existing versions so checking does not upgrade them, but never with their
                # hashes: uv keeps hashes it finds in the output file, which would hide an edited hash.
                (temporary / lock.name).write_text(pins_only(lock.read_text(encoding="utf-8")), encoding="utf-8")
        for source in sorted(sources, key=lambda path: (priorities.get(path.stem, 4), path.name)):
            command = compile_command(uv, source)
            print("+ uv " + " ".join(command[1:]), flush=True)
            result = subprocess.run(command, cwd=temporary, check=False, stdout=subprocess.DEVNULL)
            if result.returncode:
                return result.returncode
            if source.stem in ADDON_LOCKS:
                generated = temporary / source.with_suffix(".txt").name
                shared = [entry.partition("--hash=")[0].strip()
                          for name, _, entry in requirements(generated.read_text(encoding="utf-8"))
                          if name not in TORCH_PACKAGES]
                with shared_constraints.open("a", encoding="utf-8") as stream:
                    stream.write("\n".join(shared) + "\n")

        changed = False
        for source in sources:
            lock = source.with_suffix(".txt")
            generated = temporary / lock.name
            if args.check:
                before = lock.read_text(encoding="utf-8") if lock.exists() else ""
                after = generated.read_text(encoding="utf-8")
                if before != after:
                    changed = True
                    sys.stdout.writelines(difflib.unified_diff(
                        before.splitlines(keepends=True), after.splitlines(keepends=True),
                        fromfile=lock.name, tofile=f"{lock.name} (regenerated)",
                    ))
            else:
                shutil.copyfile(generated, lock)
        return int(changed)


if __name__ == "__main__":
    raise SystemExit(main())
