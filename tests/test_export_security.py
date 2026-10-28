"""Offline regressions for document and export privacy boundaries."""
from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

import config
from pipeline import bridge_token, caption_burn, channels, graphics, project, stock, web_security
from pipeline import compile as compiler
from pipeline.errors import ValidationError
from pipeline.secret_store import SecretStoreError
from pipeline.agent_session import FakeAgentSession
from scripts import agent_host, mcp_gpu
from studio.app import app

ROOT = Path(__file__).resolve().parents[1]


def test_pyannote_metrics_disabled_before_inference_imports() -> None:
    command = (
        "import os, sys; import pipeline.transcribe; "
        "assert os.environ['PYANNOTE_METRICS_ENABLED'] == 'false'; "
        "assert not {'pyannote.audio', 'whisperx', 'torch'} & sys.modules.keys()"
    )
    subprocess.run([sys.executable, "-c", command], cwd=ROOT,
                   env=dict(os.environ, PYANNOTE_METRICS_ENABLED="true"),
                   check=True, capture_output=True, text=True)


@pytest.mark.parametrize("stage", ["validation", "subtitle"])
@pytest.mark.parametrize("key,value", [
    ("activeColor", "&HFFFFFF\n[Events]\nDialogue: injected"),
    ("baseColor", "&HFFFFFF\r[Events]"), ("emphasisColor", "invalid"),
    ("font", "Inter\n[Events]"), ("alignment", 10), ("outlineRatio", 0),
    ("outlineRatio", 0.000001),
])
def test_caption_style_refused_at_validation_and_subtitle_write(
    tmp_path: Path, key: str, value: object, stage: str,
) -> None:
    doc = {"name": "video", "tracks": [], "captions": {"enabled": True, "style": {key: value}}}
    if stage == "validation":
        with pytest.raises(ValidationError):
            project.validate(project.from_dict(doc))
        return
    style = channels.resolve_caption_style(None)
    style[key] = value
    with pytest.raises(ValidationError):
        caption_burn.write_karaoke_ass("video", [{"word": "safe", "start": 0, "end": 1}],
                                       320, 240, style=style, output_dir=tmp_path, runtime_root=tmp_path)
    assert not list(tmp_path.glob("*.ass"))


@pytest.mark.parametrize("field", ["source", "proxy"])
@pytest.mark.parametrize("ref", ["runtime/secrets/key", "runtime/secret.mp4", "outputs/notes/video.mp4",
                                  "workspaces/example/workspace.json", "data/source/secret.json"])
def test_document_refuses_non_media_references(tmp_path: Path, field: str, ref: str) -> None:
    doc = {"name": "video", "tracks": [{"id": "v", "kind": "video", "clips": [{
        "id": "c", "media": {"source": "data/source/clip.mp4", field: ref},
        "srcInF": 0, "srcOutF": 30, "tlStartF": 0,
    }]}]}
    path = tmp_path / "video.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValidationError):
        project.validate(project.from_dict(json.loads(path.read_text(encoding="utf-8"))))


def _media_document(ref: str) -> dict:
    return {"name": "video", "tracks": [{"id": "v", "kind": "video", "clips": [{
        "id": "c", "media": {"source": ref}, "srcInF": 0, "srcOutF": 30, "tlStartF": 0,
    }]}]}


@pytest.mark.parametrize("extension", ["png", "jpg", "jpeg", "PNG"])
def test_document_accepts_generated_thumbnail_media(extension: str) -> None:
    project.validate(project.from_dict(_media_document(f"outputs/thumbnails/poster.{extension}")))


@pytest.mark.parametrize("extension", ["mp4", "json", "gif", "webp"])
def test_thumbnail_root_refuses_other_extensions(extension: str) -> None:
    with pytest.raises(ValidationError):
        project.validate(project.from_dict(_media_document(f"outputs/thumbnails/poster.{extension}")))


@pytest.mark.parametrize("ref", ["data/source/a\x00.mp4", "data/source/a\n.mp4",
    "data/source/a\x7f.mp4", "data/source/a\x85.mp4", "data/source/a\u2028.mp4", "data/source/a\u2029.mp4",
    "data/source/.../clip.mp4",
    "data/source/ . /clip.mp4", "data/source/   /clip.mp4", "data/source/./clip.mp4"])
def test_document_validation_refuses_ambiguous_media_paths(ref: str) -> None:
    with pytest.raises(ValidationError):
        project.validate(project.from_dict(_media_document(ref)))


def test_export_refuses_unsafe_media_written_directly_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    doc = _media_document("data/source/ . /clip.mp4")
    (tmp_path / "video.json").write_text(json.dumps(doc), encoding="utf-8")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            displayed = await client.get("/api/project/video")
            assert displayed.status_code == 200
            exported = await client.post("/api/jobs", json={"kind": "render_final", "project": "video"})
            assert exported.status_code == 400

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [300, 301, 302, 304, 307, 308, 400, 500])
def test_stock_download_refuses_non_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(stock, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, content=b"not media"))))
    dest = tmp_path / "download.mp4"
    with pytest.raises(stock.StockError, match="download failed"):
        stock._download("https://videos.pexels.com/clip.mp4", dest)
    assert not dest.exists()


def test_stock_environment_cannot_redirect_keys_or_allow_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTGRAPH_STOCK_PEXELS_BASE", "http://fixture.invalid")
    monkeypatch.setenv("CUTGRAPH_STOCK_PIXABAY_BASE", "http://fixture.invalid")
    monkeypatch.setenv("CUTGRAPH_STOCK_ALLOWED_HOSTS", "fixture.invalid")
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(stock, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    stock.search("offline", kind="all")
    assert requests
    assert all(request.url.scheme == "https" and request.url.host in {"api.pexels.com", "pixabay.com"}
               for request in requests)
    for url in ("http://fixture.invalid/clip.mp4", "https://fixture.invalid/clip.mp4"):
        with pytest.raises(stock.StockError):
            stock._validate_download_url(url)


def _dacl(path: Path) -> str:
    """The path's DACL in SDDL form, read directly rather than through PowerShell."""
    loader = getattr(ctypes, "WinDLL")
    security, kernel = loader("advapi32", use_last_error=True), loader("kernel32", use_last_error=True)
    get_security = security.GetFileSecurityW
    get_security.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p,
                             ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    get_security.restype = ctypes.c_int
    size = ctypes.c_uint32()
    get_security(str(path), 4, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    assert get_security(str(path), 4, buffer, size.value, ctypes.byref(size))
    convert = security.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                        ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_void_p]
    convert.restype = ctypes.c_int
    descriptor = ctypes.c_wchar_p()
    assert convert(buffer, 1, 4, ctypes.byref(descriptor), None)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return descriptor.value or ""
    finally:
        kernel.LocalFree(descriptor)


def test_bridge_config_keeps_token_in_private_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    ctx = agent_host.ToolCtx(slug="video", read_doc=lambda: (None, 0), read_transcript=lambda: None,
                             fps=lambda: 30, emit=lambda event: None)
    options = agent_host.build_options(tmp_path, ctx, key="offline-test-key")
    assert isinstance(options.mcp_servers, dict)
    gpu = options.mcp_servers["gpu"]
    assert isinstance(gpu, dict)
    encoded = json.dumps(gpu)
    assert web_security.INTERNAL_TOKEN not in encoded
    gpu_env = gpu.get("env")
    assert isinstance(gpu_env, dict)
    token_path = gpu_env.get("CUTGRAPH_INTERNAL_TOKEN_FILE")
    assert isinstance(token_path, str)
    token_file = Path(token_path)
    assert token_file.parent == tmp_path / "runtime" / "claude-agent"
    assert token_file.read_text(encoding="utf-8") == web_security.INTERNAL_TOKEN
    if os.name == "nt":
        dacl = _dacl(token_file)
        assert dacl == "D:P(A;;FA;;;OW)", dacl
    else:
        assert token_file.stat().st_mode & 0o077 == 0
        assert token_file.parent.stat().st_mode & 0o077 == 0


def _bridge_path(root: Path) -> Path:
    ctx = agent_host.ToolCtx(slug="video", read_doc=lambda: (None, 0), read_transcript=lambda: None,
                             fps=lambda: 30, emit=lambda event: None)
    options = agent_host.build_options(root, ctx, key="offline-test-key")
    assert isinstance(options.mcp_servers, dict)
    gpu = options.mcp_servers["gpu"]
    assert isinstance(gpu, dict)
    env = gpu.get("env")
    assert isinstance(env, dict)
    token_path = env.get("CUTGRAPH_INTERNAL_TOKEN_FILE")
    assert isinstance(token_path, str)
    return Path(token_path)


def test_bridge_file_is_written_once_per_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    token = _bridge_path(tmp_path)
    assert str(os.getpid()) in token.name
    original = token.stat().st_mtime_ns
    assert _bridge_path(tmp_path) == token
    assert token.stat().st_mtime_ns == original


def test_bridge_file_removed_on_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    token = _bridge_path(tmp_path)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            assert token.exists()

    asyncio.run(scenario())
    assert not token.exists()


def test_bridge_token_created_at_startup_without_assistant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            token = config.RUNTIME_DIR / "claude-agent" / f"internal-token-{os.getpid()}"
            assert token.read_text(encoding="utf-8") == web_security.INTERNAL_TOKEN
        assert not token.exists()

    asyncio.run(scenario())


def test_bridge_file_outlives_every_assistant_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    token = _bridge_path(tmp_path)
    sessions: list[FakeAgentSession] = []

    def session(slug: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        result = FakeAgentSession()
        sessions.append(result)
        return result

    async def scenario() -> None:
        host = agent_host.AgentHost(session, lambda slug: agent_host.ToolCtx(
            slug=slug, read_doc=lambda: (None, 0), read_transcript=lambda: None,
            fps=lambda: 30, emit=lambda event: None))
        try:
            await host.post_turn("first", "offline")
            await host.post_turn("second", "offline")
            await asyncio.gather(host._evict("first"), host._evict("second"))
            assert token.read_text(encoding="utf-8") == web_security.INTERNAL_TOKEN
            assert len(sessions) == 2 and all(item.closed for item in sessions)
        finally:
            await host.aclose_all()
        assert token.exists()

    asyncio.run(scenario())
    bridge_token.cleanup()
    assert not token.exists()


def test_bridge_cleanup_keeps_a_file_a_reader_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    token = bridge_token.create()
    unlink = Path.unlink

    def held(path: Path, missing_ok: bool = False) -> None:
        if path == token:
            raise PermissionError(32, "in use")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held)
    bridge_token.cleanup()
    assert token.exists()
    monkeypatch.setattr(Path, "unlink", unlink)
    bridge_token.cleanup()
    assert not token.exists()


def test_bridge_create_sweeps_files_of_stopped_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    folder = tmp_path / "runtime" / "claude-agent"
    folder.mkdir(parents=True)
    stopped = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                             capture_output=True, text=True, check=True)
    dead = folder / f"internal-token-{int(stopped.stdout)}"
    alive = folder / f"internal-token-{os.getppid()}"
    others = [folder / "notes.txt", folder / "internal-token-99999999999999999999", folder / "internal-token-\u00b2",
              folder / "internal-token-0", folder / "internal-token-x"]
    for path in (dead, alive, *others):
        path.write_text("x", encoding="utf-8")
    assert bridge_token._running(os.getpid()) and bridge_token._running(os.getppid())
    try:
        bridge_token.create()
        assert not dead.exists()
        assert alive.exists() and all(path.exists() for path in others)
    finally:
        bridge_token.cleanup()


@pytest.mark.parametrize("error", [PermissionError(5, "denied"), SecretStoreError("could not protect")])
def test_startup_continues_when_the_bridge_file_cannot_be_prepared(monkeypatch: pytest.MonkeyPatch,
                                                                   error: Exception) -> None:
    def refuse() -> Path:
        raise error

    monkeypatch.setattr(bridge_token, "create", refuse)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(scenario())


def test_bridge_files_isolate_two_processes_and_exit_cleanup(tmp_path: Path) -> None:
    command = (
        "import json, os, sys; import config; from pathlib import Path; "
        "from scripts import agent_host; from pipeline import web_security; "
        "web_security.INTERNAL_TOKEN = os.environ.pop('BRIDGE_TEST_TOKEN'); "
        "ctx = agent_host.ToolCtx(slug='video', read_doc=lambda: (None, 0), "
        "read_transcript=lambda: None, fps=lambda: 30, emit=lambda event: None); "
        "options = agent_host.build_options(Path.cwd(), ctx, key='offline-test-key'); "
        "print(json.dumps({'pid': os.getpid(), 'path': options.mcp_servers['gpu']['env']"
        "['CUTGRAPH_INTERNAL_TOKEN_FILE']}), flush=True); sys.stdin.readline()"
    )
    children: list[subprocess.Popen[str]] = []
    try:
        paths = []
        for value in ("first-process-token", "second-process-token"):
            child = subprocess.Popen([sys.executable, "-u", "-c", command], cwd=ROOT,
                                     env=dict(os.environ, CUTGRAPH_STATE_HOME=str(tmp_path), BRIDGE_TEST_TOKEN=value),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            children.append(child)
            assert child.stdout is not None
            record = json.loads(child.stdout.readline())
            token = Path(record["path"])
            paths.append(token)
            assert str(record["pid"]) in token.name
            assert token.read_text(encoding="utf-8") == value
        assert paths[0] != paths[1]
        assert paths[0].read_text(encoding="utf-8") == "first-process-token"
        children[0].communicate("\n", timeout=20)
        assert children[0].returncode == 0
        assert not paths[0].exists()
        assert paths[1].read_text(encoding="utf-8") == "second-process-token"
        children[1].communicate("\n", timeout=20)
        assert children[1].returncode == 0
        assert not paths[1].exists()
    finally:
        for child in children:
            if child.poll() is None:
                child.communicate("\n", timeout=20)


def test_security_documents_describe_actual_boundaries() -> None:
    security = (ROOT / "docs/security-model.md").read_text(encoding="utf-8")
    assert "saved or patched" in security
    assert "before export, compile and caption burn" in security
    assert "Reading a saved document for display is not a boundary" in security
    assert "other OS accounts" in security and "single-user" in security
    for name in ("privacy.md", "architecture.md"):
        document = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "can quote transcript words" in document


def test_bridge_reads_token_file_and_ignores_proxies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("private-bridge-token", encoding="utf-8")
    monkeypatch.setenv("CUTGRAPH_INTERNAL_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("CUTGRAPH_INTERNAL_TOKEN", "wrong-token")
    seen: list[httpx.Request] = []
    original = httpx.Client

    def client(**kwargs: object) -> httpx.Client:
        assert kwargs.get("trust_env") is False

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"ok": True})

        return original(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(mcp_gpu.httpx, "Client", client)
    assert json.loads(mcp_gpu.transcribe())["ok"]
    assert seen[0].headers["X-Cutgraph-Internal"] == "private-bridge-token"


@pytest.mark.parametrize("method,path", [("POST", "/api/agent/connection"),
    ("POST", "/api/agent/connection/verify"), ("DELETE", "/api/agent/connection")])
def test_connection_routes_bound_streamed_json(monkeypatch: pytest.MonkeyPatch, method: str, path: str) -> None:
    monkeypatch.setattr(config, "MAX_JSON_BODY_BYTES", 64)
    consumed = 0

    async def body():
        nonlocal consumed
        for _ in range(16):
            consumed += 1
            yield b" " * 32

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.request(method, path, content=body())

    assert asyncio.run(scenario()).status_code == 413
    assert consumed == 3


@pytest.mark.parametrize("method,path", [("POST", "/api/agent/connection"),
    ("POST", "/api/agent/connection/verify"), ("DELETE", "/api/agent/connection")])
def test_connection_routes_require_json_objects(method: str, path: str) -> None:
    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.request(method, path, json=[])

    response = asyncio.run(scenario())
    assert response.status_code == 400
    assert response.json()["error"] == "JSON body must be an object."


@pytest.mark.parametrize("method,path", [("POST", "/api/agent/connection/verify"),
    ("DELETE", "/api/agent/connection")])
def test_connection_routes_accept_empty_body(monkeypatch: pytest.MonkeyPatch, method: str, path: str) -> None:
    from studio import state

    monkeypatch.setattr(state._claude_connection, "verify", lambda: {"state": "not_connected"})
    monkeypatch.setattr(state._claude_connection, "disconnect", lambda: {"state": "not_connected"})

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.request(method, path)

    assert asyncio.run(scenario()).status_code == 200


def test_fixture_environment_excludes_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("fixture_runner", ROOT / "tests/e2e/run.py")
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "PEXELS_API_KEY", "PIXABAY_API_KEY",
                "CUTGRAPH_DEV_ORIGIN", "HTTP_PROXY", "UNRELATED_SECRET"):
        monkeypatch.setenv(key, "excluded")
    monkeypatch.setenv("PROGRAMFILES", "system-apps")
    monkeypatch.setenv("PROGRAMFILES(X86)", "system-apps-32")
    env = runner.minimal_environment()
    assert "PATH" in env
    assert "excluded" not in env.values()
    assert env["PROGRAMFILES"] == "system-apps"
    assert env["PROGRAMFILES(X86)"] == "system-apps-32"


def test_privacy_documents_cover_preferences_and_metrics() -> None:
    privacy = (ROOT / "docs/privacy.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    security = (ROOT / "docs/security-model.md").read_text(encoding="utf-8")
    assert "PYANNOTE_METRICS_ENABLED=false" in privacy and "otel.pyannote.ai" in privacy
    for document in (privacy, architecture):
        assert "read_preferences" in document and "outputs/decisions" in document
        assert "other Videos" in document
    assert "token file" in security and "same OS user" in security
    assert "Stock endpoints can be overridden" not in privacy
    assert "stock endpoint\noverrides" not in security


def test_preference_summaries_disclosed_in_privacy_and_architecture() -> None:
    for name in ("privacy.md", "architecture.md"):
        document = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "read_preferences" in document
        assert "outputs/decisions" in document
        assert "other Videos" in document


def test_rotation_smoke_uses_synthetic_media() -> None:
    source = (ROOT / "tests/test_project_start.py").read_text(encoding="utf-8")
    assert "lavfi" in source and "rotate=90" in source


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires ffmpeg to exercise text export")
@pytest.mark.parametrize("kind", ["text", "graphic"])
def test_literal_text_and_graphics_export(tmp_path: Path, kind: str) -> None:
    overlays = tmp_path / "overlays"
    item = project.TextItem(id="text", text=r"50% off \\ literal %{metadata}", preset="hook",
                            pos={"x": 0.5, "y": 0.5}, tlStartF=0, durF=30)
    filters = [compiler._drawtext(item, 320, 240, 30, "video", 0, overlays, tmp_path)]
    props = dict(graphics.definition("callout").defaults)
    props["body"] = item.text
    graphic = project.GraphicItem(id="graphic", componentId="callout", componentVersion=1,
                                  props=props, pos={"x": 0.5, "y": 0.5}, scale=1,
                                  tlStartF=0, durF=30)
    filters.append(compiler._graphic_layer(graphic, 320, 240, 30, "video", 0, overlays, tmp_path, 0, 30))
    graph = filters[0 if kind == "text" else 1]
    assert graph.count("expansion=none") == graph.count("drawtext=")
    command = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=1"]
    command += ["-vf", graph] if kind == "text" else ["-filter_complex", graph]
    command += ["-frames:v", "1", "-f", "null", "-"]
    result = subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert not result.stderr
