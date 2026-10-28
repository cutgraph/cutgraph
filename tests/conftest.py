"""Every pytest run gets its own throwaway state home, set before any test imports `config`.

Without it `config.STATE_HOME` falls back to the default state home, which is the user's real
Cutgraph data (their projects, media and runtime files), and parallel runs in different worktrees
would write to the same directory and trip over each other's files.

Every test also runs in the same process, so `_restore_process_state` puts back the process-wide
state a test leaves behind, and the suite passes in any order.
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from starlette.testclient import _TestClientTransport

collect_ignore = ["e2e"]

_STATE_HOME = tempfile.mkdtemp(prefix="cutgraph-tests-")
os.environ["CUTGRAPH_STATE_HOME"] = _STATE_HOME
_PACKS_DIR = tempfile.mkdtemp(prefix="cutgraph-test-packs-")
os.environ["CUTGRAPH_PACKS_DIR"] = _PACKS_DIR
atexit.register(shutil.rmtree, _PACKS_DIR, True)
atexit.register(shutil.rmtree, _STATE_HOME, True)

def pytest_configure() -> None:
    # CI quarantine tests also run this conftest outside the repository.
    if (Path(__file__).resolve().parents[1] / "pipeline" / "web_security.py").is_file():
        from pipeline import web_security

        web_security.set_extra_hosts(frozenset({"studio.test", "testserver", "test", "t"}))


@pytest.fixture(autouse=True)
def _internal_studio_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    def authorize(request: httpx.Request) -> None:
        security = sys.modules.get("pipeline.web_security")
        if security is not None and request.url.netloc.decode() in security.EXTRA_HOSTS:
            request.headers.setdefault("X-Cutgraph-Internal", security.INTERNAL_TOKEN)

    async_request = httpx.ASGITransport.handle_async_request
    sync_request = _TestClientTransport.handle_request

    async def handle_async(transport, request: httpx.Request) -> httpx.Response:
        authorize(request)
        return await async_request(transport, request)

    def handle_sync(transport, request: httpx.Request) -> httpx.Response:
        authorize(request)
        return sync_request(transport, request)

    monkeypatch.setattr(httpx.ASGITransport, "handle_async_request", handle_async)
    monkeypatch.setattr(_TestClientTransport, "handle_request", handle_sync)


@pytest.fixture(autouse=True)
def _encoder_process_state(monkeypatch: pytest.MonkeyPatch) -> None:
    if not (Path(__file__).resolve().parents[1] / "pipeline" / "encoder_ladder.py").is_file():
        return
    from pipeline import encoder_ladder

    monkeypatch.setattr(encoder_ladder, "_PROBES", {})
    monkeypatch.setattr(encoder_ladder, "_FAILED_INITS", {})


@pytest.fixture(autouse=True)
def _studio_agent_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    if not (Path(__file__).resolve().parents[1] / "pipeline" / "agent_providers.py").is_file():
        return
    from pipeline import agent_credentials
    from studio import state as studio_state

    # Route tests use fake sessions and a synthetic API key; credential tests remove this override.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-agent-key-value")
    # Synthetic stock keys such as "key" would otherwise redact ordinary words from later tests' logs.
    monkeypatch.setattr(agent_credentials, "_log_secrets", set())
    monkeypatch.setattr(agent_credentials, "_log_pattern", None)
    monkeypatch.setattr(agent_credentials, "_new_client", lambda: httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200))))
    connection = agent_credentials.ClaudeConnection()
    connection.verify()
    monkeypatch.setattr(studio_state, "_claude_connection", connection)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("CI", "").lower() != "true":
        return
    quarantine = {}
    for line in Path(__file__).with_name("ci_quarantine.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        nodeid, separator, reason = line.partition(" # ")
        nodeid, reason = nodeid.strip(), reason.strip()
        if not separator or not nodeid or not reason or nodeid in quarantine:
            raise pytest.UsageError("CI quarantine entries require a unique test id and ' # reason'")
        quarantine[nodeid] = reason
    for item in items:
        if item.nodeid in quarantine:
            # Only recorded environment failures may be skipped in CI.
            item.add_marker(pytest.mark.skip(reason=f"CI quarantine: {quarantine[item.nodeid]}"))


# Studio's state as a fresh import leaves it.
_FRESH_STUDIO = {"worker": None, "wstop": False, "bstop": False, "threads": set(), "queue": [], "jobs": {},
                 "waiters": {}, "failed": set()}


def _snapshot() -> dict:
    jobs = sys.modules.get("pipeline.jobs")
    studio = sys.modules.get("studio.state")
    return {
        "registry": dict(jobs._registry) if jobs else {},
        "studio": _FRESH_STUDIO if studio is None else {
            "worker": studio._WORKER_THREAD, "wstop": studio._WORKER_STOP.is_set(),
            "bstop": studio._BACKGROUND_STOP.is_set(), "threads": set(studio._BACKGROUND_THREADS),
            "queue": list(studio._JOB_Q.queue), "jobs": dict(studio._JOBS),
            "waiters": {k: set(v) for k, v in studio._PROXY_WAITERS.items()}, "failed": set(studio._PROXY_FAILED),
        },
    }


def _reset(target: dict | set, value: dict | set) -> None:
    target.clear()
    if isinstance(target, dict) and isinstance(value, dict):
        target.update(value)
    elif isinstance(target, set) and isinstance(value, set):
        target.update(value)
    else:
        raise TypeError("State snapshots must match their target container")


def _restore(before: dict) -> None:
    studio = sys.modules.get("studio.state")
    was = before["studio"]
    if studio is not None:
        from studio import compute

        # First stop the threads the test started: a job they finish still lands in the registry.
        if studio._WORKER_THREAD is not None and studio._WORKER_THREAD is not was["worker"]:
            compute._stop_fifo_worker()
        if set(studio._BACKGROUND_THREADS) - was["threads"]:
            compute._stop_background_work()
    jobs = sys.modules.get("pipeline.jobs")
    if jobs is not None:
        with jobs._lock:
            _reset(jobs._registry, before["registry"])
    if studio is None:
        return
    for gate, is_set in ((studio._WORKER_STOP, was["wstop"]), (studio._BACKGROUND_STOP, was["bstop"])):
        if is_set:
            gate.set()
        else:
            gate.clear()
    while not studio._JOB_Q.empty():
        studio._JOB_Q.get_nowait()
        studio._JOB_Q.task_done()
    for item in was["queue"]:
        studio._JOB_Q.put(item)
    with studio._BACKGROUND_LOCK:
        _reset(studio._PROXY_WAITERS, was["waiters"])
        _reset(studio._PROXY_FAILED, was["failed"])
    _reset(studio._JOBS, was["jobs"])


@pytest.fixture(autouse=True)
def _restore_process_state() -> Iterator[None]:
    """Put back what a test changed in the process: the durable job registry (pipeline.jobs) and
    Studio's FIFO worker, background proxy threads (both stopped when the test started them), their
    dispatch gates (a lifespan's shutdown leaves them set), the job queue and its in-memory tables."""
    before = _snapshot()
    yield
    _restore(before)


@pytest.fixture
def client_rig(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline import cast as cast_module

    monkeypatch.setitem(cast_module.RIGS, "sample-rig", json.loads(
        (Path(__file__).parent / "fixtures" / "inputs" / "synthetic_rig.json").read_text(encoding="utf-8"),
    ))
