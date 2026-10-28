"""AgentHost drives a session behind the seam. The whole point: NO live SDK — the fake
session is injected, so `import claude_agent_sdk` is never triggered."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import config
from pipeline import proposals
from pipeline import cast as cast_mod
from pipeline import project as project_mod
from pipeline import script_video as sv
from pipeline.agent_session import AgentEvent, Emit, FakeAgentSession
from scripts import agent_host


@pytest.fixture(autouse=True)
def _sdk_unimported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide an SDK another test already imported, so each "never touched the SDK" check is about this test."""
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)


def _stub_ctx(project: str, sink: list[dict]) -> agent_host.ToolCtx:
    return agent_host.ToolCtx(
        slug=project,
        read_doc=lambda: (None, 0),
        read_transcript=lambda: None,
        fps=lambda: 30,
        emit=lambda msg: sink.append(msg),
    )


async def _drain_until(sink: list[dict], pred, tries: int = 200) -> None:
    for _ in range(tries):
        if any(pred(m) for m in sink):
            return
        await asyncio.sleep(0)


def test_persona_loads_from_product_prompts() -> None:
    assert agent_host._PERSONA_PATH == config.CODE_ROOT / "pipeline" / "prompts" / "copilot.md"
    assert agent_host.load_persona().strip()


def test_persona_documents_exactly_the_registered_tools() -> None:
    persona = agent_host.load_persona()
    registered = {*agent_host._EDITOR_TOOLS, *agent_host._GPU_TOOLS}
    # Code spans that open with a snake_case name (optionally called) are tool references; job_id is
    # the one argument the prompt names on its own.
    named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)(?:\([^`]*\))?`", persona)) - {"job_id"}
    assert named <= registered
    for name in registered:
        assert re.search(rf"`{name}[`(]", persona), name


@pytest.mark.parametrize("error", [None, "authentication_failed", "server_error", "billing_error"])
def test_claude_adapter_starts_turn_with_fake_sdk(monkeypatch, tmp_path, error) -> None:
    events: list[dict] = []
    clients: list = []

    @dataclass
    class TextBlock:
        text: str

    @dataclass
    class AssistantMessage:
        content: list
        error: str | None

    class ResultMessage:
        pass

    class OtherBlock:
        pass

    class Client:
        def __init__(self, options) -> None:
            self.options = options
            self.turns: list[str] = []
            self.connected = False
            self.closed = False
            clients.append(self)

        async def connect(self, prompt) -> None:
            assert prompt is None
            self.connected = True

        async def query(self, text: str) -> None:
            self.turns.append(text)

        async def receive_response(self):
            yield AssistantMessage([TextBlock("A proposed edit")], error)
            yield ResultMessage()

        async def disconnect(self) -> None:
            self.closed = True

    sdk = SimpleNamespace(
        ClaudeSDKClient=Client, ClaudeAgentOptions=lambda **kwargs: kwargs,
        AssistantMessage=AssistantMessage, TextBlock=TextBlock, ResultMessage=ResultMessage,
        UserMessage=OtherBlock, ToolResultBlock=OtherBlock, ToolUseBlock=OtherBlock,
        tool=lambda *args, **kwargs: lambda function: function,
        create_sdk_mcp_server=lambda *args, **kwargs: kwargs,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(agent_host, "_bundled_cli", lambda: "synthetic-cli")
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)

    async def scenario() -> None:
        host = agent_host.AgentHost(make_session=agent_host.make_provider_session,
                                    make_ctx=lambda slug: _stub_ctx(slug, events))
        await host.post_turn("video", "Tighten the opening")
        await _drain_until(events, lambda event: event["type"] == "turn_done")
        await host.aclose_all()

    asyncio.run(scenario())
    if error is None:
        assert events == [{"type": "chat_text", "text": "A proposed edit"}, {"type": "turn_done"}]
    else:
        assert len(events) == 2 and events[-1] == {"type": "turn_done"}
        assert events[0]["type"] == "agent_error"
        assert events[0]["kind"] == agent_host._classify_assistant_error(error)
        if error == "authentication_failed":
            assert events[0]["authentication_failed"] is True
            assert events[0]["key_fingerprint"] == agent_host.agent_credentials.key_fingerprint("test-agent-key-value")
        else:
            assert "authentication_failed" not in events[0] and "key_fingerprint" not in events[0]
    assert clients[0].connected and clients[0].closed
    assert clients[0].turns == ["Tighten the opening"]
    assert clients[0].options["system_prompt"] == agent_host.load_persona()
    assert clients[0].options["cwd"] == str(tmp_path / "video")


@pytest.mark.parametrize("failure_stage,error,message", [
    ("turn", FileNotFoundError(), "agent_session_unavailable"),
    ("turn", ValueError("invalid_request"), "The co-pilot could not complete the request."),
    ("connect", RuntimeError("provider_unavailable"), "provider_unavailable"),
])
def test_agent_host_provider_failure_emits_one_terminal_pair(failure_stage, error, message) -> None:
    wire: list[dict] = []

    class Session:
        def __init__(self) -> None:
            self.closed = False
            self.turns = 0

        async def aconnect(self) -> None:
            if failure_stage == "connect":
                raise error

        async def send_turn(self, text: str, emit: Emit) -> None:
            self.turns += 1
            raise error

        async def aclose(self) -> None:
            self.closed = True

    session = Session()

    async def scenario() -> None:
        host = agent_host.AgentHost(make_session=lambda _p, _c: session,
                                    make_ctx=lambda slug: _stub_ctx(slug, wire))
        await host.post_turn("video", "help")
        await _drain_until(wire, lambda event: event["type"] == "turn_done")
        await host.aclose_all()

    asyncio.run(scenario())
    assert wire == [{"type": "agent_error", "kind": "runtime", "message": message}, {"type": "turn_done"}]
    assert session.closed
    assert session.turns == (0 if failure_stage == "connect" else 1)


def test_agent_host_cancellation_race_emits_one_terminal_pair_and_closes_session() -> None:
    wire: list[dict] = []
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class Session:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aconnect(self) -> None:
            pass

        async def send_turn(self, text: str, emit: Emit) -> None:
            started.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.close_calls += 1
            cleanup_started.set()
            await cleanup_release.wait()

    session = Session()

    async def scenario() -> None:
        host = agent_host.AgentHost(make_session=lambda _p, _c: session,
                                    make_ctx=lambda slug: _stub_ctx(slug, wire))
        await host.post_turn("video", "one")
        await host.post_turn("video", "two")
        await host.post_turn("video", "three")
        await started.wait()
        task = host._workers["video"]
        task.cancel()
        await cleanup_started.wait()
        task.cancel()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await host.aclose_all()

    asyncio.run(scenario())
    assert wire == [
        {"type": "agent_error", "kind": "runtime", "message": "turn_cancelled"},
        {"type": "turn_done"},
    ] * 3
    assert session.close_calls == 1


def test_post_turn_drives_session_and_emits() -> None:
    events: list[dict] = []
    sessions: list[FakeAgentSession] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        s = FakeAgentSession(script=lambda t: [
            AgentEvent("chat_text", {"text": f"reply to {t}"}),
            AgentEvent("turn_done", {}),
        ])
        sessions.append(s)
        return s

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))

    async def scenario() -> None:
        await host.post_turn("proj", "hello")
        await _drain_until(events, lambda m: m["type"] == "turn_done")
        await host.aclose_all()

    asyncio.run(scenario())

    assert {"type": "chat_text", "text": "reply to hello"} in events
    assert any(m["type"] == "turn_done" for m in events)
    assert sessions[0].connected and sessions[0].closed
    assert sessions[0].turns == ["hello"]
    assert "claude_agent_sdk" not in sys.modules   # fake path never touched the SDK


def test_scripted_auth_error_surfaces() -> None:
    events: list[dict] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        return FakeAgentSession(script=lambda t: [
            AgentEvent("agent_error", {"kind": "auth", "message": "Key not verified. Open Settings to verify your Anthropic API key."}),
            AgentEvent("turn_done", {}),
        ])

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))

    async def scenario() -> None:
        await host.post_turn("proj", "tighten this")
        await _drain_until(events, lambda m: m["type"] == "turn_done")
        await host.aclose_all()

    asyncio.run(scenario())

    err = next(m for m in events if m["type"] == "agent_error")
    assert err["kind"] == "auth"
    assert err["message"] == "Key not verified. Open Settings to verify your Anthropic API key."
    assert "claude_agent_sdk" not in sys.modules


def test_turns_serialize_per_project() -> None:
    events: list[dict] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        return FakeAgentSession(script=lambda t: [
            AgentEvent("chat_text", {"text": t}), AgentEvent("turn_done", {}),
        ])

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))

    async def scenario() -> None:
        await host.post_turn("proj", "one")
        await host.post_turn("proj", "two")
        await _drain_until(events, lambda m: m == {"type": "chat_text", "text": "two"})
        await host.aclose_all()

    asyncio.run(scenario())

    texts = [m["text"] for m in events if m["type"] == "chat_text"]
    assert texts == ["one", "two"]   # FIFO, one session, serialized


# ── AssistantMessage.error is a LITERAL STRING → classify the string, not a .type attr ──
def test_classify_assistant_error_from_literal_string() -> None:
    assert agent_host._classify_assistant_error("authentication_failed") == "auth"
    assert agent_host._classify_assistant_error("billing_error") == "billing"
    assert agent_host._classify_assistant_error("some_other_error") == "runtime"
    assert agent_host._classify_assistant_error(None) == "runtime"


# ── propose_patch emits the REAL (guarded) ops, not an ops-less event ──────────
def test_do_propose_patch_emits_guarded_nonempty_ops(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "PROPOSALS_DIR", tmp_path / "proposals")
    doc = {"name": "p", "fps": 30, "width": 1920, "height": 1080,
           "tracks": [{"id": "t1", "kind": "speech", "clips": [
               {"id": f"c{i}", "srcInF": 0, "srcOutF": 30, "tlStartF": i * 30} for i in range(5)]}]}
    sink: list[dict] = []
    ctx = agent_host.ToolCtx(slug="zzhostprop", read_doc=lambda: (doc, 9),
                             read_transcript=lambda: None, fps=lambda: 30, emit=sink.append)

    out = json.loads(agent_host._do_propose_patch(
        ctx, {"ops": [{"op": "remove", "path": "/tracks/0/clips/3"}], "title": "Cut", "rationale": "r"}))

    ev = sink[0]
    assert ev["type"] == "proposal" and ev["status"] == "pending"
    assert ev["ops"]                                              # NON-EMPTY (the regression)
    assert ev["ops"][0] == {"op": "test", "path": "/tracks/0/clips/3/id", "value": "c3"}
    assert ev["ops"][-1] == {"op": "remove", "path": "/tracks/0/clips/3"}
    # the STORED proposal carries the identical guarded ops (the getProposals accept path also works)
    assert proposals.read("zzhostprop", out["id"])["ops"] == ev["ops"]  # type: ignore[index]


def test_do_propose_patch_carries_grounded_intent_and_material_preview(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "PROPOSALS_DIR", tmp_path / "proposals")
    doc = {"name": "p", "fps": 30, "width": 1920, "height": 1080, "tracks": []}
    sink: list[dict] = []
    ctx = agent_host.ToolCtx(slug="zzhostgrounded", read_doc=lambda: (doc, 4),
                             read_transcript=lambda: None, fps=lambda: 30, emit=sink.append)
    out = json.loads(agent_host._do_propose_patch(ctx, {
        "title": "Tighten opener", "rationale": "grounded", "material": True,
        "intentGroups": [{"id": "trim", "intent": "trim intro", "ops": [
            {"op": "replace", "path": "/width", "value": 1280},
        ]}],
        "citations": [
            {"kind": "transcript", "start": 0, "end": 1, "summary": "claim"},
        ],
        "preview": {"startF": 0, "endF": 60, "previewRef": "/media/preview/clip123.mp4"},
    }))
    assert sink[0]["intentGroups"][0]["id"] == "trim"
    assert sink[0]["preview"]["previewRef"].endswith("clip123.mp4")
    stored = proposals.read("zzhostgrounded", out["id"])
    assert stored is not None
    assert stored["citations"] == [{"kind": "transcript", "start": 0, "end": 1, "summary": "claim"}]


def _registered_tool_schemas(monkeypatch) -> dict[str, dict]:
    schemas: dict[str, dict] = {}

    def tool(name, _description, schema):
        schemas[name] = schema
        return lambda function: function

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", SimpleNamespace(
        tool=tool, create_sdk_mcp_server=lambda *args, **kwargs: kwargs))
    agent_host.build_editor_server(_stub_ctx("video", []))
    return schemas


def test_propose_patch_schema_describes_each_citation_field(monkeypatch) -> None:
    import jsonschema

    schema = _registered_tool_schemas(monkeypatch)["propose_patch"]
    assert schema is agent_host._PROPOSE_PATCH_SCHEMA
    validator = jsonschema.Draft202012Validator(schema)
    validator.check_schema(schema)
    citation = schema["properties"]["citations"]["items"]
    assert citation["type"] == "object"
    assert citation["required"] == ["kind", "start", "end", "summary"]
    assert citation["properties"]["kind"]["enum"] == ["transcript"]
    assert {citation["properties"][f]["type"] for f in ("start", "end")} == {"number"}
    assert all("seconds" in citation["properties"][f]["description"] for f in ("start", "end"))
    # The example embedded in each description is itself a valid value.
    for name in ("citations", "intentGroups"):
        description = schema["properties"][name]["description"]
        example = json.loads(description[description.index("["):])
        assert not list(jsonschema.Draft202012Validator(schema["properties"][name]).iter_errors(example))
    valid = {"title": "Cut", "rationale": "r",
             "intentGroups": [{"id": "trim", "intent": "trim", "ops": [{"op": "remove", "path": "/tracks/0/clips/0"}]}],
             "citations": [{"kind": "transcript", "start": 0.5, "end": 1.5, "summary": "a restart"}]}
    assert validator.is_valid(valid)
    for bad in ('[{"kind":"transcript"}]', [], [{"kind": "transcript", "start": "0", "end": 1, "summary": "s"}],
                [{"kind": "visual", "start": 0, "end": 1, "summary": "s"}], [{"start": 0, "end": 1, "summary": "s"}]):
        assert not validator.is_valid({**valid, "citations": bad})


@pytest.mark.parametrize("args,field", [
    ({"citations": [{"kind": "transcript", "start": 2.0, "end": 1.0, "summary": "s"}]}, "citations[0].end"),
    ({"citations": [{"kind": "transcript", "start": -1, "end": 1.0, "summary": "s"}]}, "citations[0].start"),
    ({"citations": [{"kind": "transcript", "start": 0, "end": 1.0}]}, "citations[0].summary"),
    ({"citations": [{"kind": "visual", "start": 0, "end": 1.0, "summary": "s"}]}, "citations[0].kind"),
    ({"citations": '[{"kind":"transcript"}]'}, "citations"),
    ({"title": ""}, "title"),
    ({"intentGroups": [{"id": "trim", "intent": "trim", "ops": []}]}, "intentGroups[0].ops"),
])
def test_do_propose_patch_names_the_bad_field(tmp_path, monkeypatch, args, field) -> None:
    monkeypatch.setattr(config, "PROPOSALS_DIR", tmp_path / "proposals")
    sink: list[dict] = []
    ctx = agent_host.ToolCtx(slug="zzhostbadfield", read_doc=lambda: ({"tracks": []}, 3),
                             read_transcript=lambda: None, fps=lambda: 30, emit=sink.append)
    valid = {"title": "Cut", "rationale": "r",
             "intentGroups": [{"id": "trim", "intent": "trim", "ops": [{"op": "replace", "path": "/width", "value": 1}]}],
             "citations": [{"kind": "transcript", "start": 0, "end": 1, "summary": "claim"}]}
    out = json.loads(agent_host._do_propose_patch(ctx, {**valid, **args}))
    assert set(out) == {"error"} and out["error"].startswith(field)
    assert sink == [] and proposals.list_all("zzhostbadfield") == []


def _readiness_sdk(monkeypatch, statuses) -> list:
    """A fake SDK whose client reports `statuses` (dicts, or exceptions to raise) from get_mcp_status."""
    clients: list = []

    class ResultMessage:
        pass

    class Client:
        def __init__(self, options) -> None:
            self.log: list[str] = []
            self.statuses = list(statuses)
            clients.append(self)

        async def connect(self, prompt) -> None:
            self.log.append("connect")

        async def get_mcp_status(self) -> dict:
            self.log.append("status")
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            if isinstance(status, BaseException):
                raise status
            return status

        async def query(self, text: str) -> None:
            self.log.append("query")

        async def receive_response(self):
            yield ResultMessage()

        async def disconnect(self) -> None:
            self.log.append("disconnect")

    class Other:
        pass

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", SimpleNamespace(
        ClaudeSDKClient=Client, ClaudeAgentOptions=lambda **kwargs: kwargs,
        AssistantMessage=Other, TextBlock=Other, ResultMessage=ResultMessage,
        UserMessage=Other, ToolResultBlock=Other, ToolUseBlock=Other,
        tool=lambda *args, **kwargs: lambda function: function,
        create_sdk_mcp_server=lambda *args, **kwargs: kwargs,
    ))
    monkeypatch.setattr(agent_host, "_bundled_cli", lambda: "synthetic-cli")
    monkeypatch.setattr(agent_host, "_MCP_READY_POLL_S", 0)
    return clients


def _servers(gpu: str, editor: str = "connected") -> dict:
    return {"mcpServers": [{"name": "editor", "status": editor}, {"name": "gpu", "status": gpu}]}


def _first_turn(tmp_path, monkeypatch) -> list[dict]:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    events: list[dict] = []

    async def scenario() -> None:
        host = agent_host.AgentHost(make_session=agent_host.make_provider_session,
                                    make_ctx=lambda slug: _stub_ctx(slug, events))
        await host.post_turn("video", "Tighten the opening")
        await _drain_until(events, lambda event: event["type"] == "turn_done")
        await host.aclose_all()

    asyncio.run(scenario())
    return events


@pytest.mark.parametrize("terminal", ["connected", "failed"])
def test_first_turn_waits_for_pending_mcp_servers(tmp_path, monkeypatch, terminal) -> None:
    clients = _readiness_sdk(monkeypatch, [
        {"mcpServers": []}, _servers("pending", editor="pending"), _servers("pending"), _servers(terminal),
    ])
    assert _first_turn(tmp_path, monkeypatch) == [{"type": "turn_done"}]
    assert clients[0].log == ["connect", "status", "status", "status", "status", "query", "disconnect"]


@pytest.mark.parametrize("statuses", [[_servers("pending")], [RuntimeError("control request timed out")]])
def test_mcp_readiness_wait_is_bounded_and_best_effort(tmp_path, monkeypatch, statuses) -> None:
    clients = _readiness_sdk(monkeypatch, statuses)
    monkeypatch.setattr(agent_host, "_MCP_READY_TIMEOUT_S", 0.05)
    assert _first_turn(tmp_path, monkeypatch) == [{"type": "turn_done"}]
    log = clients[0].log
    assert log[0] == "connect" and log[-2:] == ["query", "disconnect"]
    assert set(log[1:-2]) == {"status"}


def test_turn_posted_while_aclose_all_drains_gets_a_tracked_worker() -> None:
    events: list[dict] = []
    closing, release = asyncio.Event(), asyncio.Event()
    sessions: dict[str, list] = {}

    class Session(FakeAgentSession):
        async def aclose(self) -> None:
            if self is sessions["one"][0]:
                closing.set()
                await release.wait()
            await super().aclose()

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        session = Session(script=lambda t: [AgentEvent("chat_text", {"text": f"{project}:{t}"}),
                                            AgentEvent("turn_done", {})])
        sessions.setdefault(project, []).append(session)
        return session

    async def scenario() -> None:
        host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))
        await host.post_turn("one", "first")
        await _drain_until(events, lambda m: m.get("text") == "one:first")
        shutdown = asyncio.create_task(host.aclose_all())
        await closing.wait()
        await host.post_turn("one", "again")
        await host.post_turn("two", "hello")
        await _drain_until(events, lambda m: m.get("text") == "two:hello")
        release.set()
        await shutdown
        assert set(host._workers) == {"one", "two"}
        await host.aclose_all()
        assert host._workers == {}

    asyncio.run(scenario())
    assert {m.get("text") for m in events} >= {"one:again", "two:hello"}
    assert [s.closed for group in sessions.values() for s in group] == [True, True, True]
    assert len(sessions["one"]) == 2


# ── accept/reject feedback is prepended to the warm session's next turn ──
def test_push_context_prepends_to_next_turn() -> None:
    events: list[dict] = []
    sessions: list[FakeAgentSession] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        s = FakeAgentSession(script=lambda t: [AgentEvent("turn_done", {})])
        sessions.append(s)
        return s

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))

    async def scenario() -> None:
        host.push_context("proj", "System: the user rejected proposal X (keep the intro)")
        await host.post_turn("proj", "try again")
        await _drain_until(events, lambda m: m["type"] == "turn_done")
        await host.aclose_all()

    asyncio.run(scenario())

    turn = sessions[0].turns[0]
    assert turn.startswith("System: the user rejected proposal X")   # feedback folded in FIRST
    assert turn.endswith("try again")                                # ahead of the user's message


def test_pending_context_drains_once() -> None:
    """The queued note reaches exactly the NEXT turn, then is cleared (not re-fed forever)."""
    events: list[dict] = []
    sessions: list[FakeAgentSession] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        s = FakeAgentSession(script=lambda t: [AgentEvent("turn_done", {})])
        sessions.append(s)
        return s

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))

    async def scenario() -> None:
        host.push_context("proj", "System: accepted P1")
        await host.post_turn("proj", "first")
        await _drain_until(events, lambda m: m["type"] == "turn_done")
        await host.post_turn("proj", "second")
        await _drain_until(events, lambda m: sum(e["type"] == "turn_done" for e in events) >= 2)
        await host.aclose_all()

    asyncio.run(scenario())

    assert "accepted P1" in sessions[0].turns[0]
    assert sessions[0].turns[1] == "second"        # note NOT repeated on the following turn


# ── warm-session LRU cap + idle-TTL eviction ──────────────────────────────────
def test_warm_session_lru_cap_evicts_oldest(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_MAX_WARM_SESSIONS", 2)
    events: list[dict] = []
    sessions: dict[str, FakeAgentSession] = {}

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        s = FakeAgentSession(script=lambda t: [AgentEvent("turn_done", {})])
        sessions[project] = s
        return s

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))

    async def scenario() -> set[str]:
        await host.post_turn("a", "hi")
        await host.post_turn("b", "hi")
        await host.post_turn("c", "hi")   # over the cap of 2 ⇒ evict the LRU (a)
        keys = set(host._workers)
        await host.aclose_all()
        return keys

    keys = asyncio.run(scenario())
    assert keys == {"b", "c"}             # a evicted, b & c stay warm
    assert sessions["a"].closed           # evicted gracefully (SDK client torn down)


# ──: co-editor model/effort/mode selectors ────────────────────────────────────
def test_agent_settings_from_request_validates_and_defaults() -> None:
    """The wire trust boundary: unknown values fall back to defaults; valid ones pass through; the UI's
    "Default" model row ("" / "default") normalizes to None (⇒ SDK default)."""
    junk = agent_host.AgentSettings.from_request({"model": "gpt-4", "effort": "ludicrous", "mode": "yolo"})
    assert junk == agent_host.AgentSettings(model=config.AGENT_MODEL, effort=None, mode="default")

    ok = agent_host.AgentSettings.from_request({"model": "claude-opus-4-8", "effort": "high", "mode": "plan"})
    assert ok == agent_host.AgentSettings(provider_id="claude", profile_id="claude-opus-4-8",
                                           model="claude-opus-4-8", effort="high", mode="plan")

    assert agent_host.AgentSettings.from_request({"model": "default"}).model is None
    assert agent_host.AgentSettings.from_request({}) == agent_host.AgentSettings()


def test_settings_thread_into_session_ctx() -> None:
    """The picked knobs reach build_options via ctx.settings on the session the worker creates."""
    events: list[dict] = []
    ctxs: list[agent_host.ToolCtx] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        ctxs.append(ctx)
        return FakeAgentSession(script=lambda t: [AgentEvent("turn_done", {})])

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))
    picked = agent_host.AgentSettings(model="claude-opus-4-8", effort="high", mode="plan")

    async def scenario() -> None:
        await host.post_turn("proj", "hi", picked)
        await _drain_until(events, lambda m: m["type"] == "turn_done")
        await host.aclose_all()

    asyncio.run(scenario())
    assert ctxs[0].settings == picked
    assert "claude_agent_sdk" not in sys.modules


def test_changed_settings_rebuild_session_same_settings_reuse() -> None:
    """Options are frozen at connect: a turn whose settings differ tears down + rebuilds the session;
    identical settings reuse the warm one."""
    events: list[dict] = []
    sessions: list[FakeAgentSession] = []
    ctxs: list[agent_host.ToolCtx] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        ctxs.append(ctx)
        s = FakeAgentSession(script=lambda t: [AgentEvent("turn_done", {})])
        sessions.append(s)
        return s

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))
    a = agent_host.AgentSettings(mode="default")
    b = agent_host.AgentSettings(mode="plan")

    async def _await_turns(n: int) -> None:
        await _drain_until(events, lambda _m: sum(e["type"] == "turn_done" for e in events) >= n)

    async def scenario() -> None:
        await host.post_turn("proj", "one", a)
        await _await_turns(1)
        await host.post_turn("proj", "two", b)     # b != a ⇒ rebuild
        await _await_turns(2)
        await host.post_turn("proj", "three", b)    # b == b ⇒ reuse
        await _await_turns(3)
        await host.aclose_all()

    asyncio.run(scenario())
    assert len(sessions) == 2                        # rebuilt once (a→b), NOT for the b→b turn
    assert sessions[0].closed                        # the stale session was torn down on the change
    assert ctxs[0].settings == a and ctxs[1].settings == b
    assert "claude_agent_sdk" not in sys.modules


def test_idle_session_evicted_after_ttl(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_SESSION_TTL_S", -1)  # any idle session is stale (deterministic)
    events: list[dict] = []
    sessions: dict[str, FakeAgentSession] = {}

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        s = FakeAgentSession(script=lambda t: [AgentEvent("turn_done", {})])
        sessions[project] = s
        return s

    host = agent_host.AgentHost(make_session=make_session, make_ctx=lambda p: _stub_ctx(p, events))

    async def scenario() -> set[str]:
        await host.post_turn("a", "hi")
        await _drain_until(events, lambda event: event["type"] == "turn_done")
        await host.post_turn("b", "hi")   # posting b sweeps the stale (idle) a first
        keys = set(host._workers)
        await host.aclose_all()
        return keys

    keys = asyncio.run(scenario())
    assert "a" not in keys                # a evicted as idle-stale
    assert sessions["a"].closed


def test_periodic_reaper_closes_idle_session_without_followup_turn(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_SESSION_TTL_S", 0.02)
    events: list[dict] = []
    sessions: list[FakeAgentSession] = []

    def make_session(project: str, ctx: agent_host.ToolCtx) -> FakeAgentSession:
        session = FakeAgentSession(script=lambda _text: [AgentEvent("turn_done", {})])
        sessions.append(session)
        return session

    host = agent_host.AgentHost(
        make_session=make_session,
        make_ctx=lambda project: _stub_ctx(project, events),
        reaper_interval_s=0.005,
    )

    async def scenario() -> None:
        await host.post_turn("project", "hello")
        await _drain_until(events, lambda event: event["type"] == "turn_done")
        for _ in range(100):
            if sessions[0].closed:
                break
            await asyncio.sleep(0.005)
        assert sessions[0].closed
        assert "project" not in host._workers
        await host.aclose_all()

    asyncio.run(scenario())


def test_periodic_reaper_does_not_close_busy_session(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_SESSION_TTL_S", 0.01)
    entered: "asyncio.Event | None" = None
    release: "asyncio.Event | None" = None
    closed = 0
    events: list[dict] = []

    class BlockingSession:
        async def aconnect(self) -> None:
            return None

        async def send_turn(self, text: str, emit) -> None:  # noqa: ANN001
            assert text == "hello"
            assert entered is not None and release is not None
            entered.set()
            await release.wait()
            await emit(AgentEvent("turn_done", {}))

        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

    host = agent_host.AgentHost(
        make_session=lambda _project, _ctx: BlockingSession(),
        make_ctx=lambda project: _stub_ctx(project, events),
        reaper_interval_s=0.003,
    )

    async def scenario() -> None:
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        await host.post_turn("project", "hello")
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0.04)
        assert closed == 0
        assert "project" in host._workers
        release.set()
        await _drain_until(events, lambda event: event["type"] == "turn_done")
        for _ in range(50):
            if closed:
                break
            await asyncio.sleep(0.02)
        assert closed == 1
        await host.aclose_all()
        await host.aclose_all()
        assert host._reaper is None

    asyncio.run(scenario())


def test_aclose_all_bounds_hanging_turn_and_closes_session() -> None:
    entered: "asyncio.Event | None" = None
    closed = 0

    class HangingSession:
        async def aconnect(self) -> None:
            return None

        async def send_turn(self, text: str, emit: Emit) -> None:
            del text, emit
            assert entered is not None
            entered.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

    host = agent_host.AgentHost(
        make_session=lambda _project, _ctx: HangingSession(),
        make_ctx=lambda project: _stub_ctx(project, []),
        shutdown_grace_s=0.01,
    )

    async def scenario() -> None:
        nonlocal entered
        entered = asyncio.Event()
        await host.post_turn("project", "hello")
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(host.aclose_all(), timeout=1)

    asyncio.run(scenario())
    assert closed == 1


# ── Describe: the copilot's one-shot structured completion, and its offline fallback ────────
# `_describe_by_copilot` is the only thing here that touches the SDK (lazily, inside itself), so every
# test below replaces it directly — never the real `claude_agent_sdk.query` — and stays SDK-free.

SMUG_SPEC = {"eyes": "side", "mouth": "flat"}


def test_describe_expression_uses_the_copilot_when_it_answers_cleanly(monkeypatch) -> None:
    async def fake_copilot(text: str, rig: str) -> dict:
        assert (text, rig) == ("smug, one eyebrow up", "lens")
        return {"name": "Smug", "spec": SMUG_SPEC}

    monkeypatch.setattr(agent_host, "_describe_by_copilot", fake_copilot)
    proposal = asyncio.run(agent_host.describe_expression("smug, one eyebrow up", "lens"))
    assert proposal == {"name": "Smug", "spec": SMUG_SPEC, "matched": [], "source": "copilot"}
    assert "claude_agent_sdk" not in sys.modules


def test_describe_expression_falls_back_to_keywords_on_a_copilot_exception(monkeypatch) -> None:
    async def broken(text: str, rig: str) -> dict:
        raise RuntimeError("not logged in")

    monkeypatch.setattr(agent_host, "_describe_by_copilot", broken)
    proposal = asyncio.run(agent_host.describe_expression("smug, one eyebrow up", "lens"))
    assert (proposal["source"], proposal["spec"]) == ("keywords", SMUG_SPEC)


def test_describe_expression_falls_back_to_keywords_on_a_timeout(monkeypatch) -> None:
    async def slow(text: str, rig: str) -> dict:
        await asyncio.sleep(10)
        return {"name": "Slow", "spec": SMUG_SPEC}

    monkeypatch.setattr(agent_host, "_describe_by_copilot", slow)
    monkeypatch.setattr(agent_host, "_DESCRIBE_TIMEOUT_S", 0.01)
    proposal = asyncio.run(agent_host.describe_expression("smug, one eyebrow up", "lens"))
    assert proposal["source"] == "keywords"


def test_describe_expression_falls_back_to_keywords_on_an_invalid_proposal(monkeypatch) -> None:
    async def bad_spec(text: str, rig: str) -> dict:
        return {"name": "Laser", "spec": {**SMUG_SPEC, "eyes": "laser"}}

    monkeypatch.setattr(agent_host, "_describe_by_copilot", bad_spec)
    assert asyncio.run(agent_host.describe_expression("smug, one eyebrow up", "lens"))["source"] == "keywords"

    async def bad_shape(text: str, rig: str) -> dict:
        return {"oops": True}

    monkeypatch.setattr(agent_host, "_describe_by_copilot", bad_shape)
    assert asyncio.run(agent_host.describe_expression("smug, one eyebrow up", "lens"))["source"] == "keywords"


def test_describe_expression_skips_the_copilot_for_invalid_text(monkeypatch) -> None:
    called = False

    async def unexpected(text: str, rig: str) -> dict:
        nonlocal called
        called = True
        return {"name": "X", "spec": SMUG_SPEC}

    monkeypatch.setattr(agent_host, "_describe_by_copilot", unexpected)
    try:
        asyncio.run(agent_host.describe_expression("   ", "lens"))
        raised = False
    except cast_mod.CastError:
        raised = True
    assert raised and not called


def test_describe_expression_is_rig_aware_for_guest(monkeypatch, client_rig: None) -> None:
    async def guest_copilot(text: str, rig: str) -> dict:
        assert rig == "sample-rig"
        return {"name": "Curious", "spec": {"brows": "raised", "eyes": "open", "mouth": "closed"}}

    monkeypatch.setattr(agent_host, "_describe_by_copilot", guest_copilot)
    proposal = asyncio.run(agent_host.describe_expression("raised brows", "sample-rig"))
    assert proposal == {
        "name": "Curious", "spec": {"brows": "raised", "eyes": "open", "mouth": "closed"},
        "matched": [], "source": "copilot",
    }


def test_describe_schema_and_prompt_are_built_from_rigs_at_call_time(client_rig: None) -> None:
    schema = agent_host._describe_schema("sample-rig")
    assert set(schema["properties"]["spec"]["properties"]) == set(cast_mod.RIGS["sample-rig"]["parts"])
    assert schema["properties"]["spec"]["properties"]["mouth"]["enum"] == list(cast_mod.RIGS["sample-rig"]["parts"]["mouth"])
    assert "closed" in agent_host._describe_prompt("an open mouth", "sample-rig")


# ── Draft: the copilot's one-shot script draft, offline-safe on anything short of a clean,
# parseable reply. `_draft_by_copilot` is the only thing here that touches the SDK, so every test
# below replaces it directly — never the real `claude_agent_sdk.query` — and stays SDK-free.

DRAFT_RAW = {
    "title": "A Simple Pasta Supper",
    "scenes": [
        {"role": "Hook", "title": "A quick supper",
         "body": "Have you ever cooked pasta with fresh garlic and a little lemon?"},
        {"role": "Wrap", "title": "Serve the pasta",
         "body": "Fresh lemon adds brightness. That is a simple supper to share."},
    ],
}


def test_draft_script_uses_the_copilot_when_it_answers_cleanly(monkeypatch) -> None:
    async def fake_copilot(idea: str, format_hint: str | None) -> dict:
        assert (idea, format_hint) == ("how to cook pasta", None)
        return DRAFT_RAW

    monkeypatch.setattr(agent_host, "_draft_by_copilot", fake_copilot)
    draft = asyncio.run(agent_host.draft_script("how to cook pasta"))
    assert draft["source"] == "copilot" and draft["title"] == "A Simple Pasta Supper"
    scenes = sv.parse_script(draft["script"])
    assert [s.role for s in scenes] == ["Hook", "Wrap"]
    assert scenes[0].title == "A quick supper" and scenes[1].title == "Serve the pasta"
    assert "claude_agent_sdk" not in sys.modules


def test_draft_script_is_unavailable_on_a_copilot_exception(monkeypatch) -> None:
    async def broken(idea: str, format_hint: str | None) -> dict:
        raise RuntimeError("not logged in")

    monkeypatch.setattr(agent_host, "_draft_by_copilot", broken)
    unavailable = False
    try:
        asyncio.run(agent_host.draft_script("an idea"))
    except agent_host.ScriptDraftUnavailable:
        unavailable = True
    assert unavailable


def test_draft_script_is_unavailable_on_a_timeout(monkeypatch) -> None:
    async def slow(idea: str, format_hint: str | None) -> dict:
        await asyncio.sleep(10)
        return DRAFT_RAW

    monkeypatch.setattr(agent_host, "_draft_by_copilot", slow)
    monkeypatch.setattr(agent_host, "_DRAFT_TIMEOUT_S", 0.01)
    unavailable = False
    try:
        asyncio.run(agent_host.draft_script("an idea"))
    except agent_host.ScriptDraftUnavailable:
        unavailable = True
    assert unavailable


def test_draft_script_is_unavailable_when_the_reply_does_not_render_into_scenes(monkeypatch) -> None:
    async def empty(idea: str, format_hint: str | None) -> dict:
        return {"title": "Nothing", "scenes": []}

    monkeypatch.setattr(agent_host, "_draft_by_copilot", empty)
    unavailable = False
    try:
        asyncio.run(agent_host.draft_script("an idea"))
    except agent_host.ScriptDraftUnavailable:
        unavailable = True
    assert unavailable

    async def blank_bodies(idea: str, format_hint: str | None) -> dict:
        return {"title": "Nothing", "scenes": [{"role": "Hook", "title": "x", "body": "   "}]}

    monkeypatch.setattr(agent_host, "_draft_by_copilot", blank_bodies)
    unavailable = False
    try:
        asyncio.run(agent_host.draft_script("an idea"))
    except agent_host.ScriptDraftUnavailable:
        unavailable = True
    assert unavailable


def test_draft_script_skips_the_copilot_for_an_empty_idea(monkeypatch) -> None:
    called = False

    async def unexpected(idea: str, format_hint: str | None) -> dict:
        nonlocal called
        called = True
        return DRAFT_RAW

    monkeypatch.setattr(agent_host, "_draft_by_copilot", unexpected)
    raised = False
    try:
        asyncio.run(agent_host.draft_script("   "))
    except agent_host.ScriptDraftError:
        raised = True
    assert raised and not called


def test_draft_script_falls_back_an_unknown_role_to_beat(monkeypatch) -> None:
    async def odd_role(idea: str, format_hint: str | None) -> dict:
        return {
            "title": "X",
            "scenes": [
                {"role": "Nonsense", "title": "One", "body": "First scene words go here."},
                {"role": "Wrap", "title": "Two", "body": "Last scene words go here."},
            ],
        }

    monkeypatch.setattr(agent_host, "_draft_by_copilot", odd_role)
    draft = asyncio.run(agent_host.draft_script("an idea"))
    scenes = sv.parse_script(draft["script"])
    assert scenes[0].role == "Beat"


def test_draft_schema_and_prompt_use_the_scene_roles() -> None:
    schema = agent_host._draft_schema()
    scene_props = schema["properties"]["scenes"]["items"]["properties"]
    assert set(scene_props["role"]["enum"]) == set(project_mod.SCENE_ROLES)
    assert "a lemon pasta recipe" in agent_host._draft_prompt("a lemon pasta recipe", None)


def test_agent_host_slow_connect_cancellation_drains_all_turns_and_closes() -> None:
    wire: list[dict] = []
    connect_started = asyncio.Event()
    never = asyncio.Event()

    class SlowConnectSession:
        def __init__(self) -> None:
            self.closed = False

        async def aconnect(self) -> None:
            connect_started.set()
            await never.wait()

        async def send_turn(self, text: str, emit: Emit) -> None:
            raise AssertionError("cancelled connect must not execute a turn")

        async def aclose(self) -> None:
            self.closed = True

    session = SlowConnectSession()

    def make_ctx(slug: str) -> agent_host.ToolCtx:
        return agent_host.ToolCtx(
            slug=slug, read_doc=lambda: (None, 0), read_transcript=lambda: None, fps=lambda: 30,
            emit=wire.append,
        )

    async def scenario() -> None:
        host = agent_host.AgentHost(make_session=lambda _p, _c: session, make_ctx=make_ctx)
        await host.post_turn("project", "one")
        await host.post_turn("project", "two")
        await host.post_turn("project", "three")
        await connect_started.wait()
        task = host._workers["project"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await host.aclose_all()

    asyncio.run(scenario())
    assert session.closed
    assert wire == [
        {"type": "agent_error", "kind": "runtime", "message": "turn_cancelled"},
        {"type": "turn_done"},
    ] * 3
