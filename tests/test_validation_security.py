from __future__ import annotations

import ast
import asyncio
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

import config
from pipeline import library, names, runtime_tools
from studio import compute
from studio import helpers
from studio.app import app


ROOT = Path(__file__).resolve().parents[1]
DEVICES = ["CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)],
           *[f"LPT{i}" for i in range(10)], *[f"{prefix}{digit}" for prefix in ("COM", "LPT")
                                        for digit in ("¹", "²", "³")]]


@pytest.mark.parametrize("name", DEVICES)
@pytest.mark.parametrize("suffix", ["", ".mp4", ".", " ", ".mp4. "])
def test_reserved_path_names(name: str, suffix: str) -> None:
    raw = name.lower() + suffix
    assert names.reserved_name(raw)
    assert helpers._safe(raw) is None
    assert helpers._safe_asset_filename(raw) is None


@pytest.mark.parametrize("name", ["", "a" * 65, "line\n", "../video", "café"])
def test_name_bounds_and_charset(name: str) -> None:
    assert names.safe_name(name) is None


@pytest.mark.parametrize("name", ["a", "a" * 64, "Video_2-test"])
def test_valid_names(name: str) -> None:
    assert helpers._safe(name) == name


def test_case_collision_checks_directory_spelling(tmp_path: Path) -> None:
    (tmp_path / "Video.json").write_text("original", encoding="utf-8")
    with pytest.raises(names.NameConflict, match="only by case"):
        names.check_case_collision(tmp_path / "video.json")
    assert (tmp_path / "Video.json").read_text(encoding="utf-8") == "original"
    names.check_case_collision(tmp_path / "Video.json")


def test_source_and_video_case_collisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data, projects = tmp_path / "data", tmp_path / "projects"
    data.mkdir()
    projects.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PROJECTS_DIR", projects)
    (data / "Source.mp4").write_bytes(b"original")
    (projects / "Video.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *args, **kwargs: None)

    async def scenario() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return [await client.put("/upload/source.mp4", content=b"replacement"),
                    await client.post("/api/projects", json={"project": "video", "source": "Source"}),
                    await client.put("/api/project/video", json={
                        "project": {"name": "video", "tracks": []}, "baseRevision": 0,
                    })]

    responses = asyncio.run(scenario())
    assert [response.status_code for response in responses] == [409, 409, 404]
    for response in responses[:2]:
        assert response.json()["code"] == "name_conflict"
    assert responses[2].json()["code"] == "not_found"
    assert (data / "Source.mp4").read_bytes() == b"original"
    assert (projects / "Video.json").read_text(encoding="utf-8") == "{}"


def test_asset_case_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    workspace = library.create_workspace("Example")
    monkeypatch.setattr(library, "_probe_media", lambda *args: {})
    monkeypatch.setattr(library, "_make_asset_thumb", lambda *args: None)
    source = tmp_path / "source.png"
    source.write_bytes(b"original")
    original, _ = library.import_asset(workspace["id"], source, display_name="Picture.png")
    source.write_bytes(b"replacement")
    with pytest.raises(names.NameConflict, match="only by case"):
        library.import_asset(workspace["id"], source, display_name="picture.png")
    assert library.list_assets(workspace["id"]) == [original]
    assert source.read_bytes() == b"replacement"

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post(f"/api/workspaces/{workspace['id']}/assets",
                                     files={"files": ("picture.png", b"replacement", "image/png")})

    response = asyncio.run(scenario())
    assert response.status_code == 409
    assert response.json()["code"] == "name_conflict"
    assert library.list_assets(workspace["id"]) == [original]
    assert not list(config.WORKSPACES_DIR.glob(".upload-*"))


@pytest.mark.parametrize("module", ["project", "proposals", "graphics"])
def test_validation_survives_optimization(module: str, tmp_path: Path) -> None:
    statements = {
        "project": "project.validate(project.Project(name='video', fps=0))",
        "proposals": "proposals._nonempty('', 'title')",
        "graphics": "graphics.validate_props('callout', {})",
    }
    script = (f"from pipeline import {module}\nfrom pipeline.errors import ValidationError\n"
              f"try:\n    {statements[module]}\nexcept ValidationError:\n    print('validation active')\n"
              "else:\n    raise SystemExit('validation vanished')")
    result = subprocess.run([sys.executable, "-O", "-c", script], cwd=ROOT,
                            env=dict(os.environ, CUTGRAPH_STATE_HOME=str(tmp_path)),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "validation active"


@pytest.mark.parametrize("module", ["project", "proposals", "notes", "jobs", "library"])
def test_path_helpers_share_reserved_name_rule(module: str) -> None:
    from pipeline import jobs, notes, project, proposals

    with pytest.raises(ValueError):
        if module == "project":
            project.validate(project.Project(name="CON"))
        elif module == "proposals":
            proposals._path("CON", "proposal")
        elif module == "notes":
            notes._path("NUL")
        elif module == "jobs":
            jobs._path("COM0")
        else:
            library._workspace_dir("lpt9")


def test_workspace_case_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    library.create_workspace("Example")

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post("/api/workspaces", json={"name": "example"})

    response = asyncio.run(scenario())
    assert response.status_code == 409
    assert "case" in response.json()["error"].lower()
    assert len(list(config.WORKSPACES_DIR.iterdir())) == 1


@pytest.mark.parametrize("module", ["project", "proposals", "graphics"])
def test_validation_has_no_asserts(module: str) -> None:
    tree = ast.parse((ROOT / "pipeline" / f"{module}.py").read_text(encoding="utf-8"))
    assert not [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]


@pytest.mark.parametrize("args", [
    ["scripts/studio_app.py", "--host", "0.0.0.0", "--no-browser"],
    ["-c", "import studio.app"],
])
def test_optimized_studio_refused(args: list[str], tmp_path: Path) -> None:
    env = dict(os.environ, CUTGRAPH_STATE_HOME=str(tmp_path))
    result = subprocess.run([sys.executable, "-O", *args], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Studio requires Python without optimization" in output
    assert "Traceback" not in output


def test_errors_are_public_and_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    private = f"internal failure at {tmp_path / 'private.json'}"

    def broken(_project: str) -> tuple[dict | None, int]:
        raise RuntimeError(private)

    async def scenario() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
            responses = [await client.get("/api/project/missing"),
                         await client.post("/api/projects", content=b"{"),
                         await client.put("/api/project/video", json={
                             "project": {"name": "video", "fps": 0, "tracks": []}, "baseRevision": 0,
                         })]
            monkeypatch.setattr(helpers, "_read_project_doc", broken)
            responses.append(await client.get("/api/project/video"))
            return responses

    responses = asyncio.run(scenario())
    assert [r.status_code for r in responses] == [404, 400, 400, 500]
    for response in responses:
        body = response.json()
        assert isinstance(body["code"], str) and body["code"]
        assert isinstance(body["error"], str) and body["error"]
        assert str(tmp_path) not in response.text
        assert not re.search(r"[A-Za-z]:[\\/]", response.text)
        assert not any(word in response.text for word in ("Traceback", "RuntimeError", "ValidationError", "AssertionError"))
    assert private in caplog.text


@pytest.mark.parametrize("message", ["drive C:/private", r"network \\host\private", "file /private/data",
                                    "RuntimeError: private", "Traceback details"])
def test_authored_error_messages_cannot_echo_private_paths(message: str,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline.errors import ValidationError

    def invalid(_name: str) -> dict:
        raise ValidationError(message)

    monkeypatch.setattr(library, "create_workspace", invalid)

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.post("/api/workspaces", json={"name": "Example"})

    response = asyncio.run(scenario())
    assert response.status_code == 400
    assert response.json() == {"code": "invalid_request", "error": "The request is invalid."}


def test_internal_json_errors_hide_plain_diagnostic_text(caplog: pytest.LogCaptureFixture) -> None:
    from starlette.responses import JSONResponse
    from starlette.types import Receive, Scope, Send

    from studio.errors import ErrorResponses

    async def failing(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"error": "private engine diagnostic", "details": "private context"}, status_code=500)
        await response(scope, receive, send)

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ErrorResponses(failing)),
                                     base_url="http://studio.test") as client:
            return await client.get("/")

    response = asyncio.run(scenario())
    assert response.json() == {"error": "Studio could not complete the request.", "code": "internal_error"}
    assert "private engine diagnostic" in caplog.text
    assert "private context" in caplog.text


@pytest.mark.parametrize("tool", ["ffmpeg", "ffprobe"])
def test_invalid_media_tool_prevents_cli_start(tool: str, tmp_path: Path) -> None:
    override = f"CUTGRAPH_{tool.upper()}"
    result = subprocess.run([sys.executable, "scripts/studio_app.py", "--no-browser"], cwd=ROOT,
                            env=dict(os.environ, CUTGRAPH_STATE_HOME=str(tmp_path), **{override: "relative.exe"}),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert override in result.stdout + result.stderr
    assert "absolute executable path" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize("tool", ["ffmpeg", "ffprobe"])
@pytest.mark.parametrize("invalid", ["relative.exe", "missing.exe"])
def test_invalid_tool_override_fails_closed(tool: str, invalid: str, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    override = f"CUTGRAPH_{tool.upper()}"
    path = invalid if invalid == "relative.exe" else str(tmp_path / invalid)
    (tmp_path / "relative.exe").write_bytes(b"fixture")
    with pytest.raises(RuntimeError, match=override):
        runtime_tools.resolve_tool(tool, environ={override: path})


def test_tools_are_resolved_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    paths = {name: tmp_path / f"{name}.exe" for name in ("ffmpeg", "ffprobe")}
    for path in paths.values():
        path.write_bytes(b"fixture")

    def resolve(name: str, **kwargs: object) -> Path:
        calls.append(name)
        return paths[name]

    monkeypatch.setattr(runtime_tools, "_TOOLS", {})
    monkeypatch.setattr(runtime_tools, "resolve_tool", resolve)
    runtime_tools.initialize_tools()
    for _ in range(3):
        for name, path in paths.items():
            assert runtime_tools.tool_path(name) == str(path)
            assert Path(runtime_tools.tool_path(name)).is_absolute()
    assert calls == ["ffmpeg", "ffprobe"]


@pytest.mark.parametrize("result", [None, Path("relative.exe")])
def test_missing_or_relative_resolved_tool_prevents_startup(result: Path | None,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_tools, "_TOOLS", {})
    monkeypatch.setattr(runtime_tools, "resolve_tool", lambda *args, **kwargs: result)
    with pytest.raises(RuntimeError, match="CUTGRAPH_FFMPEG"):
        runtime_tools.initialize_tools()
    assert runtime_tools._TOOLS == {}


def test_media_commands_never_use_bare_tool_names() -> None:
    failures = []
    for directory in ("pipeline", "studio", "scripts"):
        for path in (ROOT / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                    first = node.elts[0]
                    if isinstance(first, ast.Constant) and first.value in ("ffmpeg", "ffprobe"):
                        failures.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not failures
    for path in (ROOT / "motion-core" / "bin").rglob("*.ts"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"spawn\(\s*['\"](?:ffmpeg|ffprobe)['\"]", source)
        assert not re.search(r"(?:\?\?|=)\s*['\"](?:ffmpeg|ffprobe)['\"]", source)


def test_full_resolved_command_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "run.log"
    monkeypatch.setattr(config, "RUN_LOG", log)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(helpers, "THUMBS_DIR", tmp_path / "thumbs")
    (tmp_path / "input.mp4").write_bytes(b"source")
    commands: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: commands.append(command))
    helpers._make_thumb("input")
    assert len(commands) == 1
    assert commands[0][0] == runtime_tools.tool_path("ffmpeg")
    assert " ".join(commands[0]) in log.read_text(encoding="utf-8")


def test_existing_names_have_separate_bounds() -> None:
    assert helpers._safe("v" * 80) == "v" * 80
    assert helpers._safe("v" * 200) == "v" * 200
    assert helpers._safe("v" * 201) is None
    assert names.safe_name("v" * 65) is None
    assert len(Path(names.filename("v" * 240 + ".mp4", helpers.VIDEO_EXTS) or "").stem) == 64


def test_source_namespace_spans_extensions(tmp_path: Path) -> None:
    (tmp_path / "Source.mp4").touch()
    with pytest.raises(names.NameConflict):
        names.check_case_collision(tmp_path / "source.mov", source_stem=True)


def test_device_names_are_valid_document_ids() -> None:
    from pipeline import project

    project._validate_id("CON", "clip id")


def test_library_display_device_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    ws = library.create_workspace("Example")
    monkeypatch.setattr(library, "_probe_media", lambda *args: {})
    monkeypatch.setattr(library, "_make_asset_thumb", lambda *args: None)
    source = tmp_path / "picture.png"
    source.write_bytes(b"picture")
    asset, _ = library.import_asset(ws["id"], source, display_name="CON.png")
    assert asset["name"] == "CON.png"


def test_job_reads_do_not_scan_siblings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import jobs

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    (tmp_path / "job.json").write_text('{}', encoding="utf-8")

    def forbidden(_path: Path):
        raise RuntimeError("directory scan on read")

    monkeypatch.setattr(Path, "iterdir", forbidden)
    assert jobs._path("job").name == "job.json"


@pytest.mark.parametrize("scope,project,channel", [
    ("project", "../..", None), ("channel", "video", "../.."),
])
def test_decision_reads_validate_identifiers(scope: str, project: str, channel: str | None) -> None:
    from pipeline import decisions

    with pytest.raises(ValueError):
        decisions.list_for(project, scope=scope, channel=channel)


def test_script_title_case_collision_gets_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.routes.script_video import _script_video_slug

    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    (tmp_path / "Video.json").write_text('{}', encoding="utf-8")
    assert _script_video_slug("video") == "video_2"


@pytest.mark.parametrize("message", ["diagnostic", "location=/private/data", "location:'/private/data'", "root=\\private", "C:/private/data", "RuntimeError occurred"])
def test_untrusted_error_text_is_generic(message: str) -> None:
    from studio.errors import error_body, public_error
    from pipeline.errors import ValidationError

    assert public_error(RuntimeError(message), 400) == "The request is invalid."
    assert error_body({"error": message}, 400)["error"] == "The request is invalid."
    if message != "diagnostic":
        assert public_error(ValidationError(message), 400) == "The request is invalid."


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_every_server_error_is_generic(status: int) -> None:
    from studio.errors import error_body, public_error
    from pipeline.errors import ValidationError

    assert error_body({"error": "private diagnostic"}, status)["error"] == public_error(ValidationError("private diagnostic"), status)


def test_error_fields_are_allowlisted(caplog: pytest.LogCaptureFixture) -> None:
    from studio.errors import error_body

    body = error_body({"debug": "private context", "currentRevision": 3, "ops": []}, 409)
    assert "debug" not in body
    assert body["currentRevision"] == 3 and body["ops"] == []
    assert "private context" in caplog.text


def test_malformed_json_error_body() -> None:
    from starlette.responses import Response
    from studio.errors import ErrorResponses

    async def failing(scope, receive, send):
        await Response(b"not json", status_code=502, media_type="application/json")(scope, receive, send)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ErrorResponses(failing)), base_url="http://studio.test") as client:
            return await client.get("/")

    assert asyncio.run(scenario()).json()["code"] == "upstream_error"


def test_upload_publish_without_hardlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.routes import uploads

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *args: None)

    def unsupported(*args):
        raise OSError("hard links unavailable")

    monkeypatch.setattr(os, "link", unsupported)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            first = await client.put("/upload/video.mp4", content=b"original")
            second = await client.put("/upload/video.mp4", content=b"replacement")
            return first, second

    first, second = asyncio.run(scenario())
    assert first.status_code == 200 and second.status_code == 409
    assert (tmp_path / "video.mp4").read_bytes() == b"original"
    assert not list(tmp_path.glob("*.part"))
    assert uploads._safe_filename("video.mp4") == "video.mp4"


def test_cli_configures_third_party_tools() -> None:
    source = (ROOT / "scripts/studio_app.py").read_text(encoding="utf-8")
    assert "runtime_tools.configure_process_environment()" in source


@pytest.mark.parametrize("length", [61, 62, 63, 64, 80, 200])
def test_existing_video_and_generated_proxy_open(length: int, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import project

    slug = "v" * length
    data, proxies, projects = (tmp_path / name for name in ("source", "proxies", "projects"))
    for directory in (data, proxies, projects):
        directory.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PROXIES_DIR", proxies)
    monkeypatch.setattr(config, "PROJECTS_DIR", projects)
    project.write_project(projects / f"{slug}.json", project.Project(name=slug))
    (data / f"{slug}.mp4").write_bytes(b"source")
    (proxies / f"{slug}_540.mp4").write_bytes(b"proxy")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return [await client.get(f"/api/project/{slug}"),
                    await client.get(f"/media/source-raw/data/source/{slug}.mp4"),
                    await client.get(f"/media/proxy/{slug}_540.mp4")]

    responses = asyncio.run(scenario())
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert responses[1].content == b"source" and responses[2].content == b"proxy"


def test_read_case_mismatches_are_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import jobs, project

    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(jobs, "_registry", {})
    project.write_project(tmp_path / "Video.json", project.Project(name="Video"))
    (tmp_path / "Source.mp4").write_bytes(b"source")
    (tmp_path / "Job.json").write_text('{"id":"Job"}', encoding="utf-8")
    assert jobs.get("job") is None

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return [await client.get("/api/project/video"),
                    await client.get("/media/source-raw/data/source/source.mp4"),
                    await client.get("/media/source/source")]

    assert [response.status_code for response in asyncio.run(scenario())] == [404, 404, 404]


def test_job_scan_does_not_list_per_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import jobs

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(jobs, "_registry", {})
    for index in range(2000):
        (tmp_path / f"job{index}.json").write_text('{}', encoding="utf-8")

    def forbidden(_path: Path):
        raise RuntimeError("directory scan on lookup")

    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(os, "listdir", forbidden)
    assert len(jobs.all_persisted()) == 2000


def test_job_creation_checks_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import jobs

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(jobs, "new_id", lambda: "job")
    (tmp_path / "Job.json").write_text('{"id":"Job"}', encoding="utf-8")
    with pytest.raises(names.NameConflict):
        jobs.new_job("transcribe", "video", {})


def test_decision_reads_enforce_resolved_containment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import decisions

    root = tmp_path / "decisions"
    selected = root / "channels" / "channel"
    monkeypatch.setattr(config, "DECISIONS_DIR", root)
    original = Path.resolve

    def resolve(path: Path, *args, **kwargs):
        return tmp_path / "outside" if path == selected else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="storage directory"):
        decisions.list_for("video", scope="channel", channel="channel")


@pytest.mark.parametrize("code,status", [("not_configured", 400), ("unauthorized", 401)])
def test_stock_errors_remain_accurate(code: str, status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import stock

    error = stock.StockError(code, status)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(stock, "search", fail)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return await client.get("/api/stock/search", params={"q": "landscape", "kind": "video"})

    response = asyncio.run(scenario())
    assert response.status_code == status
    assert response.json()["error"] == str(error)
    assert response.json()["code"] == code


def test_domain_error_prose_is_not_a_drive() -> None:
    from pipeline.errors import ValidationError
    from studio.errors import public_error

    message = "client's: choose a video"
    assert public_error(ValidationError(message), 400) == message


def test_failed_job_and_take_responses_hide_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    from pipeline import jobs
    from studio import state
    from studio.routes.script_video import _take_status

    private = str(tmp_path / "private.json")
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(jobs, "_registry", {})
    monkeypatch.setattr(state, "_JOBS", {"video": {"status": "error", "error": private}})
    jid = jobs.new_job("transcribe", "video", {"sourceSlug": "take"})
    jobs.set(jid, status="error", error=private)
    assert private not in str(_take_status("video", "take", {}))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            return [await client.get("/api/jobs"), await client.get(f"/api/jobs/{jid}")]

    for response in asyncio.run(scenario()):
        assert response.status_code == 200 and private not in response.text
    assert private in caplog.text


def test_chat_errors_are_sanitized_before_publish_and_replay(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    from studio.routes import chat

    monkeypatch.setattr(config, "CHAT_DIR", tmp_path)
    event = {"type": "agent_error", "kind": "runtime", "message": str(tmp_path / "private.json")}
    emitted: list[dict] = []
    monkeypatch.setattr(chat.state.hub, "publish_event", lambda slug, message: emitted.append(message))
    chat._make_tool_ctx("video").emit(event)
    assert event["message"] not in str(emitted)
    assert event["message"] not in (tmp_path / "video.jsonl").read_text(encoding="utf-8")
    chat._append_chat_log("video", event)
    assert event["message"] not in str(chat._read_chat_log("video"))
    assert event["message"] in caplog.text


def test_third_party_tool_discovery_uses_startup_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    suffix = ".exe" if os.name == "nt" else ""
    tools = {name: tmp_path / f"{name}{suffix}" for name in ("ffmpeg", "ffprobe")}
    for path in tools.values():
        path.write_bytes(b"tool")
        path.chmod(0o755)
    monkeypatch.setattr(runtime_tools, "_TOOLS", tools)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "previous")
    runtime_tools.configure_process_environment()
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path)
    assert Path(shutil.which("ffmpeg") or "").resolve() == tools["ffmpeg"]
    assert os.environ["IMAGEIO_FFMPEG_EXE"] == str(tools["ffmpeg"])


@pytest.mark.parametrize("platform", [
    pytest.param("nt", marks=pytest.mark.skipif(os.name != "nt", reason="relies on Windows rename refusing to overwrite")),
    "posix",
])
def test_upload_fallback_preserves_competing_file(platform: str, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from studio.routes import uploads

    source, dest = tmp_path / "upload.part", tmp_path / "video.mp4"
    source.write_bytes(b"original")

    def unsupported(*args):
        raise OSError("hard links unavailable")

    monkeypatch.setattr(uploads, "os", SimpleNamespace(name=platform, link=unsupported, rename=os.rename))
    uploads._publish_upload(source, dest)
    source.write_bytes(b"replacement")
    with pytest.raises(FileExistsError):
        uploads._publish_upload(source, dest)
    assert dest.read_bytes() == b"original"
    assert source.read_bytes() == b"replacement"


def test_inserted_element_and_cast_ids_allow_device_words() -> None:
    from pipeline import cast, story_insert

    assert story_insert._slug("CON.png") == "CON"
    assert cast._slug("CON") == "con"


@pytest.mark.parametrize("message", ["NameConflict occurred", "SystemExit occurred"])
def test_authored_messages_hide_exception_names(message: str) -> None:
    from pipeline.errors import ValidationError
    from studio.errors import public_error

    assert public_error(ValidationError(message), 400) == "The request is invalid."


def test_workspace_existing_name_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path)
    slug = "W" * 200
    directory = tmp_path / slug
    directory.mkdir()
    (directory / "workspace.json").write_text('{"id":"' + slug + '"}', encoding="utf-8")
    assert library.get_workspace(slug) == {"id": slug}
    with pytest.raises(names.NameNotFound):
        library.get_workspace(slug.lower())


def test_process_tool_configuration_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_tools, "_TOOLS", {name: tmp_path / f"{name}.exe" for name in ("ffmpeg", "ffprobe")})
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "previous")
    runtime_tools.configure_process_environment()
    first = os.environ["PATH"]
    runtime_tools.configure_process_environment()
    assert os.environ["PATH"] == first


def test_exact_path_checks_parent_spelling(tmp_path: Path) -> None:
    directory = tmp_path / "Video"
    directory.mkdir()
    (directory / "asset.png").touch()
    with pytest.raises(names.NameNotFound):
        names.exact_path(tmp_path / "video" / "asset.png")


def test_peaks_case_mismatch_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path)
    (tmp_path / "Video.peaks.json").write_text("[]", encoding="utf-8")
    assert asyncio.run(_request("GET", "/api/peaks/video")).status_code == 404


async def _request(method: str, url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://studio.test") as client:
        return await client.request(method, url, **kwargs)


def _link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_linked_projects_keep_exact_spelling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import project

    target = tmp_path / "relocated"
    target.mkdir()
    link = tmp_path / "projects"
    _link_directory(link, target)
    monkeypatch.setattr(config, "PROJECTS_DIR", link)
    project.write_project(target / "Video.json", project.Project(name="Video", tracks=[]))
    assert names.exact_path(link / "Video.json", link) == link / "Video.json"
    assert asyncio.run(_request("GET", "/api/project/Video")).status_code == 200
    assert asyncio.run(_request("GET", "/api/project/video")).status_code == 404


def test_exact_path_rejects_link_escape(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    outside = tmp_path / "outside"
    trusted.mkdir()
    outside.mkdir()
    (outside / "asset.png").touch()
    _link_directory(trusted / "nested", outside)
    with pytest.raises(names.NameNotFound):
        names.exact_path(trusted / "nested" / "asset.png", trusted)


def test_exact_path_rejects_a_linked_entry_escaping_the_root(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    outside = tmp_path / "outside"
    trusted.mkdir()
    outside.mkdir()
    _link_directory(trusted / "entry", outside)
    with pytest.raises(names.NameNotFound):
        names.exact_path(trusted / "entry", trusted)


def test_exact_path_accepts_a_device_prefixed_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "video.json").touch()
    realpath = os.path.realpath

    def prefixed(path, **kwargs):
        return ("\\\\?\\" if Path(path) == tmp_path else "") + realpath(path, **kwargs)

    monkeypatch.setattr(os.path, "realpath", prefixed)
    assert names.exact_path(tmp_path / "video.json", tmp_path) == tmp_path / "video.json"


def test_exact_path_leaves_a_concurrent_replace_alone(tmp_path: Path) -> None:
    target = tmp_path / "video.json"
    target.write_text("0", encoding="utf-8")
    errors: list[OSError] = []
    done = threading.Event()

    def replace() -> None:
        try:
            for i in range(300):
                staged = tmp_path / "video.json.tmp"
                staged.write_text(str(i), encoding="utf-8")
                os.replace(staged, target)
        except OSError as error:
            errors.append(error)
        finally:
            done.set()

    writer = threading.Thread(target=replace)
    writer.start()
    while not done.is_set():
        names.exact_path(target, tmp_path)
    writer.join()
    assert errors == []
    assert target.read_text(encoding="utf-8") == "299"


def test_linked_source_folder_keeps_exact_spelling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "relocated"
    target.mkdir()
    link = tmp_path / "source"
    _link_directory(link, target)
    monkeypatch.setattr(config, "DATA_DIR", link)
    (target / "Video.MP4").write_bytes(b"source")
    assert helpers._source("Video") == link / "Video.MP4"
    response = asyncio.run(_request("GET", "/media/source-raw/data/source/Video.MP4"))
    assert response.status_code == 200 and response.content == b"source"
    assert asyncio.run(_request("GET", "/media/source-raw/data/source/video.MP4")).status_code == 404


@pytest.mark.parametrize("source", ["CON_script.mp4", "NUL_take1.mp4"])
def test_literal_generated_looking_upload_is_readable(source: str, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *args: None)
    uploaded = asyncio.run(_request("PUT", f"/upload/{source}", content=b"source"))
    assert uploaded.status_code == 200
    response = asyncio.run(_request("GET", f"/media/source-raw/data/source/{source}"))
    assert response.status_code == 200 and response.content == b"source"
    assert helpers._source(Path(source).stem) == tmp_path / source


def test_legacy_uppercase_source_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    source = tmp_path / "Video.MP4"
    source.write_bytes(b"source")
    assert helpers._source("Video") == source
    assert asyncio.run(_request("GET", "/media/source/Video")).status_code == 307
    assert asyncio.run(_request("GET", "/media/source/video")).status_code == 404


@pytest.mark.parametrize("filename,status,rendered", [("Video", "error", 0), ("video", "done", 1)])
def test_scene_job_checks_transcript_spelling(filename: str, status: str, rendered: int, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import jobs, project, scene_render

    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(jobs, "_registry", {})
    monkeypatch.setattr(compute, "_emit_job_update_threadsafe", lambda *args: None)
    monkeypatch.setattr(compute, "_gpu_run", lambda fn, *args: fn(*args))
    monkeypatch.setattr(scene_render, "motion_core_version", lambda: "version")
    renders = []

    def render(request: dict, dest: Path) -> dict:
        renders.append(request)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.touch()
        return {}

    monkeypatch.setattr(scene_render, "render_scene", render)
    transcript_dir = tmp_path / "outputs" / "transcripts"
    transcript_dir.mkdir(parents=True)
    (transcript_dir / f"{filename}.words.json").write_text(
        '{"segments": [{"words": [{"word": "Hello", "start": 0, "end": 1}]}]}', encoding="utf-8")
    project.write_project(config.PROJECTS_DIR / "video.json", project.Project(name="video", tracks=[
        project.Track(id="speech", kind="speech", clips=[project.Clip(id="clip", media=project.Media(
            source="data/source/video.mp4"), srcInF=0, srcOutF=30, tlStartF=0)]),
    ], scenes=[project.Scene(id="scene", title="Intro", role="Hook", startWord=0,
                             shot={"mode": "animation", "pose": "cheer"})]))
    jid = jobs.new_job("render_scene", "video", {})
    job = jobs.get(jid)
    assert job is not None
    compute._run_detached_job(jid, job)
    result = jobs.get(jid)
    assert result is not None and result["status"] == status
    assert len(renders) == rendered


@pytest.mark.parametrize("layer", ["text", "graphics", "captions", "all"])
def test_derived_legacy_export_builds_plan(layer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import compile as compiler

    name = "v" * 200 + "__9x16"
    source = tmp_path / "data" / "source" / "video.mp4"
    source.parent.mkdir(parents=True)
    source.touch()
    transcripts = tmp_path / "outputs" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "video.words.json").write_text(
        '{"segments": [{"words": [{"word": "Hello", "start": 0, "end": 1}]}]}', encoding="utf-8")
    monkeypatch.setattr(compiler, "_probe_transfer", lambda *args: "")
    doc = {"name": name, "fps": 30, "width": 1080, "height": 1920, "tracks": [{
        "id": "speech", "kind": "speech", "clips": [{"id": "clip", "media": {
            "source": "data/source/video.mp4"}, "srcInF": 0, "srcOutF": 30, "tlStartF": 0}],
    }]}
    items = []
    if layer in {"text", "all"}:
        items.append({"id": "text", "text": "Hello", "preset": "hook", "pos": {"x": 0.5, "y": 0.2},
                      "tlStartF": 0, "durF": 30})
    if layer in {"graphics", "all"}:
        items.append({"id": "graphic", "type": "graphic", "componentId": "lower-third", "componentVersion": 1,
                      "props": {"eyebrow": "", "title": "Hello", "detail": ""},
                      "pos": {"x": 0.5, "y": 0.8}, "scale": 1, "tlStartF": 0, "durF": 30})
    if items:
        for item in items:
            doc["tracks"].append({"id": f"track_{item['id']}", "kind": "text", "items": [item]})
    if layer in {"captions", "all"}:
        doc["captions"] = {"enabled": True, "style": {"look": "outline"}}
    plan = compiler.build_plan(doc, project_dir=tmp_path)
    if layer in {"text", "all"}:
        assert (tmp_path / "outputs" / "overlays" / f"{name}_text0.txt").read_text(encoding="utf-8") == "Hello"
    if layer in {"graphics", "all"}:
        assert list((tmp_path / "outputs" / "overlays").glob(f"{name}_graphic*.txt"))
    if layer in {"captions", "all"}:
        assert "ass=" in plan.filter_complex
        assert (tmp_path / "outputs" / "captions" / f"{name}.ass").is_file()


def test_rejected_source_does_not_fall_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "other")
    directory = tmp_path / "data" / "source"
    directory.mkdir(parents=True)
    (directory / "bad.name.mp4").write_bytes(b"private")
    response = asyncio.run(_request("GET", "/media/source-raw/data/source/bad.name.mp4"))
    assert response.status_code == 404


@pytest.mark.parametrize("suffix", ["__9x16", "__3840x2160"])
def test_generated_render_creation(suffix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    slug = "v" * 64 + suffix
    response = asyncio.run(_request("PUT", f"/api/project/{slug}", json={
        "project": {"name": slug, "tracks": []}, "baseRevision": 0,
    }))
    assert response.status_code == 200


def test_generated_take_compute_and_media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import project

    slug = "v" * 200
    take = slug + "_take1"
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    project.write_project(tmp_path / f"{slug}.json", project.Project(name=slug, tracks=[
        project.Track(id="speech", kind="speech", clips=[project.Clip(id="clip", media=project.Media(
            source=f"data/source/{take}.mp4"), srcInF=0, srcOutF=30, tlStartF=0)]),
    ]))
    (tmp_path / f"{take}.mp4").write_bytes(b"take")
    assert compute._project_compute_source_slug(slug) == take
    assert compute._job_source_slug({"project": slug, "meta": {"sourceSlug": take}}) == take
    assert asyncio.run(_request("GET", f"/media/source-raw/data/source/{take}.mp4")).status_code == 200


def test_device_words_in_display_titles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.routes.script_video import _script_video_slug

    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    assert library.create_workspace("CON")["name"] == "CON"
    assert names.safe_name(_script_video_slug("CON"))
    assert names.filename("Con. Ed. interview.mp4", helpers.VIDEO_EXTS) == "Con_Ed_interview.mp4"


def test_same_stem_upload_conflict_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "Video.mp4").touch()
    response = asyncio.run(_request("PUT", "/upload/Video.mov", content=b"replacement"))
    assert response.status_code == 409
    assert "already exists" in response.json()["error"]
    assert "case" not in response.json()["error"]


@pytest.mark.parametrize("message", ["/secret", "field=/secret", "field:'/secret'", "\\secret"])
def test_single_component_absolute_paths_are_private(message: str) -> None:
    from pipeline.errors import ValidationError
    from studio.errors import public_error

    assert public_error(ValidationError(message), 400) == "The request is invalid."


def test_speech_path_validation_does_not_echo_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    response = asyncio.run(_request("PUT", "/api/project/video", json={
        "baseRevision": 0, "project": {"name": "video", "tracks": [{"id": "speech", "kind": "speech",
            "clips": [{"id": "clip", "media": {"source": "/secret"}, "srcInF": 0, "srcOutF": 30, "tlStartF": 0}]}]},
    }))
    assert response.status_code == 400
    assert "/secret" not in response.text
    assert "media.source" in response.json()["error"]


@pytest.mark.parametrize("method,body", [("PUT", {"project": {"name": "video", "tracks": []}}),
                                      ("PATCH", {"ops": []})])
def test_new_long_video_is_bad_request(method: str, body: dict, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    response = asyncio.run(_request(method, "/api/project/" + "v" * 65, json={**body, "baseRevision": 0}))
    assert response.status_code == 400


def test_project_name_conflict_has_distinct_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)

    def conflict(*args):
        raise names.NameConflict("name differs from an existing name only by case")

    monkeypatch.setattr(names, "check_case_collision", conflict)
    response = asyncio.run(_request("PUT", "/api/project/video", json={
        "baseRevision": 0, "project": {"name": "video", "tracks": []},
    }))
    assert response.status_code == 409 and response.json()["code"] == "name_conflict"


@pytest.mark.parametrize("inline", [False, True])
def test_proxy_responses_do_not_publish_paths(inline: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    from pipeline import contracts, proxy
    from studio.routes.jobs import _run_inline_verb

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "video.mp4").touch()
    monkeypatch.setattr(proxy, "make_proxy", lambda *args: contracts.StageResult(
        "ok", "proxy", "video", output_path=str(tmp_path / "video_540.mp4")))
    if inline:
        inline_response = asyncio.run(_run_inline_verb("make_proxy", "video", {}))
        assert inline_response.status_code == 200
        body = json.loads(bytes(inline_response.body))
    else:
        response = asyncio.run(_request("POST", "/api/proxy/video"))
        assert response.status_code == 200
        body = response.json()
    assert "output_path" not in body
    assert body.get("output", "").startswith("/media/proxy/")


def test_upload_lock_does_not_block_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time
    from studio import state

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *args: None)
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with state._PROJECT_CREATE_LOCK:
            acquired.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(1)

    async def scenario():
        task = asyncio.create_task(_request("PUT", "/upload/video.mp4", content=b"source"))
        started = time.monotonic()
        try:
            await asyncio.sleep(0.05)
            response = await _request("GET", "/api/version")
            assert response.status_code == 200
            assert time.monotonic() - started < 1
        finally:
            release.set()
            await task

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        thread.join()


def test_transcription_reads_pcm_without_whisperx_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import wave
    from types import SimpleNamespace
    import numpy as np
    from pipeline import transcribe

    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path)
    source = tmp_path / "video.mp4"
    source.touch()

    def extract(_source, dest):
        with wave.open(str(dest), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(np.array([-32768, 0, 32767], dtype="<i2").tobytes())

    def words(audio, *args):
        assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
        assert audio.tolist() == pytest.approx([-1, 0, 32767 / 32768])
        return [{"start": 0, "end": 3 / 16000, "text": "word", "words": [
            {"start": 0, "end": 3 / 16000, "word": "word"},
        ]}]

    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace(load_audio=lambda *args: pytest.fail("loader called")))
    monkeypatch.setattr(transcribe, "extract_audio", extract)
    monkeypatch.setattr(transcribe, "_inference_settings", lambda: ("cpu", "int8", 4))
    monkeypatch.setattr(transcribe, "_whisperx_words", words)
    assert transcribe.transcribe(source).status == "ok"


def test_job_path_resolves_candidate_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import jobs

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    path = tmp_path / "job.json"
    path.touch()
    original = Path.resolve
    calls = []

    def resolve(selected: Path, *args, **kwargs):
        if selected == path:
            calls.append(selected)
        return original(selected, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert jobs._path("job") == path
    assert len(calls) == 1


def test_project_asset_checks_project_spelling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PROJECT_MEDIA_DIR", tmp_path)
    directory = tmp_path / "Video"
    directory.mkdir()
    (directory / "asset.png").write_bytes(b"private")
    assert asyncio.run(_request("GET", "/media/project-asset/video/asset.png")).status_code == 404


@pytest.mark.parametrize("raw", ["data/source/CON.mp4", "NUL/video.mp4", "data/source/nested/video.mp4"])
def test_source_read_validates_every_component(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helpers, "_existing_under", lambda *args: pytest.fail("invalid name reached filesystem"))
    assert asyncio.run(_request("GET", f"/media/source-raw/{raw}")).status_code == 404


def test_render_media_checks_parent_spelling(tmp_path: Path) -> None:
    from pipeline import project

    directory = tmp_path / "data" / "Source"
    directory.mkdir(parents=True)
    (directory / "video.mp4").touch()
    with pytest.raises(names.NameNotFound):
        project.resolve_media(tmp_path, project.Media(source="data/source/video.mp4"), prefer_proxy=False)


def test_edit_word_lookup_checks_spelling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.routes import verbs

    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path)
    (tmp_path / "Video.words.json").write_text('{"segments": []}', encoding="utf-8")
    with pytest.raises(names.NameNotFound):
        verbs._flat_words("video")


def test_legacy_project_narration_record_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import project, script_video, transcribe
    from studio.routes import script_video as routes

    slug = "v" * 200
    take = f"{slug}_take1"
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    for key, rel in (("DATA_DIR", "data/source"), ("PROJECTS_DIR", "projects"),
                     ("TRANSCRIPTS_DIR", "transcripts"), ("PROXIES_DIR", "proxies")):
        directory = tmp_path / rel
        directory.mkdir(parents=True)
        monkeypatch.setattr(config, key, directory)
    scenes = script_video.parse_script("Hello world.")
    transcript = script_video.script_transcript(scenes)
    script_slug = slug + "_script"
    doc = script_video.build_project(slug, scenes, f"data/source/{script_slug}.mp4", 60, None)
    project.write_project(config.PROJECTS_DIR / f"{slug}.json", doc)
    transcribe._write_outputs(transcript, script_slug)
    monkeypatch.setattr(script_video, "take_has_picture", lambda *args: True)
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *args, **kwargs: None)
    queued = []
    monkeypatch.setattr(compute, "_enqueue_transcribe", lambda source, **kwargs: queued.append(source) or {"status": "queued"})
    response = asyncio.run(_request("PUT", f"/api/script-video/{slug}/narration/take.mp4", content=b"take"))
    assert response.status_code == 201 and response.json()["take"] == take
    assert queued == [take]
    assert routes._script_takes(slug) == [take]
    transcribe._write_outputs(transcript, take)
    monkeypatch.setattr(helpers, "_source_duration_frames", lambda *args: 60)
    response = asyncio.run(_request("POST", f"/api/script-video/{slug}/narration/{take}/apply", json={"baseRevision": doc.revision}))
    assert response.status_code == 200
    assert response.json()["project"]["narration"] == "recorded"


def test_rootless_lookups_use_the_deepest_state_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import config
    state = tmp_path / "state"
    chat = state / "outputs" / "chat"
    chat.mkdir(parents=True)
    (chat / "video.jsonl").write_text("")
    monkeypatch.setattr(config, "STATE_HOME", state)
    monkeypatch.setattr(config, "CHAT_DIR", chat)
    listed: list[Path] = []
    real = os.listdir

    def listing(folder: os.PathLike[str] | str) -> list[str]:
        listed.append(Path(folder))
        return real(folder)

    monkeypatch.setattr(names.os, "listdir", listing)
    assert names.exact_path(chat / "video.jsonl") == chat / "video.jsonl"
    assert listed == [Path(os.path.normpath(chat.absolute()))]


def test_an_unlistable_parent_does_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "inner").mkdir(parents=True)

    def refuse(folder: os.PathLike[str] | str) -> list[str]:
        raise PermissionError("listing refused")

    monkeypatch.setattr(names.os, "listdir", refuse)
    assert names.exact_path(root / "inner" / "file.json", root) == root / "inner" / "file.json"
