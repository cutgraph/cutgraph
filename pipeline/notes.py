"""Atomic per-project decision log (NOTES discipline).

`outputs/notes/<slug>.md` is the agent's long-term memory (the chat session is ephemeral; a studio
restart is a fresh session). Every accept / reject / choose appends one entry. The append is a
read-modify-write made atomic the same way as `write_project` (temp-sibling + os.replace) and guarded
by a per-slug lock so two interleaved appends never lose an entry. SDK-free.
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import config
from pipeline import agent_credentials
from pipeline.names import exact_path, existing_name

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# Notes are read back into the co-pilot's context, so they must not carry text shaped like a tool call:
# XML-style call tags, JSON tool-use frames, or a tool's qualified name.
_TOOL_CALL = re.compile(
    r"<\s*/?\s*(?:function_calls|invoke|tool_call|tool_use|tool_result|function|parameter)\b"
    r'|"type"\s*:\s*"(?:tool_use|tool_result|function)"'
    r'|"(?:tool_calls|function_call)"\s*:'
    r"|mcp__",
    re.IGNORECASE,
)


class AgentNoteWriter:
    """Bound agent-authored notes per session; durable human decisions use append directly."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self._recent: deque[float] = deque()
        self._guard = threading.Lock()

    def append(self, title: str, body: str) -> None:
        if not isinstance(title, str) or not isinstance(body, str):
            raise ValueError("note title and body must be text")
        if len(title) + len(body) > 2000:
            raise ValueError("note title and body must total at most 2000 characters")
        if _TOOL_CALL.search(title + "\n" + body):
            raise ValueError("note must not contain tool-call syntax")
        with self._guard:
            now = time.monotonic()
            while self._recent and now - self._recent[0] >= 60:
                self._recent.popleft()
            if len(self._recent) >= 10:
                raise ValueError("note rate limit: at most 10 per minute per session")
            append(self.slug, title, body)
            self._recent.append(now)


def _lock(slug: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(slug)
        if lk is None:
            lk = threading.Lock()
            _locks[slug] = lk
        return lk


def _path(slug: str) -> Path:
    if existing_name(slug) is None:
        raise ValueError(f"unsafe notes slug: {slug!r}")
    path = config.NOTES_DIR / f"{slug}.md"
    exact_path(path)
    return path


def read(slug: str) -> str:
    path = _path(slug)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def append(slug: str, title: str, body: str) -> None:
    """Append `## <ISO-8601Z> — Claude: <title>\\n\\n<body>\\n\\n` atomically under a per-slug lock."""
    path = _path(slug)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = agent_credentials.redact(f"## {stamp} — Claude: {title}\n\n{body}\n\n")
    with _lock(slug):
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(current + entry, encoding="utf-8")
        os.replace(tmp, path)


def append_once(slug: str, title: str, body: str, key: str) -> None:
    """Append a durable note once, using a hidden stable marker for retry-safe workflows."""
    path = _path(slug)
    marker = f"<!-- cutgraph-note:{key} -->"
    with _lock(slug):
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if marker in current:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = agent_credentials.redact(f"## {stamp} — Claude: {title}\n\n{body}\n\n{marker}\n\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(current + entry, encoding="utf-8")
        os.replace(tmp, path)
