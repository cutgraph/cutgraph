from __future__ import annotations

import asyncio
import queue
import subprocess
import threading

from pipeline.agent_credentials import ClaudeConnection
from pipeline.addons import EVENTS_CHANNEL

_PROJECT_CREATE_LOCK = threading.Lock()


_claude_connection = ClaudeConnection()

# The prefix cannot occur in a Video slug.
ADDON_EVENTS_CHANNEL = EVENTS_CHANNEL


# ── transcription jobs ─────────────────────────────────────────────────────────
# WhisperX loads a big model onto the GPU, so transcribe runs in a single-slot FIFO worker
# (never two at once). The mechanical transcribe stage IS auto-runnable; editorial stays Claude.
# Kept as a sync thread+queue (NOT async): the single-slot GPU serializer is a process-global
# invariant, and workers=1 (see main()) keeps it authoritative.
_JOBS: dict[str, dict] = {}


_JOB_Q: "queue.Queue[str | object]" = queue.Queue()


_WORKER_STARTED = False


_WORKER_LOCK = threading.Lock()


_WORKER_STOP = threading.Event()


_WORKER_THREAD: threading.Thread | None = None


_WORKER_SENTINEL = object()


# Fire-and-forget work must still be owned by the application.  These are
# deliberately kept separate from the FIFO worker: proxy generation is an
# upload-side best-effort operation and does not belong in the transcription
# queue, but it must not outlive the Studio process silently.
_BACKGROUND_THREADS: set[threading.Thread] = set()


_BACKGROUND_LOCK = threading.Lock()


_BACKGROUND_STOP = threading.Event()


# source path (str) -> project names awaiting that source's scrub-proxy completion; see _spawn_proxy.
_PROXY_WAITERS: dict[str, set[str]] = {}


# source paths whose last scrub-proxy encode failed; the preview route won't retry them this run.
_PROXY_FAILED: set[str] = set()


_WORKER_JOIN_TIMEOUT_S = 5.0


_BACKGROUND_JOIN_TIMEOUT_S = 5.0


# Every media subprocess started by Studio is owned by the thread that started
# it.  ``subprocess.run`` hides its Popen object, so the short-lived scope below
# intercepts Popen creation while a Studio-owned operation is running.  This
# lets shutdown terminate the process *and* its descendants before joining the
# worker thread; a timed-out Python thread must never leave ffmpeg/WhisperX
# running after the application has closed.
_OWNED_PROCESSES: dict[int, tuple[subprocess.Popen[str], int]] = {}


_OWNED_PROCESSES_LOCK = threading.RLock()


_PROCESS_SCOPE_LOCK = threading.RLock()


_PROCESS_SCOPE_LOCAL = threading.local()


# Detached jobs (render_final, …) share this SAME single GPU slot (one serializer).
# They are keyed by an opaque job_id in the generalized `jobs` registry; a dequeued key is a
# job when `jobs.get()` resolves it, else it is a legacy transcribe slug (the existing path, untouched).
_MAIN_LOOP: "asyncio.AbstractEventLoop | None" = None


# Serialize GPU/ffmpeg work from jobs and route handlers to prevent competing encodes
# from racing the same output file or oversubscribing the GPU.
_GPU_LOCK = threading.Lock()


# ── project.json namespace + broadcast hub (concurrency spine) ────────────────
# The server is the single authoritative writer, so every accepted write runs in the async route
# handler on the event loop — an in-process hub publishing directly to per-project subscriber queues
# is deterministic and unit-testable (no fs.watch, which is flaky on Windows). Revision authority is
# the `revision` int inside the on-disk file; a missing file is on-disk revision 0.
class Hub:
    """Per-project fan-out of accepted-write notifications to SSE subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict]]] = {}

    def subscribe(self, project: str) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._subs.setdefault(project, set()).add(q)
        return q

    def unsubscribe(self, project: str, q: asyncio.Queue[dict]) -> None:
        subs = self._subs.get(project)
        if subs is not None:
            subs.discard(q)
            if not subs:
                self._subs.pop(project, None)

    def publish(self, project: str, revision: int) -> None:
        for q in list(self._subs.get(project, ())):
            q.put_nowait({"type": "project", "revision": revision})

    def publish_event(self, project: str, msg: dict) -> None:
        """Fan an arbitrary agent event (chat_text/tool_use/proposal/options/job/agent_error/turn_done)
        onto the SAME per-project SSE queue. The `{type:"project",revision}` contract of `publish` is
        left byte-identical — chat events never carry a `revision` and never touch the adopt path."""
        for q in list(self._subs.get(project, ())):
            q.put_nowait(msg)


hub = Hub()


# ── Claude co-editor: chat host + durable chat log ─────────────────
# The SDK-coupled AgentHost is reached only through this lazy accessor so the studio module top NEVER
# imports claude_agent_sdk / agent_host. agent_host itself imports the SDK lazily inside
# functions, so `from scripts import agent_host` here is SDK-free. Tests inject a fake via the override.
_agent_host: "object | None" = None


_agent_host_override: "object | None" = None  # tests set this to a fake AgentHost (no SDK imported)
