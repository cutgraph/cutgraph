"""Compile the launcher with the .NET Framework compiler supplied by Windows."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import tomllib
import re


LAUNCHER = "Cutgraph Studio.exe"
LAUNCHER_SOURCES = tuple("installer/launcher/" + name for name in (
    "Program.cs", "Native.cs", "SessionObjects.cs", "DataPaths.cs", "Update.cs", "app.manifest"
)) + ("ui/public/assets/cutgraph.ico", "pyproject.toml")


def find_csc(explicit: Path | None = None) -> Path:
    compiler = explicit or Path(os.environ["SYSTEMROOT"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        raise ValueError("Windows .NET Framework C# compiler is missing; provide --csc")
    return compiler.resolve()


def windows_version(version: str) -> str:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\D|$)", version)
    if not match or any(int(part) > 65535 for part in match.groups()):
        raise ValueError("The product version needs three Windows-compatible numeric components")
    return ".".join(match.groups()) + ".0"


def normalize_pe(path: Path) -> None:
    """Remove the old compiler's timestamp and random module ID before signing."""
    data = bytearray(path.read_bytes())
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("The launcher compiler did not produce a PE image")
    sections, = struct.unpack_from("<H", data, pe + 6)
    optional_size, = struct.unpack_from("<H", data, pe + 20)
    optional = pe + 24
    if struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise ValueError("The launcher must be a Windows x64 PE image")

    def offset(rva: int) -> int:
        for index in range(sections):
            section = optional + optional_size + index * 40
            size, address, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        raise ValueError("Invalid launcher metadata address")

    cli = offset(struct.unpack_from("<I", data, optional + 112 + 14 * 8)[0])
    metadata = offset(struct.unpack_from("<I", data, cli + 8)[0])
    version_size, = struct.unpack_from("<I", data, metadata + 12)
    cursor = metadata + 16 + version_size
    streams, = struct.unpack_from("<H", data, cursor + 2)
    cursor += 4
    guid = None
    for _ in range(streams):
        start, size = struct.unpack_from("<II", data, cursor)
        end = data.index(0, cursor + 8)
        name = data[cursor + 8:end].decode("ascii")
        cursor = (end + 4) & ~3
        if name == "#GUID":
            if size != 16:
                raise ValueError("Unexpected launcher module ID heap")
            guid = metadata + start
    if guid is None:
        raise ValueError("Launcher module ID is missing")
    data[pe + 8:pe + 12] = bytes(4)
    data[optional + 64:optional + 68] = bytes(4)
    data[guid:guid + 16] = bytes(16)
    data[guid:guid + 16] = hashlib.sha256(data).digest()[:16]
    path.write_bytes(data)


def compile_launcher(source: Path, destination: Path, *, csc: Path | None = None,
                     revision: str = "HEAD") -> Path:
    from installer.build import copy_sources, outside_checkout, source_revision

    if sys.platform != "win32":
        raise ValueError("The launcher must be compiled on Windows")
    destination = outside_checkout(destination, source.resolve())
    compiler = find_csc(csc)
    revision = source_revision(source, revision)
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / LAUNCHER
    if output.exists():
        raise ValueError("The launcher output already exists")
    with tempfile.TemporaryDirectory(prefix=".launcher-source-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        copy_sources(source, stage, revision, selection=LAUNCHER_SOURCES)
        version = tomllib.loads((stage / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        numeric = windows_version(version)
        launcher = stage / "installer/launcher"
        manifest = launcher / "app.manifest"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("@VERSION@", numeric), encoding="utf-8")
        assembly = launcher / "AssemblyInfo.cs"
        assembly.write_text('\n'.join((
            '[assembly: System.Reflection.AssemblyVersion("' + numeric + '")]',
            '[assembly: System.Reflection.AssemblyFileVersion("' + numeric + '")]',
            '[assembly: System.Reflection.AssemblyInformationalVersion("' + version + '")]',
        )), encoding="utf-8")
        command = [str(compiler), "/nologo", "/warnaserror+", "/target:winexe", "/platform:x64", "/optimize+", "/debug-",
                   "/utf8output", f"/out:{output}", f"/win32manifest:{manifest}",
                   f"/win32icon:{stage / 'ui/public/assets/cutgraph.ico'}",
                   "/reference:System.dll", "/reference:System.Core.dll", "/reference:System.Drawing.dll",
                   "/reference:System.Windows.Forms.dll", "/reference:System.Web.Extensions.dll",
                   *map(str, sorted(launcher.glob("*.cs")))]
        subprocess.run(command, check=True, timeout=120, cwd=stage)
    normalize_pe(output)
    return output
