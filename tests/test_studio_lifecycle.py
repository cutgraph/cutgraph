from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time

import pytest

import config
from scripts import studio_app
from studio import compute
from studio import state as studio_state
from studio.routes import chat


@pytest.fixture(autouse=True)
def _isolate_durable_recovery(monkeypatch):  # noqa: ANN201
    monkeypatch.setattr(compute.jobs_mod, "recover_interrupted", lambda: {})


def test_lifespan_recovers_abandoned_jobs_before_opening_dispatch(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(compute.jobs_mod, "recover_interrupted", lambda: calls.append("recover") or {})
    monkeypatch.setattr(compute, "_prepare_runtime_start", lambda: calls.append("prepare"))
    monkeypatch.setattr(compute, "_stop_fifo_worker", lambda: None)
    monkeypatch.setattr(compute, "_stop_background_work", lambda: None)
    monkeypatch.setattr(chat, "_existing_chat_host", lambda: None)

    async def scenario() -> None:
        async with studio_app.app.router.lifespan_context(studio_app.app):
            assert calls == ["recover", "prepare"]

    asyncio.run(scenario())


def test_lifespan_stops_fifo_and_background_work_before_children(monkeypatch) -> None:
    calls: list[str] = []

    class Host:
        async def aclose_all(self) -> None:
            calls.append("agent")

    monkeypatch.setattr(studio_state, "_agent_host", Host())
    monkeypatch.setattr(studio_state, "_agent_host_override", None)
    monkeypatch.setattr(compute, "_stop_fifo_worker", lambda: calls.append("worker"))
    monkeypatch.setattr(compute, "_stop_background_work", lambda: calls.append("background"))

    async def scenario() -> None:
        async with studio_app.app.router.lifespan_context(studio_app.app):
            assert calls == []

    asyncio.run(scenario())
    assert calls == ["worker", "background", "agent"]


def test_fifo_worker_stop_kills_owned_child_before_join(monkeypatch) -> None:
    started = threading.Event()
    child_holder: list[subprocess.Popen] = []

    def run_child() -> None:
        with compute._owned_process_scope():
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            child_holder.append(child)
            started.set()
            while child.poll() is None:
                time.sleep(0.01)

    thread = threading.Thread(target=run_child)
    thread.start()
    assert started.wait(1)
    monkeypatch.setattr(studio_state, "_WORKER_THREAD", thread)
    monkeypatch.setattr(studio_state, "_WORKER_STARTED", True)
    monkeypatch.setattr(studio_state, "_WORKER_JOIN_TIMEOUT_S", 0.01)

    compute._stop_fifo_worker()
    assert studio_state._WORKER_STOP.is_set()
    thread.join(1)
    assert not thread.is_alive()
    assert child_holder[0].poll() is not None
    with studio_state._OWNED_PROCESSES_LOCK:
        assert not studio_state._OWNED_PROCESSES
    # A later lifespan can clear the stop gate once the owned child and its
    # worker have actually exited; no subprocess is left behind.
    compute._prepare_runtime_start()
    assert not studio_state._WORKER_STOP.is_set()
    while not studio_state._JOB_Q.empty():
        studio_state._JOB_Q.get_nowait()
        studio_state._JOB_Q.task_done()


def test_owned_scope_does_not_adopt_subprocess_from_another_thread() -> None:
    entered = threading.Event()
    release = threading.Event()

    def hold_scope() -> None:
        with compute._owned_process_scope():
            entered.set()
            release.wait(2)

    owner = threading.Thread(target=hold_scope)
    owner.start()
    assert entered.wait(1)
    unrelated = subprocess.Popen([sys.executable, "-c", "pass"])
    unrelated.wait(timeout=2)
    with studio_state._OWNED_PROCESSES_LOCK:
        assert id(unrelated) not in studio_state._OWNED_PROCESSES
    release.set()
    owner.join(1)
    assert not owner.is_alive()


def test_proxy_thread_is_tracked_and_kills_owned_child(monkeypatch) -> None:
    started = threading.Event()
    child_holder: list[subprocess.Popen] = []

    def make_proxy(_source) -> None:  # noqa: ANN001 - test double
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        child_holder.append(child)
        started.set()
        while child.poll() is None:
            time.sleep(0.01)

    monkeypatch.setattr(compute.proxy_stage, "make_proxy", make_proxy)
    monkeypatch.setattr(studio_state, "_BACKGROUND_JOIN_TIMEOUT_S", 0.01)
    studio_state._BACKGROUND_STOP.clear()
    compute._spawn_proxy(config.DATA_DIR / "lifecycle.mp4")
    assert started.wait(1)
    with studio_state._BACKGROUND_LOCK:
        assert len(studio_state._BACKGROUND_THREADS) == 1

    compute._stop_background_work()
    assert studio_state._BACKGROUND_STOP.is_set()
    assert child_holder[0].poll() is not None
    with studio_state._BACKGROUND_LOCK:
        assert not studio_state._BACKGROUND_THREADS
    with studio_state._OWNED_PROCESSES_LOCK:
        assert not studio_state._OWNED_PROCESSES


def test_lifespan_closes_agent_children(monkeypatch) -> None:
    calls: list[str] = []

    class Host:
        async def aclose_all(self) -> None:
            calls.append("agent")

    host = Host()
    monkeypatch.setattr(studio_state, "_agent_host", host)
    monkeypatch.setattr(studio_state, "_agent_host_override", None)

    async def scenario() -> None:
        async with studio_app.app.router.lifespan_context(studio_app.app):
            assert calls == []

    asyncio.run(scenario())

    assert calls == ["agent"]
    assert studio_state._agent_host is None


def test_lifespan_clears_agent_when_shutdown_fails(monkeypatch) -> None:
    calls: list[str] = []

    class Host:
        async def aclose_all(self) -> None:
            calls.append("agent")
            raise RuntimeError("agent cleanup failed")

    monkeypatch.setattr(studio_state, "_agent_host", Host())
    monkeypatch.setattr(studio_state, "_agent_host_override", None)

    async def scenario() -> None:
        try:
            async with studio_app.app.router.lifespan_context(studio_app.app):
                pass
        except RuntimeError as exc:
            assert str(exc) == "agent cleanup failed"

    asyncio.run(scenario())

    assert calls == ["agent"]
    assert studio_state._agent_host is None
