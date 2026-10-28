"""Portable build boundaries, using only fake downloads and synthetic trees."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

from installer import build, manifest, smoke
from pipeline import runtime_tools, scene_render, thumbnail


def test_bundle_carries_every_registered_addon_lock():
    from scripts.lock_requirements import ADDON_LOCKS

    assert all(build.is_source_path(f"{stem}.txt") for stem in ADDON_LOCKS)


@pytest.mark.parametrize("cached", [False, True])
def test_download_rejects_hash_mismatch(tmp_path, monkeypatch, cached):
    pin = {"name": "runtime", "url": "https://example.invalid/runtime.zip", "sha256": "0" * 64}
    if cached:
        (tmp_path / ("0" * 64 + "-runtime.zip")).write_bytes(b"tampered")
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"tampered"))
    with pytest.raises(ValueError, match="Hash mismatch"):
        build.download(pin, tmp_path)
    assert not list(tmp_path.glob("*.partial"))


def _serve(monkeypatch, responses, seen):
    def urlopen(request, **_kwargs):
        seen.append(request.full_url)
        body = responses[request.full_url]
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(body)
    monkeypatch.setattr(build.urllib.request, "urlopen", urlopen)


def test_download_prefers_the_byte_exact_mirror(tmp_path, monkeypatch):
    data, seen = b"pinned source", []
    pin = {"name": "src", "url": "https://upstream.invalid/src.tar.gz", "mirror": "https://mirror.invalid/src.tar.gz",
           "sha256": hashlib.sha256(data).hexdigest()}
    _serve(monkeypatch, {pin["mirror"]: data, pin["url"]: b"regenerated"}, seen)
    assert build.download(pin, tmp_path).read_bytes() == data
    assert seen == [pin["mirror"]]


@pytest.mark.parametrize("mirror", [b"stale mirror", OSError("mirror unavailable")])
def test_download_falls_back_to_upstream_when_the_mirror_fails(tmp_path, monkeypatch, mirror):
    data, seen = b"pinned source", []
    pin = {"name": "src", "url": "https://upstream.invalid/src.tar.gz", "mirror": "https://mirror.invalid/src.tar.gz",
           "sha256": hashlib.sha256(data).hexdigest()}
    _serve(monkeypatch, {pin["mirror"]: mirror, pin["url"]: data}, seen)
    assert build.download(pin, tmp_path).read_bytes() == data
    assert seen == [pin["mirror"], pin["url"]]
    assert not list(tmp_path.glob("*.partial"))


def test_download_rejects_when_neither_location_matches(tmp_path, monkeypatch):
    pin = {"name": "src", "url": "https://upstream.invalid/src.tar.gz", "mirror": "https://mirror.invalid/src.tar.gz",
           "sha256": "0" * 64}
    _serve(monkeypatch, {pin["mirror"]: b"one", pin["url"]: b"two"}, [])
    with pytest.raises(ValueError, match="Hash mismatch in downloaded input"):
        build.download(pin, tmp_path)
    assert not list(tmp_path.iterdir())


def test_download_verifies_then_reuses_cache(tmp_path, monkeypatch):
    data = b"pinned runtime"
    pin = {"name": "runtime", "url": "https://example.invalid/runtime.zip",
           "sha256": hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(data))
    path = build.download(pin, tmp_path)
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("Network on cache hit"))
    assert build.download(pin, tmp_path) == path
    assert path.read_bytes() == data


@pytest.mark.parametrize("path", [
    "app/.env",
    "app/.env.local", "app/.venv/pyvenv.cfg",
    "python/__pycache__/module.pyc", "app/models/weights.bin",
    "app/key.pem", "app/private.pfx", "other/file", "app/../outside", "app/C:alias",
])
def test_inventory_rejects_private_paths(tmp_path, path):
    with pytest.raises(ValueError):
        manifest.check_path(path)
    if ".." not in path and ":" not in path:
        planted = tmp_path / path
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_bytes(b"synthetic")
        with pytest.raises(ValueError):
            manifest.inventory(tmp_path, {})


def test_manifest_rejects_unexpected_and_changed_files(tmp_path):
    target = tmp_path / "app/runtime.py"
    target.parent.mkdir()
    target.write_bytes(b"runtime")
    origins = {"app/runtime.py": "exported path:runtime.py"}
    result = manifest.write_manifest(tmp_path, origins)
    assert manifest.verify_manifest(tmp_path) == result
    target.write_bytes(b"changed")
    with pytest.raises(ValueError, match="differ"):
        manifest.verify_manifest(tmp_path)
    target.write_bytes(b"runtime")
    (target.parent / "untracked.py").touch()
    with pytest.raises(ValueError, match="Unregistered"):
        manifest.verify_manifest(tmp_path)


def test_manifest_rejects_missing_files(tmp_path):
    with pytest.raises(ValueError, match="Missing"):
        manifest.inventory(tmp_path, {"app/missing.py": "exported path:missing.py"})


@pytest.mark.parametrize("name", ["../escape", "/escape", "c:escape", "//server/share", "app\\escape", "CON.txt", "file."])
def test_archive_rejects_unsafe_members(tmp_path, name):
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(name, b"synthetic")
    if "\\" in name:
        archive.write_bytes(archive.read_bytes().replace(name.replace("\\", "/").encode(), name.encode()))
    with pytest.raises(ValueError, match="Unsafe archive"):
        build.extract_zip(archive, tmp_path / "output")


def test_source_copy_reads_head_blobs_instead_of_dirty_files(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("runtime.py", "junk.py"):
        (source / name).write_bytes(b"synthetic")
    files = b"100644 blob hash\truntime.py\x00100644 blob other\tunselected.txt\x00"
    monkeypatch.setattr(build, "git", lambda _source, *args: b"revision" if args[0] == "rev-parse" else files)
    monkeypatch.setattr(build.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        stdout=b"hash blob 9\ncommitted\n"))
    assert build.copy_sources(source, tmp_path / "stage") == ["runtime.py"]
    assert list((tmp_path / "stage").iterdir()) == [tmp_path / "stage/runtime.py"]
    assert (tmp_path / "stage/runtime.py").read_bytes() == b"committed"


def test_output_refuses_any_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / ".git").write_text("gitdir: metadata")
    for destination in (source / "output", source.parent, other / "output"):
        with pytest.raises(ValueError, match="outside"):
            build.outside_checkout(destination, source)


def test_nonruntime_metadata_omission_is_narrow_and_recorded(tmp_path):
    metadata = tmp_path / "fastapi/.agents/skills/SKILL.md"
    metadata.parent.mkdir(parents=True)
    metadata.write_bytes(b"upstream documentation")
    unrelated = tmp_path / "unexpected/.agents/entry.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unexpected")
    omitted = build.omit_nonruntime_metadata(tmp_path)
    assert omitted == [{"path": "fastapi/.agents/skills/SKILL.md",
                        "sha256": hashlib.sha256(b"upstream documentation").hexdigest()}]
    assert not metadata.exists()
    assert unrelated.exists()


def test_node_resolution_order_and_media_bundle(tmp_path, monkeypatch):
    app = tmp_path / "bundle/app"
    app.mkdir(parents=True)
    (app.parent / manifest.MANIFEST).write_text("{}")
    node = app.parent / "node/node.exe"
    node.parent.mkdir()
    node.touch()
    override = tmp_path / "custom.exe"
    override.touch()
    path_node = tmp_path / "path-node.exe"
    path_node.touch()
    monkeypatch.setattr(runtime_tools.shutil, "which", lambda _name, **_kwargs: str(path_node))
    assert runtime_tools.node_path(source_root=app, environ={"CUTGRAPH_NODE": str(override)}) == override
    assert runtime_tools.node_path(source_root=app, environ={}) == node
    node.unlink()
    assert runtime_tools.node_path(source_root=app, environ={}) == path_node
    monkeypatch.setattr(runtime_tools.shutil, "which", lambda *_args, **_kwargs: None)
    assert runtime_tools.node_path(source_root=app, environ={}) is None
    for invalid in ("relative.exe", str(tmp_path / "missing.exe")):
        with pytest.raises(RuntimeError, match="absolute"):
            runtime_tools.node_path(source_root=app, environ={"CUTGRAPH_NODE": invalid})
    ffmpeg = app.parent / "ffmpeg/ffmpeg.exe"
    ffmpeg.parent.mkdir()
    ffmpeg.touch()
    assert runtime_tools.ffmpeg_path(source_root=app, environ={}) == ffmpeg


@pytest.mark.parametrize("app_name,marker", [("checkout", True), ("app", False), ("checkout", False)])
def test_source_checkout_ignores_sibling_tools(tmp_path, monkeypatch, app_name, marker):
    app = tmp_path / app_name
    app.mkdir()
    if marker:
        (tmp_path / manifest.MANIFEST).write_text("{}")
    for tool in ("node", "ffmpeg", "ffprobe"):
        sibling = tmp_path / tool / (tool + ".exe")
        sibling.parent.mkdir()
        sibling.touch()
    path_tool = tmp_path / "path-tool.exe"
    path_tool.touch()
    monkeypatch.setattr(runtime_tools.shutil, "which", lambda *_args, **_kwargs: str(path_tool))
    assert runtime_tools.bundle_root(app) is None
    for resolver in (runtime_tools.node_path, runtime_tools.ffmpeg_path, runtime_tools.ffprobe_path):
        assert resolver(source_root=app, environ={}) == path_tool


def test_bundle_health_reports_installed_build(tmp_path, monkeypatch):
    from studio.routes import system

    app = tmp_path / "app"
    app.mkdir()
    (tmp_path / manifest.MANIFEST).write_text("{}")
    monkeypatch.setattr(runtime_tools, "_root", lambda _source=None: app)
    monkeypatch.setattr(runtime_tools, "tool_path", lambda name: name)
    assert system.health()["capabilities"]["packaged"] is True


@pytest.mark.parametrize("module,render,suffix", [(scene_render, "render_scene", ".mp4"),
                                                 (thumbnail, "render_thumbnail", ".png")])
def test_renderers_use_resolved_node(tmp_path, monkeypatch, module, render, suffix):
    node = tmp_path / "bundled-node.exe"
    monkeypatch.setattr(module, "node_path", lambda: node)
    monkeypatch.setattr(scene_render, "tool_path", lambda _name: "bundled-ffmpeg.exe")
    dest = tmp_path / ("render" + suffix)

    def run(command, **_kwargs):
        assert command[0] == str(node)
        Path(command[3]).write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"event": "done", "frames": 60}), stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    getattr(module, render)({}, dest)
    assert dest.read_bytes() == b"rendered"


@pytest.mark.parametrize("module,render,error_type", [
    (scene_render, "render_scene", scene_render.SceneRenderError),
    (thumbnail, "render_thumbnail", thumbnail.ThumbnailRenderError),
])
def test_invalid_node_override_raises_renderer_error(tmp_path, monkeypatch, module, render, error_type):
    monkeypatch.setenv("CUTGRAPH_NODE", "missing-relative.exe")
    with pytest.raises(error_type, match="CUTGRAPH_NODE must name an existing absolute executable path"):
        getattr(module, render)({}, tmp_path / "render")


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_sources_reject_git_links_and_submodules(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(build, "git", lambda *_args: f"{mode} blob hash\truntime.py\0".encode())
    with pytest.raises(ValueError, match="links and submodules"):
        build.tracked_sources(tmp_path, "revision")


def test_git_environment_scrubs_inherited_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "elsewhere")
    monkeypatch.setenv("GIT_WORK_TREE", "elsewhere")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    def run(command, **kwargs):
        assert command == ["git", "rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"]
        assert kwargs["cwd"] == tmp_path
        env = kwargs["env"]
        assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT"} & env.keys()
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == build.os.devnull
        return SimpleNamespace(stdout=b"revision\n")
    monkeypatch.setattr(build.subprocess, "run", run)
    assert build.source_revision(tmp_path) == "revision"


def test_missing_head_fails(tmp_path, monkeypatch):
    def git(*_args):
        raise subprocess.CalledProcessError(128, "git")
    monkeypatch.setattr(build, "git", git)
    with pytest.raises(ValueError, match="committed HEAD"):
        build.source_revision(tmp_path)


@pytest.mark.parametrize("document", ["node-SHASUMS256.txt", "node-SHASUMS256.txt.asc"])
def test_node_checksum_line_must_match_both_documents(tmp_path, document):
    pins = {"node": {"version": "1.2.3", "sha256": "a" * 64}}
    archives = {}
    for name in ("node-SHASUMS256.txt", "node-SHASUMS256.txt.asc"):
        archives[name] = tmp_path / name
        archives[name].write_text("a" * 64 + "  node-v1.2.3-win-x64.zip\n")
    build.verify_node_checksums(pins, archives)
    archives[document].write_text("a" * 64 + "  wrong.zip\n")
    with pytest.raises(ValueError, match="disagrees"):
        build.verify_node_checksums(pins, archives)


@pytest.mark.parametrize("name", ["python", "node"])
def test_version_pin_mismatch(tmp_path, name):
    (tmp_path / ".python-version").write_text("3.12.10")
    (tmp_path / ".nvmrc").write_text("24.22.1")
    pins = [{"name": key, "version": version, "sha256": "a" * 64,
             "url": "https://example.invalid/input", "license": "MIT"}
            for key, version in (("python", "3.12.10"), ("node", "24.22.1"))]
    next(item for item in pins if item["name"] == name)["version"] = "0.0.0"
    with pytest.raises(ValueError, match="does not match"):
        build.validate_pins({"inputs": pins}, tmp_path)


@pytest.mark.parametrize("field,value", [("url", "http://example.invalid/input"), ("sha256", "bad"), ("license", "")])
def test_pins_require_https_hash_and_license(tmp_path, field, value):
    (tmp_path / ".python-version").write_text("3.12.10")
    (tmp_path / ".nvmrc").write_text("24.22.1")
    pins = [{"name": key, "version": version, "sha256": "a" * 64,
             "url": "https://example.invalid/input", "license": "MIT"}
            for key, version in (("python", "3.12.10"), ("node", "24.22.1"))]
    pins[0][field] = value
    with pytest.raises(ValueError, match="Every input"):
        build.validate_pins({"inputs": pins}, tmp_path)


@pytest.mark.parametrize("kind", ["symlink", "case collision"])
def test_archive_rejects_links_and_case_collisions(tmp_path, kind):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        if kind == "symlink":
            member = zipfile.ZipInfo("link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(member, "target")
        else:
            output.writestr("file", "one")
            output.writestr("FILE", "two")
    with pytest.raises(ValueError, match="Unsafe archive"):
        build.extract_zip(archive, tmp_path / "output")


@pytest.mark.parametrize("populated", [False, True])
def test_build_refuses_preexisting_output(tmp_path, populated):
    out = tmp_path / "out"
    out.mkdir()
    if populated:
        (out / "partial").touch()
    with pytest.raises(ValueError, match="must not already exist"):
        build.build(tmp_path / "source", out, tmp_path / "cache")


def test_failed_build_removes_partial_output(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "source_revision", lambda *_args: "revision")
    monkeypatch.setattr(build, "git", lambda *_args: b"")
    def fail(_source, out, *_args, **_kwargs):
        out.mkdir()
        (out / "partial").touch()
        raise ValueError("synthetic failure")
    monkeypatch.setattr(build, "assemble", fail)
    with pytest.raises(ValueError, match="synthetic failure"):
        build.build(tmp_path / "source", tmp_path / "out", tmp_path / "cache")
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".portable-output-*"))


def test_console_launchers_are_removed_recorded_and_forbidden(tmp_path):
    script = tmp_path / "bin/tool.exe"
    script.parent.mkdir()
    script.write_bytes(b"nonportable launcher")
    assert build.omit_nonruntime_metadata(tmp_path) == [
        {"path": "bin/tool.exe", "reason": "nonportable console launcher"}]
    assert not script.exists()
    with pytest.raises(ValueError, match="console launcher"):
        manifest.check_path("python/Lib/site-packages/bin/tool.exe")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le"])
@pytest.mark.parametrize("filename", ["module.py", "RECORD", "direct_url.json", "launcher.exe"])
def test_manifest_rejects_build_paths_in_text_and_binary_tails(tmp_path, encoding, filename):
    path = tmp_path / filename
    path.write_bytes(b"\0" * (1024 * 1024 - 3) + str(tmp_path).encode(encoding))
    with pytest.raises(ValueError, match="Build output path"):
        manifest.reject_build_paths(path, (tmp_path,))


def test_manifest_rejects_undeclared_generated_artifacts(tmp_path):
    target = tmp_path / "app/unknown.py"
    target.parent.mkdir()
    target.touch()
    with pytest.raises(ValueError, match="Unregistered"):
        manifest.inventory(tmp_path, {"app/unknown.py": "generated:anything"})


@pytest.mark.parametrize("error", [ConnectionResetError(), ConnectionRefusedError(), smoke.http.client.RemoteDisconnected("")])
def test_startup_wait_retries_connection_failures(tmp_path, monkeypatch, error):
    calls = iter([error, {"status": "ok"}])
    def request(*_args):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(smoke, "request", request)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    process = Mock(spec=subprocess.Popen)
    process.poll.return_value = None
    assert smoke.wait_for_health("http://localhost", process,
                                 tmp_path / "log", smoke.time.monotonic() + 10) == {"status": "ok"}


def test_smoke_drops_overrides_and_import_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path / "Windows"))
    monkeypatch.setenv("CUTGRAPH_NODE", "outside")
    monkeypatch.setenv("PYTHONPATH", "outside")
    monkeypatch.setenv("NODE_OPTIONS", "outside")
    monkeypatch.setenv("CUTGRAPH_FFMPEG", "outside")
    state = tmp_path / "state"
    env = smoke.isolated_environment(state)
    assert not {"CUTGRAPH_NODE", "PYTHONPATH", "NODE_OPTIONS", "CUTGRAPH_FFMPEG"} & env.keys()
    assert env["CUTGRAPH_STATE_HOME"] == str(state)
    assert "System32" in env["PATH"]


@pytest.mark.parametrize("attempts", [
    {"scene": ["h264_qsv", "h264_mf"], "export": ["h264_qsv", "h264_mf"]},
    {"scene": ["h264_mf"], "export": ["h264_mf"]},
])
def test_smoke_asserts_expected_successful_encoder(attempts):
    assert smoke.successful_encoders(attempts, "h264_mf") == {"scene": "h264_mf", "export": "h264_mf"}


@pytest.mark.parametrize("attempts", [
    {"scene": [], "export": ["h264_mf"]},
    {"scene": ["h264_qsv"], "export": ["h264_mf"]},
    {"scene": ["h264_mf"], "export": ["h264_nvenc"]},
])
def test_smoke_rejects_missing_or_unexpected_successful_encoders(attempts):
    with pytest.raises(RuntimeError):
        smoke.successful_encoders(attempts, "h264_mf")


def test_restricted_smoke_requires_an_expected_encoder(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTGRAPH_TEST_ENCODERS", "h264_mf")
    monkeypatch.setattr(smoke.sys, "platform", "win32")
    with pytest.raises(ValueError, match="requires --expected-encoder"):
        smoke.smoke(tmp_path / "bundle", tmp_path)


def test_windowed_launcher_logs_in_state_home(tmp_path, monkeypatch):
    import config
    import sys
    from scripts import studio_app

    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    studio_app._configure_windowed_stdio()
    stream = sys.stdout
    assert stream is not None and stream is sys.stderr
    try:
        print("windowed diagnostics")
        assert (tmp_path / "runtime/backend.log").read_text() == "windowed diagnostics\n"
    finally:
        stream.close()
