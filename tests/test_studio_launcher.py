"""Single-instance launch diagnostics, bound URLs and pre-activation imports."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

import config
from pipeline import state_lock, web_security
from scripts import studio_app


@pytest.fixture(autouse=True)
def restore_runtime(monkeypatch):
    port = web_security.BOUND_PORT
    monkeypatch.setenv("CUTGRAPH_STUDIO_URL", os.environ.get("CUTGRAPH_STUDIO_URL", ""))
    try:
        yield
    finally:
        web_security.configure(port)


@pytest.mark.parametrize("url,no_browser,expected", [(True, False, 0), (True, True, 2), (False, False, 2)])
def test_windowless_second_launch_logs_and_opens_recorded_instance(monkeypatch, tmp_path, url, no_browser, expected):
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(studio_app, "_serve", lambda *args, **kwargs: pytest.fail("second launch must not bind"))
    opened = []
    monkeypatch.setattr(studio_app.webbrowser, "open", opened.append)
    monkeypatch.setattr(sys, "argv", ["studio", "--port", "8765", *(["--no-browser"] if no_browser else [])])
    with state_lock.acquire(tmp_path):
        if url:
            state_lock.record_url(tmp_path, "127.0.0.1", 9123)
        with monkeypatch.context() as streams:
            streams.setattr(sys, "stdout", None)
            streams.setattr(sys, "stderr", None)
            try:
                result = studio_app.main()
            finally:
                if sys.stdout is not None:
                    sys.stdout.close()
    assert result == expected
    assert "already running with this state home" in (tmp_path / "runtime/backend.log").read_text()
    assert opened == (["http://127.0.0.1:9123/editor/"] if url and not no_browser else [])


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_first_launch_records_the_actual_bound_port_and_cleans_url(monkeypatch, tmp_path, host):
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(studio_app.runtime_tools, "configure_process_environment", lambda: None)
    monkeypatch.setattr(sys, "argv", ["studio", "--host", host, "--port", "0", "--no-browser"])
    observed = []
    def run(server, *, sockets):
        port = sockets[0].getsockname()[1]
        origin = "http://[::1]" if host == "::1" else "http://127.0.0.1"
        observed.append(state_lock.running_url(tmp_path))
        assert observed[-1] == f"{origin}:{port}/editor/"
        assert web_security.BOUND_PORT == port
        assert os.environ["CUTGRAPH_STUDIO_URL"] == f"{origin}:{port}"
        assert server.config.workers == 1
    monkeypatch.setattr(studio_app.uvicorn.Server, "run", run)
    assert studio_app.main() == 0
    assert len(observed) == 1
    assert not (tmp_path / "runtime/studio.url").exists()


@pytest.mark.parametrize("url", ["https://example.org/editor/", "http://192.0.2.1:8765/editor/",
                                 "http://127.0.0.1:0/editor/", "http://127.0.0.1:65536/editor/",
                                 "http://127.0.0.1:8765/editor/?next=https://example.org"])
def test_running_instance_url_never_opens_non_loopback_or_invalid_records(tmp_path, url):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/studio.url").write_text(url)
    assert state_lock.running_url(tmp_path) is None


def test_launcher_imports_only_reviewed_core_dependencies_before_addon_activation(tmp_path):
    code = textwrap.dedent('''
        import sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        before = set(sys.modules)
        from pipeline import addons
        def forbidden(*args, **kwargs):
            raise AssertionError("Add-on state must not be read during launcher import")
        addons.active = forbidden
        addons.activate = forbidden
        from scripts import studio_app
        allowed = {
            "PIL", "httpx", "uvicorn", "fastapi", "starlette", "sse_starlette",
            "pydantic", "pydantic_core", "annotated_types", "annotated_doc",
            "anyio", "sniffio", "idna", "typing_extensions", "typing_inspection",
            "click", "rich", "pygments", "defusedxml", "python_multipart", "opentelemetry",
        }
        unexpected = set()
        for name, module in tuple(sys.modules.items()):
            filename = getattr(module, "__file__", None)
            if name in before or not filename:
                continue
            path = Path(filename).resolve()
            root = name.partition(".")[0]
            if root in sys.stdlib_module_names or root in allowed or path.is_relative_to(Path(sys.argv[1])):
                continue
            unexpected.add(root)
        assert not unexpected, sorted(unexpected)
        watched = {"torch", "torchaudio", "whisperx", "silero_vad", "huggingface_hub",
                   "transformers", "matplotlib", "numba", "nltk", "claude_agent_sdk"}
        assert not watched.intersection(name.partition(".")[0] for name in sys.modules)
    ''')
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(config.CODE_ROOT)],
                            env={**os.environ, "CUTGRAPH_STATE_HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_verified_update_requests_graceful_server_shutdown(tmp_path, monkeypatch):
    import time
    from pipeline import updates, web_security
    from scripts import studio_app

    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    bound = web_security.BOUND_PORT
    def run(server, *, sockets):
        assert server.config.timeout_graceful_shutdown == 10
        updates.exit_requested.set()
        deadline = time.monotonic() + 5
        while not server.should_exit and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.should_exit
    monkeypatch.setattr(studio_app.uvicorn.Server, "run", run)
    try:
        studio_app._serve(studio_app.app, host="127.0.0.1", port=0, landing="/editor/", no_browser=True)
        assert not updates.exit_requested.is_set()
    finally:
        web_security.configure(bound)
