"""Launcher build reproducibility, installer containment and smoke authorization."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import shutil
import subprocess
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

import pytest

from installer import build, licensing, lifecycle_smoke, manifest, sources
from scripts import sbom
from installer.launcher import package, smoke as launcher_smoke
from installer.launcher.build import LAUNCHER, LAUNCHER_SOURCES, compile_launcher, find_csc
from pipeline import version


@pytest.fixture
def committed_sources(monkeypatch):
    """Supply fixed source blobs without creating a repository in the test output."""
    def copy(_source, destination, _revision=None, *, selection=None):
        assert selection is not None
        for name in selection:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(build.SOURCE / name, target)
        return list(selection)

    monkeypatch.setattr(build, "source_revision", lambda *_args: "fixed-revision")
    monkeypatch.setattr(build, "copy_sources", copy)


@pytest.fixture
def launcher(tmp_path, committed_sources):
    if sys.platform != "win32":
        pytest.skip("Exercises Windows kernel objects and executable lifecycle")
    return compile_launcher(build.SOURCE, tmp_path / "launcher")


def junction(link: Path, target: Path) -> None:
    script = "New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + \
        str(target).replace("'", "''") + "' -ErrorAction Stop | Out-Null"
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                    base64.b64encode(script.encode("utf-16-le")).decode("ascii")], check=True, capture_output=True)


@pytest.mark.parametrize("failure", [None, "hash", "size", "outside", "downgrade", "same", "junction"])
def test_native_update_verification_never_runs_setup(launcher, tmp_path, failure):
    state = tmp_path / "state"
    folder = state / "updates"
    folder.mkdir(parents=True)
    data = b"This is deliberately not an executable."
    sha = hashlib.sha256(data).hexdigest()
    path = folder / ("installer-" + sha + ".exe")
    path.write_bytes(data)
    request = {"id": "a" * 32, "path": str(path), "sha256": sha, "size": len(data), "version": "0.3.0"}
    if failure == "hash":
        path.write_bytes(b"x" * len(data))
    elif failure == "size":
        request["size"] += 1
    elif failure == "outside":
        other = tmp_path / path.name
        other.write_bytes(data)
        request["path"] = str(other)
    elif failure in {"downgrade", "same"}:
        request["version"] = "0.0.0" if failure == "downgrade" else version.__version__
    elif failure == "junction":
        real = state / "real"
        folder.rename(real)
        junction(folder, real)
    (folder / "pending.json").write_text(json.dumps(request))
    try:
        process = subprocess.run([str(launcher), "--verify-update"],
                                 env={**os.environ, "CUTGRAPH_STATE_HOME": str(state)}, timeout=20)
        assert process.returncode == (0 if failure is None else 1)
    finally:
        if failure == "junction":
            os.rmdir(folder)


def licensed_manifest(bundle: Path, origins: dict[str, str], revision: str) -> None:
    text = bundle / "licenses/product/LICENSE"
    text.parent.mkdir(parents=True)
    text.write_text("synthetic licence")
    entry = {"name": "Cutgraph Studio", "version": "1", "ecosystem": "generic", "license": "FSL-1.1-ALv2",
             "notices": ["licenses/product/LICENSE"],
             "files": [{"path": name, "sha256": manifest.digest(bundle / name)} for name in origins]}
    for name, data in {licensing.INVENTORY: {"components": [entry]},
                       licensing.SBOM_FILE: sbom.bundle_document([entry])}.items():
        (bundle / name).write_text(json.dumps(data))
    (bundle / licensing.NOTICE_FILE).write_text("synthetic notices")
    for name in ("licenses/product/LICENSE", licensing.INVENTORY, licensing.SBOM_FILE, licensing.NOTICE_FILE):
        origins[name] = "generated:licensing"
    receipt = manifest.write_manifest(bundle, origins, source_revision=revision)
    source = bundle.with_name(bundle.name + "-" + sources.SOURCE_ARCHIVE)
    source.write_bytes(b"synthetic source archive")
    receipt["ffmpegSource"] = {"name": sources.SOURCE_ARCHIVE, "sha256": manifest.digest(source),
                               "size": source.stat().st_size}
    (bundle / manifest.MANIFEST).write_text(json.dumps(receipt))


def test_installer_requires_iscc_before_creating_output(tmp_path, monkeypatch):
    monkeypatch.setattr(package.shutil, "which", lambda _name: None)
    out = tmp_path / "output"
    with pytest.raises(ValueError, match="ISCC.exe was not found"):
        package.build_installer(build.SOURCE, out, tmp_path / "cache")
    assert not out.exists()


@pytest.mark.parametrize("keep_payload", [False, True])
def test_installer_inventories_launcher_and_preserves_unsigned_ffmpeg_payload(
        tmp_path, monkeypatch, committed_sources, keep_payload):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    (bundle / "app/runtime.py").write_bytes(b"runtime")
    (bundle / "run-studio.cmd").write_bytes(b"portable entry")
    ffmpeg_files = {f"ffmpeg/{name}": f"synthetic unsigned {name}".encode()
                    for name in ("ffmpeg.exe", "ffprobe.exe", "avcodec.dll")}
    origins = {"app/runtime.py": "exported path:runtime.py", "run-studio.cmd": "generated:bundle runner"}
    (bundle / "ffmpeg").mkdir()
    for name, data in ffmpeg_files.items():
        (bundle / name).write_bytes(data)
        origins[name] = "pinned input:ffmpeg"
    licensed_manifest(bundle, origins, "fixed-revision")
    original = {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"synthetic compiler")
    out = tmp_path / "output"
    observed = []
    notices = []

    def compile_stub(_source, destination, **_kwargs):
        (destination / LAUNCHER).write_bytes(b"synthetic launcher")

    def run_stub(command, **_kwargs):
        assert command[0] == str(compiler)
        payload = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("/DBundleDir=")))
        receipt = manifest.verify_manifest(payload)
        assert {path.relative_to(payload).as_posix(): path.read_bytes()
                for path in (payload / "ffmpeg").iterdir()} == ffmpeg_files
        assert receipt["sourceRevision"] == "fixed-revision"
        assert not (payload / "run-studio.cmd").exists()
        assert Path(command[-1]).parent != source / "installer"
        assert any(row["path"] == LAUNCHER and row["sha256"] == hashlib.sha256(b"synthetic launcher").hexdigest()
                   for row in receipt["files"])
        document = json.loads((payload / "SBOM.cdx.json").read_text())
        assert any(component["name"] == "Cutgraph Studio launcher" for component in document["components"])
        inventory = json.loads((payload / licensing.INVENTORY).read_text())["components"]
        assert not any(row["path"] == "run-studio.cmd" for component in inventory for row in component["files"])
        observed.append(payload)
        notices.append((payload / "THIRD-PARTY-NOTICES.txt").read_bytes())
        output = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("/DOutputDir=")))
        version = next(arg.split("=", 1)[1] for arg in command if arg.startswith("/DAppVersion="))
        (output / f"CutgraphStudio-{version}-setup.exe").write_bytes(b"synthetic setup")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(package, "compile_launcher", compile_stub)
    monkeypatch.setattr(package.subprocess, "run", run_stub)
    result = package.build_installer(source, out, tmp_path / "cache", bundle=bundle, iscc=compiler, csc=compiler,
                                     keep_payload=keep_payload)
    assert result["unsigned"] is True
    assert result["sha256"] == hashlib.sha256(b"synthetic setup").hexdigest()
    assert (out / (result["installer"] + ".sha256")).read_text().split()[0] == result["sha256"]
    assert result["sourceRevision"] == "fixed-revision"
    assert result["cscSha256"] == manifest.digest(compiler)
    assert json.loads((out / "installer-build.json").read_text()) == result
    assert not observed[0].exists()
    assert (out / "THIRD-PARTY-NOTICES.txt").read_bytes() == notices[0]
    assert (out / "payload").exists() == keep_payload
    if keep_payload:
        assert manifest.verify_manifest(out / "payload")["sourceRevision"] == "fixed-revision"
        assert {path.relative_to(out / "payload").as_posix(): path.read_bytes()
                for path in (out / "payload/ffmpeg").iterdir()} == ffmpeg_files
    assert {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob("*") if path.is_file()} == original


def test_installer_refuses_input_output_overlap(tmp_path, committed_sources):
    compiler = tmp_path / "ISCC.exe"
    compiler.touch()
    with pytest.raises(ValueError, match="overlap"):
        package.build_installer(build.SOURCE, tmp_path / "bundle/out", tmp_path / "cache",
                                bundle=tmp_path / "bundle", iscc=compiler, csc=compiler)


def test_lifecycle_refuses_to_install_without_disposable_authorization(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(lifecycle_smoke, "no_existing_install", lambda: pytest.fail("Inspected installed programs"))
    with pytest.raises(ValueError, match="authorization"):
        lifecycle_smoke.smoke(tmp_path / "setup.exe", tmp_path)


@pytest.mark.parametrize("ci,explicit,expected", [("", False, False), ("false", False, False),
                                                ("true", False, False), ("1", False, False), ("", True, True)])
def test_lifecycle_authorization(ci, explicit, expected, monkeypatch):
    monkeypatch.setenv("CI", ci)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert lifecycle_smoke.authorized(explicit) is expected


@pytest.mark.skipif(sys.platform != "win32", reason="Uses the C# compiler supplied with Windows")
def test_framework_launcher_build_is_reproducible(tmp_path, committed_sources):
    first = compile_launcher(build.SOURCE, tmp_path / "first")
    second = compile_launcher(build.SOURCE, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().startswith(b"MZ")
    assert os.stat(first).st_size > 10000


def test_installer_rejects_mixed_source_revisions(tmp_path, committed_sources):
    compiler = tmp_path / "ISCC.exe"
    compiler.touch()
    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    (bundle / "app/runtime.py").write_bytes(b"runtime")
    manifest.write_manifest(bundle, {"app/runtime.py": "exported path:runtime.py"}, source_revision="different")
    with pytest.raises(ValueError, match="same committed source revision"):
        package.build_installer(build.SOURCE, tmp_path / "out", tmp_path / "cache", bundle=bundle, iscc=compiler, csc=compiler)
    assert not (tmp_path / "out").exists()


def test_lifecycle_waits_for_registry_and_directory_removal(tmp_path, monkeypatch):
    install = tmp_path / "program"
    install.mkdir()
    states = iter([True, False, False])
    checks = []

    def registered():
        result = next(states)
        checks.append(result)
        if len(checks) == 3:
            install.rmdir()
        return result

    monkeypatch.setattr(lifecycle_smoke, "registered", registered)
    monkeypatch.setattr(lifecycle_smoke, "shortcuts", lambda: [])
    lifecycle_smoke.wait_uninstalled(install)
    assert checks == [True, False, False]


def test_setup_warnings_fail_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(package.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="Warning: unsafe option\n"))
    with pytest.raises(ValueError, match="warnings must be resolved"):
        package.compile_setup(["ISCC.exe"], tmp_path)


def test_lifecycle_refuses_existing_shortcuts(tmp_path, monkeypatch):
    shortcut = tmp_path / "existing.lnk"
    shortcut.touch()
    monkeypatch.setattr(lifecycle_smoke, "registered", lambda: False)
    monkeypatch.setattr(lifecycle_smoke, "shortcuts", lambda: [shortcut])
    with pytest.raises(ValueError, match="already present"):
        lifecycle_smoke.no_existing_install()


def test_addon_removal_keeps_state_and_accepts_linked_parent(tmp_path, launcher):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "redirected"
    junction(linked, actual)
    try:
        state = linked / "state"
        (state / "addons/nested").mkdir(parents=True)
        (state / "addons/nested/package.txt").write_bytes(b"add-on")
        (state / "workspaces").mkdir()
        preserved = state / "workspaces/preserved.txt"
        preserved.write_bytes(b"keep")
        env = {**os.environ, "CUTGRAPH_STATE_HOME": str(state)}
        subprocess.run([str(launcher), "--remove-addons"], env=env, check=True, timeout=15)
        assert not (state / "addons").exists()
        assert preserved.read_bytes() == b"keep"
        assert state.exists()
    finally:
        os.rmdir(linked)


def test_addon_removal_refuses_internal_junction_before_deleting(tmp_path, launcher):
    state, outside = tmp_path / "state", tmp_path / "outside"
    (state / "addons").mkdir(parents=True)
    outside.mkdir()
    protected = outside / "preserved.txt"
    protected.write_bytes(b"keep")
    first = state / "addons/first.txt"
    first.write_bytes(b"untouched")
    linked = state / "addons/linked"
    junction(linked, outside)
    try:
        result = subprocess.run([str(launcher), "--remove-addons"],
                                env={**os.environ, "CUTGRAPH_STATE_HOME": str(state)}, timeout=15)
        assert result.returncode == 1
        assert protected.read_bytes() == b"keep"
        assert first.read_bytes() == b"untouched"
    finally:
        os.rmdir(linked)


@pytest.mark.parametrize("state", ["relative", "~", "overlap"])
def test_launcher_rejects_unsafe_state_without_creating_it(tmp_path, launcher, state):
    value = str(launcher.parent / "data") if state == "overlap" else state
    result = subprocess.run([str(launcher), "--stop"], cwd=tmp_path,
                            env={**os.environ, "CUTGRAPH_STATE_HOME": value}, timeout=15)
    assert result.returncode == 1
    assert not (tmp_path / "relative").exists()
    assert not (launcher.parent / "data").exists()


def test_launcher_source_selection_is_closed():
    assert "installer/launcher/SessionObjects.cs" in LAUNCHER_SOURCES
    assert all("*" not in name for name in LAUNCHER_SOURCES)


def test_program_cleanup_requires_marker_and_keeps_uninstall_metadata(tmp_path, launcher):
    program = tmp_path / "program"
    program.mkdir()
    stale = program / "stale.txt"
    stale.write_bytes(b"obsolete")
    env = {**os.environ, "CUTGRAPH_STATE_HOME": str(tmp_path / "state")}
    command = [str(launcher), "--clear-program-files", str(program)]
    assert subprocess.run(command, env=env, timeout=15).returncode == 1
    assert stale.exists()
    marker = program / ".cutgraph-install"
    marker.write_text("[Install]\nId=A0352AC4-4E53-4582-84D1-B1D719AA4FD4\n")
    uninstall = program / "unins000.exe"
    uninstall.write_bytes(b"uninstaller metadata")
    subprocess.run(command, env=env, check=True, timeout=15)
    assert not stale.exists()
    assert marker.exists()
    assert uninstall.read_bytes() == b"uninstaller metadata"
    assert not (tmp_path / "state").exists()


def test_setup_path_check_resolves_parent_junction_overlap(tmp_path, launcher):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "redirected"
    junction(linked, actual)
    try:
        env = {**os.environ, "CUTGRAPH_STATE_HOME": str(actual / "data")}
        result = subprocess.run([str(launcher), "--check-install-path", str(linked)], env=env, timeout=15)
        assert result.returncode == 3
        assert not (actual / "data").exists()
    finally:
        os.rmdir(linked)


def test_health_probe_retries_malformed_responses(tmp_path, launcher):
    probe = tmp_path / "HealthProbe.cs"
    probe.write_text('''using System;
using System.Reflection;
internal static class Probe {
    static int Main(string[] args) {
        var type = Assembly.LoadFile(args[0]).GetType("Program");
        var healthy = type.GetMethod("Healthy", BindingFlags.NonPublic | BindingFlags.Static);
        Console.WriteLine(healthy.Invoke(null, new object[] { args[1] }));
        return 0;
    }
}''', encoding="utf-8")
    executable = tmp_path / "HealthProbe.exe"
    subprocess.run([str(find_csc()), "/nologo", f"/out:{executable}", str(probe)], check=True, timeout=30)
    response = b""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            for response in (b"invalid", b"{}", b'{"status":"ok"}', b'{"status":"ok","runtime":null}',
                             b'{"status":"ok","runtime":false}'):
                result = subprocess.run([str(executable), str(launcher), f"http://127.0.0.1:{server.server_port}/"],
                                        capture_output=True, text=True, check=True, timeout=10)
                assert result.stdout.strip() == "False"
            response = b'{"status":"ok","runtime":{"application":"standalone"}}'
            result = subprocess.run([str(executable), str(launcher), f"http://127.0.0.1:{server.server_port}/"],
                                    capture_output=True, text=True, check=True, timeout=10)
            assert result.stdout.strip() == "True"
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Exercises Windows private namespaces and access-control lists")
def test_private_namespace_objects_have_user_only_security(tmp_path):
    probe = tmp_path / "Probe.cs"
    probe.write_text('''using System;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;
internal static class Probe {
    static void Check(ObjectSecurity security) {
        var user = WindowsIdentity.GetCurrent().User;
        if (!user.Equals(security.GetOwner(typeof(SecurityIdentifier)))) throw new Exception("owner");
        if (!security.AreAccessRulesProtected) throw new Exception("inherited rules");
        foreach (AuthorizationRule rule in ((CommonObjectSecurity)security).GetAccessRules(true, true, typeof(SecurityIdentifier)))
            if (!user.Equals(rule.IdentityReference)) throw new Exception("foreign rule");
    }
    static int Main(string[] args) {
        using (var objects = new SessionObjects(args[0])) {
            Check(objects.Mutex.GetAccessControl());
            using (var signal = objects.CreateEvent("stop")) {
                Check(signal.GetAccessControl());
                using (var process = System.Diagnostics.Process.GetCurrentProcess())
                using (var sender = objects.TryOpenEvent("stop", process)) sender.Set();
                if (!signal.WaitOne(0)) throw new Exception("signal");
                try { objects.CreateEvent("stop").Dispose(); throw new Exception("reused event"); }
                catch (IOException) { }
            }
            objects.PublishOwner();
            using (var owner = objects.TryOpenOwner())
                if (owner.Id != System.Diagnostics.Process.GetCurrentProcess().Id) throw new Exception("process identity");
        }
        return 0;
    }
}''', encoding="utf-8")
    executable = tmp_path / "Probe.exe"
    subprocess.run([str(find_csc()), "/nologo", "/warnaserror+", "/platform:x64", f"/out:{executable}", str(probe),
                    *[str(build.SOURCE / "installer/launcher" / name)
                      for name in ("Native.cs", "DataPaths.cs", "SessionObjects.cs")]], check=True, timeout=30)
    subprocess.run([str(executable), str(tmp_path)], check=True, timeout=15)


@pytest.mark.parametrize("groups,expected", [
    ([("S-1-5-32-545", 7)], True),
    ([("S-1-5-32-544", 7)], False),
    ([("S-1-5-32-545", 7), ("S-1-5-32-544", 0x10)], False),
])
def test_standard_user_evidence_counts_deny_only_administrators(groups, expected):
    assert lifecycle_smoke.standard_user_from_groups(groups) is expected


@pytest.mark.skipif(sys.platform != "win32", reason="Queries Windows token groups")
def test_standard_user_evidence_reads_real_token_groups():
    assert isinstance(lifecycle_smoke.standard_user_verified(), bool)


def test_cleanup_preserves_original_failure_and_reports_orphans(monkeypatch):
    def stopped(_pids, _port):
        raise RuntimeError("fixture child survived")

    monkeypatch.setattr(launcher_smoke, "assert_stopped", stopped)
    original = ValueError("fixture install failed")
    launcher_smoke.cleanup_launchers([], [([1], 0)], original)
    assert "fixture child survived" in original.__notes__[0]
    with pytest.raises(RuntimeError, match="fixture child survived"):
        launcher_smoke.cleanup_launchers([], [([1], 0)], None)


@pytest.mark.parametrize("value,code", [("relative", 2), ("~", 2), ("overlap", 3), ("separate", 0)])
def test_setup_reports_distinct_path_errors(tmp_path, launcher, value, code):
    program = tmp_path / "application"
    state = str(program / "data") if value == "overlap" else str(tmp_path / "state") if value == "separate" else value
    result = subprocess.run([str(launcher), "--check-install-path", str(program)],
                            env={**os.environ, "CUTGRAPH_STATE_HOME": state}, cwd=tmp_path, timeout=15)
    assert result.returncode == code
    assert not program.exists()


def test_program_check_and_cleanup_refuse_junctions_without_deleting(tmp_path, launcher):
    program, outside = tmp_path / "application", tmp_path / "outside"
    program.mkdir()
    outside.mkdir()
    (program / ".cutgraph-install").write_text("[Install]\nId=A0352AC4-4E53-4582-84D1-B1D719AA4FD4\n")
    (program / "first.txt").write_bytes(b"keep")
    (outside / "protected.txt").write_bytes(b"keep")
    linked = program / "linked"
    junction(linked, outside)
    try:
        env = {**os.environ, "CUTGRAPH_STATE_HOME": str(tmp_path / "state")}
        for option in ("--check-install-path", "--clear-program-files"):
            result = subprocess.run([str(launcher), option, str(program)], env=env, timeout=15)
            assert result.returncode == 4
            assert (program / "first.txt").read_bytes() == b"keep"
            assert (outside / "protected.txt").read_bytes() == b"keep"
    finally:
        os.rmdir(linked)


@pytest.mark.parametrize("option", ["--remove-addons", "--clear-program-files"])
def test_cleanup_removes_readonly_files_and_nested_directories(tmp_path, launcher, option):
    state, program = tmp_path / "state", tmp_path / "application"
    target = state / "addons" if option == "--remove-addons" else program
    nested = target / "nested/deeper"
    nested.mkdir(parents=True)
    readonly = nested / "object.bin"
    readonly.write_bytes(b"obsolete")
    readonly.chmod(0o444)
    if option == "--clear-program-files":
        (program / ".cutgraph-install").write_text("[Install]\nId=A0352AC4-4E53-4582-84D1-B1D719AA4FD4\n")
    subprocess.run([str(launcher), option, *([str(program)] if option == "--clear-program-files" else [])],
                   env={**os.environ, "CUTGRAPH_STATE_HOME": str(state)}, check=True, timeout=15)
    assert not (target / "nested").exists()


def test_reparse_tag_policy_allows_cloud_placeholders(tmp_path, launcher):
    probe = tmp_path / "TagProbe.cs"
    probe.write_text('''using System;
using System.Reflection;
internal static class Probe {
    static int Main(string[] args) {
        var method = Assembly.LoadFile(args[0]).GetType("DataPaths").GetMethod("IsLink", BindingFlags.NonPublic | BindingFlags.Static);
        foreach (string arg in args[1].Split(','))
            Console.WriteLine(method.Invoke(null, new object[] { Convert.ToUInt32(arg, 16) }));
        return 0;
    }
}''', encoding="utf-8")
    executable = tmp_path / "TagProbe.exe"
    subprocess.run([str(find_csc()), "/nologo", f"/out:{executable}", str(probe)], check=True, timeout=30)
    # IO_REPARSE_TAG_CLOUD and IO_REPARSE_TAG_CLOUD_1 through IO_REPARSE_TAG_CLOUD_F.
    cloud = [0x9000001A | (variant << 12) for variant in range(16)]
    # MOUNT_POINT and SYMLINK are links; WOF and APPEXECLINK are other non-link tags.
    tags = [0xA0000003, 0xA000000C, 0, 0x80000017, 0x8000001B, *cloud]
    result = subprocess.run([str(executable), str(launcher), ",".join(f"{tag:x}" for tag in tags)],
                            capture_output=True, text=True, check=True, timeout=15)
    assert result.stdout.split() == ["True", "True", *(["False"] * (len(tags) - 2))]


@pytest.mark.skipif(sys.platform != "win32", reason="Compiles committed Windows launcher sources")
def test_revision_build_uses_real_git_blobs_and_refuses_missing_or_linked_selection(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    env = {**build.git_environment(), "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": ""}

    def git(*args, data=None):
        return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(tmp_path / "hooks"),
                               *args], cwd=source, env=env, input=data, capture_output=True, check=True).stdout

    git("init")
    selection = (*LAUNCHER_SOURCES, "installer/cutgraph.iss")
    for name in selection:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(build.SOURCE / name, target)
    (source / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    first_bytes = {name: (source / name).read_bytes() for name in selection}
    git("add", ".")
    git("commit", "-m", "First source snapshot")
    first = git("rev-parse", "HEAD").decode().strip()
    for name in ("installer/launcher/Program.cs", "installer/cutgraph.iss", "pyproject.toml"):
        (source / name).write_text("newer committed bytes", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Second source snapshot")
    (source / "installer/launcher/Program.cs").write_text("dirty bytes", encoding="utf-8")
    (source / "untracked.cs").write_text("untracked", encoding="utf-8")
    destination = tmp_path / "copied"
    assert build.copy_sources(source, destination, first, selection=selection) == sorted(selection)
    assert {name: (destination / name).read_bytes() for name in selection} == first_bytes
    for missing in ("untracked.cs", "missing.cs"):
        with pytest.raises(ValueError, match="committed build sources are missing"):
            build.tracked_sources(source, first, selection=(missing,))
    blob = git("hash-object", "-w", "--stdin", data=b"target").decode().strip()
    git("update-index", "--add", "--cacheinfo", f"120000,{blob},linked.cs")
    git("update-index", "--add", "--cacheinfo", f"160000,{first},component")
    git("commit", "-m", "Link and submodule snapshot")
    for name in ("linked.cs", "component"):
        with pytest.raises(ValueError, match="links and submodules are forbidden"):
            build.tracked_sources(source, selection=(name,))
    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    (bundle / "app/runtime.py").write_bytes(b"runtime")
    licensed_manifest(bundle, {"app/runtime.py": "exported path:runtime.py"}, first)

    def compile_setup(command, stage):
        assert Path(command[-1]).read_bytes() == first_bytes["installer/cutgraph.iss"]
        assert "/DAppVersion=1.2.3" in command
        output = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("/DOutputDir=")))
        (output / "CutgraphStudio-1.2.3-setup.exe").write_bytes(b"synthetic setup")

    monkeypatch.setattr(package, "compile_setup", compile_setup)
    compiler = tmp_path / "ISCC.exe"
    compiler.touch()
    monkeypatch.setattr(build, "SOURCE", source)
    monkeypatch.setattr(sys, "argv", ["build.py", "--installer", "--out", str(tmp_path / "out"),
                                     "--cache", str(tmp_path / "cache"), "--bundle", str(bundle),
                                     "--iscc", str(compiler), "--revision", first, "--offline"])
    assert build.main() == 0
    result = json.loads((tmp_path / "out/installer-build.json").read_text())
    assert result["sourceRevision"] == first
    assert result["version"] == "1.2.3"


def test_dirty_build_warning_names_resolved_revision(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "source_revision", lambda *_args: "selected-commit")
    monkeypatch.setattr(build, "git", lambda *_args: b" M file")
    monkeypatch.setattr(sources, "load_inputs", lambda _path: {})

    def produce(_inputs, _cache, destination, **_kwargs):
        destination.mkdir()
        (destination / sources.SOURCE_ARCHIVE).write_bytes(b"source")
        return {"artifact": {"name": sources.SOURCE_ARCHIVE, "sha256": "a" * 64}}

    monkeypatch.setattr(sources, "produce", produce)

    def assemble(_source, payload, *_args, **_kwargs):
        payload.mkdir()
        return {"files": [], "size": 0}

    monkeypatch.setattr(build, "assemble", assemble)
    build.build(build.SOURCE, tmp_path / "out", tmp_path / "cache", revision="older")
    assert "building committed revision selected-commit" in capsys.readouterr().err


def test_stop_waits_for_observed_process_without_stopping_replacement(tmp_path, launcher):
    probe = tmp_path / "ClosingInstance.cs"
    probe.write_text('''using System;
using System.IO;
using System.Threading;
internal static class Probe {
    static int Main(string[] args) {
        bool original = args[0] == "original";
        using (var objects = new SessionObjects(args[1])) {
            if (!objects.Mutex.WaitOne(15000)) return 3;
            try {
                using (var stop = objects.CreateEvent("stop")) {
                    objects.PublishOwner();
                    File.WriteAllText(Path.Combine(args[2], args[0] + "-ready"), "ready");
                    bool stopped = stop.WaitOne(original ? 15000 : 3500);
                    if (!original) return stopped ? 2 : 0;
                    if (!stopped) return 4;
                    File.WriteAllText(Path.Combine(args[2], "closing"), "closing");
                    Thread.Sleep(600);
                }
            } finally {
                objects.UnpublishOwner();
                objects.Mutex.ReleaseMutex();
            }
            // Hold the original process alive after relinquishing the instance mutex.
            Thread.Sleep(2000);
        }
        return 0;
    }
}''', encoding="utf-8")
    executable = tmp_path / "ClosingInstance.exe"
    subprocess.run([str(find_csc()), "/nologo", "/warnaserror+", "/platform:x64", f"/out:{executable}", str(probe),
                    *[str(build.SOURCE / "installer/launcher" / name)
                      for name in ("Native.cs", "DataPaths.cs", "SessionObjects.cs")]], check=True, timeout=30)
    env = {**os.environ, "CUTGRAPH_STATE_HOME": str(tmp_path / "state")}
    processes = []
    try:
        original = subprocess.Popen([str(executable), "original", str(launcher.parent), str(tmp_path)], env=env)
        processes.append(original)
        launcher_smoke.wait_for(lambda: (tmp_path / "original-ready").exists(), "synthetic instance ready", timeout=10)
        first_stop = subprocess.Popen([str(launcher), "--stop", "--headless"], env=env)
        processes.append(first_stop)
        launcher_smoke.wait_for(lambda: (tmp_path / "closing").exists(), "synthetic instance closing", timeout=10)
        second_stop = subprocess.Popen([str(launcher), "--stop", "--headless"], env=env)
        processes.append(second_stop)
        replacement = subprocess.Popen([str(executable), "replacement", str(launcher.parent), str(tmp_path)], env=env)
        processes.append(replacement)
        launcher_smoke.wait_for(lambda: (tmp_path / "replacement-ready").exists(), "replacement owns mutex", timeout=10)
        for stopping in (first_stop, second_stop):
            assert stopping.wait(timeout=10) == 0
            assert original.poll() == 0
        assert replacement.wait(timeout=10) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
