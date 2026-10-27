r"""Cutgraph Studio (FastAPI): on-demand loopback service for the React editor and media pipeline.

    .venv\Scripts\python scripts\studio_app.py [--host <loopback-address>] [--port 8765] [--no-browser]

The standalone Studio runs locally on the current computer, where it owns independent state,
provider credentials, GPU execution, and rendering. The embedded co-pilot proposes editorial
changes, and Studio jobs transcribe sources and export video documents through the
single-filtergraph compiler.
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.flags.optimize:
    raise SystemExit("Studio requires Python without optimization; remove -O or PYTHONOPTIMIZE.")


import uvicorn  # noqa: E402
import config  # noqa: E402
from pipeline import web_security, runtime_tools, state_lock, updates  # noqa: E402
from studio import helpers  # noqa: E402
from studio.app import app  # noqa: E402


def _configure_windowed_stdio() -> None:
    """Provide persistent diagnostics when pythonw has no console streams."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    import config

    config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stream = (config.RUNTIME_DIR / "backend.log").open(
        "a", encoding="utf-8", buffering=1, errors="backslashreplace",
    )
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def main() -> int:
    _configure_windowed_stdio()
    args = _arguments()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"refusing to bind {args.host}: Studio accepts loopback addresses only.")
        return 2
    try:
        with state_lock.launch(config.STATE_HOME):
            return _main(args)
    except state_lock.StateHomeInUse as error:
        print(str(error))
        url = state_lock.running_url(config.STATE_HOME)
        if url and not args.no_browser:
            _open_browser(url)
            return 0
        return 2


def _arguments() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Cutgraph Studio (FastAPI).")
    ap.add_argument("slug", nargs="?", help="(optional) open straight to one Video's Story")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="loopback bind address (127.0.0.1, localhost, or ::1)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser (for running as a persistent service)")
    return ap.parse_args()


def _main(args: argparse.Namespace) -> int:
    try:
        runtime_tools.configure_process_environment()
    except RuntimeError as error:
        print(str(error))
        return 2

    # Only persisted Videos can be deep-linked safely.
    slug = helpers._safe(args.slug or "")
    landing = f"/editor/?view=story&video={slug}" if slug and helpers._project_path(slug).is_file() else "/editor"
    _serve(app, host=args.host, port=args.port, landing=landing, no_browser=args.no_browser)
    return 0


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 — headless box: the URL is also logged
        pass


def _serve(application, *, host: str, port: int, landing: str, no_browser: bool) -> None:
    # GPU serialization and revision/SSE state are process-global; multiple workers
    # would duplicate model loads and split state between processes.
    configuration = uvicorn.Config(application, host=host, port=port, workers=1, log_level="warning",
                                   timeout_graceful_shutdown=10)
    with configuration.bind_socket() as listener:
        bound_port = listener.getsockname()[1]
        web_security.configure(bound_port)
        editor_url = state_lock.record_url(config.STATE_HOME, host, bound_port)
        origin = editor_url.removesuffix("/editor/")
        os.environ["CUTGRAPH_STUDIO_URL"] = origin
        url = origin + landing
        print(f"Cutgraph Studio -> {url}   (Ctrl+C to stop)")
        if not no_browser:
            _open_browser(url)
        import threading

        server = uvicorn.Server(configuration)
        finished = threading.Event()
        def update_shutdown():
            while not finished.wait(0.1):
                if updates.exit_requested.is_set():
                    server.should_exit = True
                    return
        watcher = threading.Thread(target=update_shutdown, name="studio-update-shutdown", daemon=True)
        watcher.start()
        try:
            server.run(sockets=[listener])
        finally:
            finished.set()
            watcher.join(timeout=1)
            updates.exit_requested.clear()



if __name__ == "__main__":
    raise SystemExit(main())
