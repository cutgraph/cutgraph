"""Build a Windows x64 source runtime without modifying the source checkout."""
from __future__ import annotations

import argparse
import csv
import fnmatch
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit
import zipfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SOURCE = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    "installer/__init__.py", "installer/release_manifest.py",
    "pipeline/**", "studio/**", "scripts/**", "schema/**", "assets/fonts/**",
    "motion-core/src/**", "motion-core/bin/**", "motion-core/scripts/**",
    "motion-core/package.json", "motion-core/package-lock.json", "ui/**",
    "LICENSE", "NOTICE", "LICENSES/**", "config.py", "runtime.py", "channels.example.json",
    "requirements-core.txt", "requirements-core.in", ".python-version", ".nvmrc",
    "requirements-local-ai-cpu.txt", "requirements-local-ai-nvidia.txt", "requirements-agent.txt",
)


def is_source_path(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in SOURCE_PATHS)


def git_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def git(source: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=source, env=git_environment(),
                          capture_output=True, check=True).stdout


def source_revision(source: Path, revision: str = "HEAD") -> str:
    try:
        return git(source, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()
    except subprocess.CalledProcessError as error:
        raise ValueError("A committed HEAD is required to build the bundle") from error


def outside_checkout(path: Path, source: Path) -> Path:
    path = path.resolve()
    if path == source or path in source.parents or source in path.parents:
        raise ValueError("Build outputs and caches must be outside the source checkout")
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("Build outputs and caches must be outside every checkout")
    return path


def download(item: dict, cache: Path, *, offline: bool = False) -> Path:
    from installer.manifest import digest

    filename = PurePosixPath(urlsplit(item["url"]).path).name
    path = cache / f'{item["sha256"]}-{filename}'
    if path.exists():
        if digest(path) != item["sha256"]:
            raise ValueError(f'Hash mismatch in cached input: {item["name"]}')
        return path
    if offline:
        raise ValueError(f'Missing cached input in offline mode: {item["name"]}')
    partial = path.with_suffix(path.suffix + ".partial")
    try:
        request = urllib.request.Request(item["url"], headers={"User-Agent": "portable-runtime-build"})
        with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        if digest(partial) != item["sha256"]:
            raise ValueError(f'Hash mismatch in downloaded input: {item["name"]}')
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def extract_zip(archive: Path, destination: Path) -> None:
    """Extract regular files only; refuse Windows aliases and archive traversal."""
    with zipfile.ZipFile(archive) as bundle:
        seen: set[str] = set()
        for info in bundle.infolist():
            name = info.orig_filename.rstrip("/")
            path = PurePosixPath(name)
            if (not name or "\0" in name or path.is_absolute() or "\\" in name or path.as_posix() != name
                    or any(part in {".", ".."} or ":" in part or part.endswith((" ", "."))
                           or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(
                               f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
                           )} for part in path.parts)
                    or stat.S_ISLNK(info.external_attr >> 16) or name.casefold() in seen):
                raise ValueError(f"Unsafe archive member: {name}")
            seen.add(name.casefold())
            target = destination / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)


def tracked_sources(source: Path, revision: str | None = None, *, selection: tuple[str, ...] | None = None) -> list[str]:
    from installer.manifest import check_path

    revision = revision or source_revision(source)
    records = git(source, "ls-tree", "-r", "-z", revision)
    paths = []
    for record in records.decode("utf-8").split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        if not (path in selection if selection is not None else is_source_path(path)):
            continue
        if metadata.split()[0] not in {"100644", "100755"}:
            raise ValueError(f"Source links and submodules are forbidden: {path}")
        if selection is None:
            check_path("app/" + path)
        paths.append(path)
    if selection is not None and set(selection) != set(paths):
        raise ValueError("Required committed build sources are missing")
    return sorted(paths)


def copy_sources(source: Path, destination: Path, revision: str | None = None, *,
                 selection: tuple[str, ...] | None = None) -> list[str]:
    revision = revision or source_revision(source)
    paths = tracked_sources(source, revision, selection=selection)
    # Read raw blobs so checkout filters and archive attributes cannot transform content.
    records = git(source, "ls-tree", "-r", "-z", revision)
    selected = set(paths)
    blobs = {path: metadata.split()[2] for record in records.decode("utf-8").split("\0") if record
             for metadata, path in [record.split("\t", 1)] if path in selected}
    result = subprocess.run(["git", "cat-file", "--batch"], cwd=source, env=git_environment(),
                            input=("\n".join(blobs[path] for path in paths) + "\n").encode(),
                            capture_output=True, check=True)
    stream = io.BytesIO(result.stdout)
    for relative in paths:
        header = stream.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError("Could not read committed source blob")
        content = stream.read(int(header[2]))
        if len(content) != int(header[2]) or stream.read(1) != b"\n":
            raise ValueError("Incomplete committed source blob")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return paths


def build_environment(work: Path, node: Path) -> dict[str, str]:
    keys = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "OS", "NUMBER_OF_PROCESSORS"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keys}
    system = Path(env["SYSTEMROOT"])
    env["PATH"] = os.pathsep.join(map(str, (node, system / "System32", system)))
    for key in ("TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        directory = work / key.lower()
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    state = work / "state"
    state.mkdir()
    env.update(CUTGRAPH_STATE_HOME=str(state), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", npm_config_update_notifier="false")
    return env


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    print(subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=1800)


def register_tree(root: Path, subtree: Path, origins: dict[str, str], origin: str) -> None:
    for path in subtree.rglob("*"):
        if path.is_file():
            origins[path.relative_to(root).as_posix()] = origin


def omit_nonruntime_metadata(target: Path) -> list[dict]:
    """Omit the upstream wheel's development documentation, recording its hashes."""
    from installer.manifest import digest

    omitted = []
    for directory in (target / "fastapi" / ".agents", target / "bin"):
        if not directory.exists():
            continue
        if target.resolve() not in directory.resolve().parents or directory.is_symlink() or directory.is_junction():
            raise ValueError("Nonruntime metadata must be inside the dependency target")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink() or path.is_junction():
                raise ValueError("Nonruntime metadata must not contain links")
            if path.is_file():
                row = {"path": path.relative_to(target).as_posix(), "sha256": digest(path)}
                # Generated launchers contain an absolute interpreter path. Their hashes are not portable.
                if directory.name == "bin":
                    row = {"path": row["path"], "reason": "nonportable console launcher"}
                omitted.append(row)
        shutil.rmtree(directory)
    removed = {row["path"] for row in omitted}
    for record in sorted(target.glob("*.dist-info/RECORD")):
        with record.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))
        retained = [row for row in rows if row[0] not in removed
                    and not (row[0].startswith(("../bin/", "../../bin/"))
                             and "bin/" + PurePosixPath(row[0]).name in removed)]
        if retained != rows:
            with record.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream, lineterminator="\n").writerows(retained)
    return omitted


def validate_pins(inputs: dict, stage: Path) -> dict[str, dict]:
    pins = {item["name"]: item for item in inputs["inputs"]}
    for name, version_file in (("python", ".python-version"), ("node", ".nvmrc")):
        if pins[name]["version"] != (stage / version_file).read_text().strip().lstrip("v"):
            raise ValueError(f"Pinned {name} does not match {version_file}")
    for item in pins.values():
        if (not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or not item["url"].startswith("https://") or not item["license"]):
            raise ValueError("Every input needs HTTPS, SHA-256 and a license identifier")
    return pins


def verify_node_checksums(pins: dict[str, dict], archives: dict[str, Path]) -> None:
    filename = f'node-v{pins["node"]["version"]}-win-x64.zip'
    line = pins["node"]["sha256"] + "  " + filename
    for document in ("node-SHASUMS256.txt", "node-SHASUMS256.txt.asc"):
        if line not in archives[document].read_text().splitlines():
            raise ValueError(f"Node SHA-256 disagrees with the pinned upstream {document}")


def build(source: Path, out: Path, cache: Path, *, offline: bool = False, revision: str = "HEAD") -> dict:
    """Publish a complete sibling staging directory only after every check succeeds."""
    source = source.resolve()
    out = outside_checkout(out, source)
    cache = outside_checkout(cache, source)
    if out == cache or out in cache.parents or cache in out.parents:
        raise ValueError("Output and cache directories must not overlap")
    if out.exists():
        raise ValueError("The output directory must not already exist")
    revision = source_revision(source, revision)
    if git(source, "status", "--porcelain", "--untracked-files=no"):
        print(f"Warning: uncommitted changes are ignored; building committed revision {revision}.", file=sys.stderr)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".portable-output-", dir=out.parent) as temporary:
        payload = Path(temporary) / "bundle"
        result = assemble(source, payload, cache, revision, offline=offline, published_out=out)
        from installer.sources import load_inputs, produce
        source_pins = Path(temporary) / "sources.json"
        source_pins.write_bytes(git(source, "show", f"{revision}:installer/sources.json"))
        evidence = produce(load_inputs(source_pins), cache, Path(temporary) / "sources", offline=offline)
        result["ffmpegSource"] = evidence["artifact"]
        (payload / "bundle-manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        source_artifact = out.with_name(out.name + "-" + evidence["artifact"]["name"])
        if source_artifact.exists():
            raise ValueError("The source artifact must not already exist")
        shutil.move(Path(temporary) / "sources" / evidence["artifact"]["name"], source_artifact)
        payload.rename(out)
    print(json.dumps({"bundle": str(out), "files": len(result["files"]), "bytes": result["size"]}))
    return result


def assemble(source: Path, out: Path, cache: Path, revision: str, *, offline: bool,
             published_out: Path) -> dict:
    from installer.manifest import write_manifest

    if sys.platform != "win32":
        raise ValueError("The portable bundle must be built on Windows x64")
    out.mkdir(parents=True, exist_ok=True)
    origins: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix=".portable-build-", dir=out.parent) as temporary:
        work = Path(temporary)
        stage = work / "source"
        paths = copy_sources(source, stage, revision)
        inputs = json.loads(git(source, "show", f"{revision}:installer/inputs.json"))
        pins = validate_pins(inputs, stage)
        cache.mkdir(parents=True, exist_ok=True)
        archives = {name: download(item, cache, offline=offline) for name, item in pins.items()}
        verify_node_checksums(pins, archives)
        extract_zip(archives["python"], out / "python")
        register_tree(out, out / "python", origins, "pinned input:python")
        pip_name = f'pip-{pins["pip"]["version"]}-py3-none-any.whl'
        (out / "addons").mkdir()
        shutil.copyfile(archives["pip"], out / "addons" / pip_name)
        origins[f"addons/{pip_name}"] = "pinned input:pip"
        pth = next((out / "python").glob("python*._pth"))
        pth.write_text("python312.zip\n.\n../app\nLib/site-packages\n", encoding="utf-8")
        origins[pth.relative_to(out).as_posix()] = "generated:embedded import paths"
        extract_zip(archives["node"], work / "node")
        node_home = next((work / "node").iterdir())
        (out / "node").mkdir()
        for filename in ("node.exe", "LICENSE"):
            shutil.copyfile(node_home / filename, out / "node" / filename)
            origins[f"node/{filename}"] = "pinned input:node"
        extract_zip(archives["ffmpeg"], work / "ffmpeg")
        ffmpeg_home = next((work / "ffmpeg").iterdir())
        ffmpeg_out = out / "ffmpeg"
        ffmpeg_out.mkdir()
        for path in (ffmpeg_home / "bin").iterdir():
            if path.suffix.lower() == ".dll" or path.name.lower() in {"ffmpeg.exe", "ffprobe.exe"}:
                shutil.copyfile(path, ffmpeg_out / path.name)
        for path in ffmpeg_home.iterdir():
            if path.is_file() and any(word in path.name.lower() for word in ("license", "copying", "readme")):
                shutil.copyfile(path, ffmpeg_out / path.name)
        for folder in ("licenses",):
            if (ffmpeg_home / folder).is_dir():
                shutil.copytree(ffmpeg_home / folder, ffmpeg_out / folder)
        register_tree(out, ffmpeg_out, origins, "pinned input:ffmpeg")
        env = build_environment(work, node_home)
        # Embedded Python ignores PYTHONPATH: explicitly add pip only for its build commands.
        pip = [str(out / "python/python.exe"), "-B", "-c",
               "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('pip',run_name='__main__')",
               str(out / "addons" / pip_name)]
        python = str(out / "python/python.exe")
        wheelhouse = cache / "wheels"
        wheelhouse.mkdir(exist_ok=True)
        if not offline:
            run([*pip, "download", "--require-hashes", "--only-binary=:all:",
                 "--dest", str(wheelhouse), "-r", str(stage / "requirements-core.txt")], stage, env)
        target = out / "python/Lib/site-packages"
        run([*pip, "install", "--require-hashes", "--only-binary=:all:", "--no-compile",
             "--no-index", "--find-links", str(wheelhouse), "--target", str(target),
             "-r", str(stage / "requirements-core.txt")], stage, env)
        omitted = omit_nonruntime_metadata(target)
        register_tree(out, target, origins, "pinned input:requirements-core.txt")
        npm = [str(node_home / "node.exe"), str(node_home / "node_modules/npm/bin/npm-cli.js")]
        npm_flags = ["--ignore-scripts", "--no-audit", "--no-fund", "--cache", str(cache / "npm")]
        if offline:
            npm_flags.append("--offline")
        run([*npm, "ci", *npm_flags], stage / "ui", env)
        run([*npm, "run", "build"], stage / "ui", env)
        run([*npm, "ci", "--omit=dev", *npm_flags], stage / "motion-core", env)
        canvas = stage / "motion-core/node_modules/@napi-rs"
        native = canvas / "canvas-win32-x64-msvc"
        if not native.is_dir() or any(p.name not in {"canvas", "canvas-win32-x64-msvc"} for p in canvas.iterdir()):
            raise ValueError("Expected only the Windows x64 canvas prebuild")
        app = out / "app"
        for relative in paths:
            if relative.startswith("ui/"):
                continue
            destination = app / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage / relative, destination)
            origins["app/" + relative] = "exported path:" + relative
        shutil.copytree(stage / "ui/dist", app / "ui/dist")
        register_tree(out, app / "ui/dist", origins, "generated:ui build from tracked source and ui/package-lock.json")
        shutil.copytree(stage / "motion-core/node_modules", app / "motion-core/node_modules")
        register_tree(out, app / "motion-core/node_modules", origins, "pinned input:motion-core/package-lock.json")
        for relative in paths:
            if relative in {"LICENSE", "NOTICE"}:
                destination = out / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(stage / relative, destination)
                origins[relative] = "exported path:" + relative
        (out / "run-studio.cmd").write_bytes(
            b'@echo off\r\n"%~dp0python\\pythonw.exe" -B "%~dp0app\\scripts\\studio_app.py" %*\r\n'
        )
        origins["run-studio.cmd"] = "generated:bundle runner"
        for directory in (target, app):
            run([python, "-B", "-m", "compileall", "-q", "--invalidation-mode", "unchecked-hash",
                 "-s", str(out), str(directory)], stage, env)
            for path in directory.rglob("*.pyc"):
                origins[path.relative_to(out).as_posix()] = "generated:unchecked-hash bytecode"
        from installer.licensing import generate
        license_inputs = json.loads(git(source, "show", f"{revision}:installer/sources.json"))
        if license_inputs["binarySha256"] != pins["ffmpeg"]["sha256"]:
            raise ValueError("FFmpeg source pins do not match the bundled binary")
        from installer.sources import verify_binary
        verify_binary(out / "ffmpeg/ffmpeg.exe", license_inputs, env)
        generate(out, origins, pins, license_inputs, cache, offline=offline, ui=stage / "ui")
        from scripts.sbom import validate_bundle_document
        schemas = {name: json.loads(git(source, "show", f"{revision}:tests/fixtures/cyclonedx/{name}"))
                   for name in ("bom-1.5.schema.json", "spdx.schema.json", "jsf-0.82.schema.json")}
        validate_bundle_document(json.loads((out / "SBOM.cdx.json").read_text(encoding="utf-8")), schemas)
        # Authenticode signing is disabled. A release signing stage can extend this seam.
        result = write_manifest(out, origins, omitted=omitted, source_revision=revision,
                                build_paths=(out, published_out, work, cache, source))
    return result


def main() -> int:
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "cutgraph-build-cache")
    parser.add_argument("--offline", action="store_true", help="Use only verified cached inputs and packages")
    parser.add_argument("--revision", default="HEAD", help="Committed source revision (default: HEAD)")
    parser.add_argument("--installer", action="store_true", help="Build an unsigned per-user installer")
    parser.add_argument("--sources-only", action="store_true", help="Build the FFmpeg corresponding-source release artifact")
    parser.add_argument("--bundle", type=Path, help="Existing verified bundle, only with --installer")
    parser.add_argument("--keep-payload", action="store_true", help="Retain the installed payload for smoke checks")
    parser.add_argument("--iscc", type=Path, help="Inno Setup compiler executable")
    parser.add_argument("--csc", type=Path, help="Windows .NET Framework C# compiler executable")
    args = parser.parse_args()
    try:
        if args.sources_only:
            if args.installer or args.bundle or args.iscc or args.csc or args.keep_payload:
                raise ValueError("--sources-only cannot be combined with installer options")
            from installer.sources import produce
            inputs = json.loads(git(SOURCE, "show", f"{source_revision(SOURCE, args.revision)}:installer/sources.json"))
            print(json.dumps(produce(inputs, args.cache, args.out, offline=args.offline), indent=2))
        elif args.installer:
            from installer.launcher.package import build_installer

            build_installer(SOURCE, args.out, args.cache, bundle=args.bundle, iscc=args.iscc,
                            csc=args.csc, offline=args.offline, revision=args.revision, keep_payload=args.keep_payload)
        else:
            if args.bundle or args.iscc or args.csc or args.keep_payload:
                raise ValueError("--bundle, --iscc and --csc require --installer")
            build(SOURCE, args.out, args.cache, offline=args.offline, revision=args.revision)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Bundle build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
