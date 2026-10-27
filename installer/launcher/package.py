"""Assemble an unsigned per-user installer from a verified portable payload."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib

from installer.launcher.build import LAUNCHER, compile_launcher, find_csc, windows_version


def find_iscc(explicit: Path | None = None) -> Path:
    candidate = str(explicit) if explicit else shutil.which("ISCC.exe")
    if not candidate or not Path(candidate).is_file():
        raise ValueError("Inno Setup compiler ISCC.exe was not found. Supply --iscc or add it to PATH.")
    return Path(candidate).resolve()


def compile_setup(command: list[str], stage: Path) -> None:
    result = subprocess.run(command, cwd=stage, check=False, timeout=1800,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout)
    if re.search(r"\bwarning:", result.stdout, re.IGNORECASE):
        raise ValueError("Inno Setup compiler warnings must be resolved before publication")


def build_installer(source: Path, out: Path, cache: Path, *, bundle: Path | None = None,
                    iscc: Path | None = None, csc: Path | None = None, offline: bool = False,
                    revision: str = "HEAD", keep_payload: bool = False) -> dict:
    from installer.build import build, copy_sources, outside_checkout, source_revision
    from installer.manifest import digest, verify_manifest, write_manifest

    compiler, launcher_compiler = find_iscc(iscc), find_csc(csc)
    source = source.resolve()
    revision = source_revision(source, revision)
    out, cache = outside_checkout(out, source), outside_checkout(cache, source)
    if out == cache or out in cache.parents or cache in out.parents:
        raise ValueError("Output and cache directories must not overlap")
    if out.exists():
        raise ValueError("The installer output directory must not already exist")
    if bundle is not None:
        bundle = outside_checkout(bundle, source)
        if out == bundle or out in bundle.parents or bundle in out.parents:
            raise ValueError("Installer output must not overlap the input bundle")
        if verify_manifest(bundle).get("sourceRevision") != revision:
            raise ValueError("The bundle must use the same committed source revision as the installer")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".installer-build-", dir=out.parent) as temporary:
        work = Path(temporary)
        output, payload, stage = work / "output", work / "payload", work / "source"
        output.mkdir()
        copy_sources(source, stage, revision, selection=(
            "installer/cutgraph.iss", "ui/public/assets/cutgraph.ico", "pyproject.toml"
        ))
        version = tomllib.loads((stage / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        if not isinstance(version, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version):
            raise ValueError("The installer version must be safe for an artifact filename")
        if bundle is None:
            build(source, payload, cache, offline=offline, revision=revision)
        else:
            shutil.copytree(bundle, payload)
        manifest = verify_manifest(payload)
        from installer.sources import SOURCE_ARCHIVE
        source_receipt = manifest.get("ffmpegSource")
        source_root = bundle if bundle is not None else payload
        source_artifact = source_root.with_name(source_root.name + "-" + SOURCE_ARCHIVE)
        if (not isinstance(source_receipt, dict) or source_receipt.get("name") != SOURCE_ARCHIVE
                or not source_artifact.is_file() or source_receipt.get("sha256") != digest(source_artifact)
                or source_receipt.get("size") != source_artifact.stat().st_size):
            raise ValueError("The matching FFmpeg source artifact is required beside the bundle")
        shutil.copyfile(source_artifact, output / SOURCE_ARCHIVE)
        launcher = payload / LAUNCHER
        launcher.unlink(missing_ok=True)
        compile_launcher(source, payload, csc=launcher_compiler, revision=revision)
        origins = {row["path"]: row["origin"] for row in manifest["files"]}
        # Installed sessions always enter through the job-owning native launcher.
        (payload / "run-studio.cmd").unlink(missing_ok=True)
        origins.pop("run-studio.cmd", None)
        origins[LAUNCHER] = "generated:launcher"
        from installer.licensing import refresh_launcher
        refresh_launcher(payload, origins)
        # Authenticode signing remains disabled; sign before final inventory if enabled later.
        manifest = write_manifest(payload, origins, omitted=manifest.get("omittedWheelMetadata", []),
                                  source_revision=revision, build_paths=(source, out, payload, work, cache))
        manifest["ffmpegSource"] = source_receipt
        (payload / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        verify_manifest(payload)
        command = [str(compiler), f"/DBundleDir={payload}", f"/DAppVersion={version}",
                   f"/DWindowsVersion={windows_version(version)}", f"/DOutputDir={output}",
                   str(stage / "installer/cutgraph.iss")]
        compile_setup(command, stage)
        setup = output / f"CutgraphStudio-{version}-setup.exe"
        if not setup.is_file():
            raise ValueError("Inno Setup did not produce the expected setup executable")
        result = {"installer": setup.name, "version": version, "unsigned": True, "sourceRevision": revision,
                  "sha256": digest(setup), "size": setup.stat().st_size,
                  "launcherSha256": digest(launcher), "payloadBytes": manifest["size"],
                  "payloadFiles": len(manifest["files"]), "isccSha256": digest(compiler),
                  "cscSha256": digest(launcher_compiler)}
        result["ffmpegSource"] = source_receipt
        (output / (setup.name + ".sha256")).write_text(f"{result['sha256']}  {setup.name}\n", encoding="utf-8")
        (output / "installer-build.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(payload / "bundle-manifest.json", output / "bundle-manifest.json")
        shutil.copyfile(payload / "SBOM.cdx.json", output / "SBOM.cdx.json")
        shutil.copyfile(payload / "THIRD-PARTY-NOTICES.txt", output / "THIRD-PARTY-NOTICES.txt")
        if keep_payload:
            payload.rename(output / "payload")
        output.rename(out)
    print(json.dumps(result, indent=2))
    return result
