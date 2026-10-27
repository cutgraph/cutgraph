"""The SDK-free agent seam.

Every `claude-agent-sdk` interaction is hidden behind this `AgentSession` protocol so no unit test
ever imports the SDK, spawns `claude.exe`, or touches the network. `scripts/agent_host.py` provides
the real `SdkAgentSession` (lazy SDK import); tests inject `FakeAgentSession`. This module is
dependency-free: stdlib + typing only, so it is safe to import anywhere (studio routes, tests).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# One normalized event a session emits per assistant content-block / lifecycle step. The host maps
# it onto the SSE wire dict `{"type": <type>, **data}`. Types:
#   "chat_text" | "tool_use" | "proposal" | "options" | "job" | "turn_done" | "agent_error"
_EVENT_TYPES = frozenset(
    {"chat_text", "tool_use", "proposal", "options", "job", "turn_done", "agent_error"},
)


@dataclass(frozen=True)
class AgentEvent:
    type: str
    data: dict = field(default_factory=dict)
    # Internal rejection evidence is consumed before publishing or persisting an error.
    authentication_failed: bool = False
    key_fingerprint: str | None = None


# The callback the host hands to a session: emit one AgentEvent (awaitable so a real session can
# backpressure on a slow subscriber).
Emit = Callable[[AgentEvent], Awaitable[None]]


@runtime_checkable
class AgentSession(Protocol):
    async def aconnect(self) -> None:
        """Start the warm session (the real impl imports the SDK lazily and connects the client)."""
        ...

    async def send_turn(self, text: str, emit: Emit) -> None:
        """Drive exactly ONE user turn: emit an AgentEvent per content block, return at the turn's
        terminal result (a `turn_done` event is emitted last)."""
        ...

    async def aclose(self) -> None:
        """Tear the session down."""
        ...


class FakeAgentSession:
    """SDK-free test double. `script(text) -> list[AgentEvent]` yields the events for one turn; the
    default echoes the input and closes the turn. Records connect/close/turns for assertions."""

    def __init__(self, script: "Callable[[str], list[AgentEvent]] | None" = None) -> None:
        self._script = script
        self.connected = False
        self.closed = False
        self.turns: list[str] = []

    async def aconnect(self) -> None:
        self.connected = True

    async def send_turn(self, text: str, emit: Emit) -> None:
        self.turns.append(text)
        if self._script is not None:
            events = self._script(text)
        else:
            events = [AgentEvent("chat_text", {"text": f"echo: {text}"}), AgentEvent("turn_done", {})]
        for ev in events:
            await emit(ev)

    async def aclose(self) -> None:
        self.closed = True
