r"""The embedded Claude co-editor host.

This is the ONLY module that touches `claude-agent-sdk`, and it imports it **lazily inside functions**
(mirroring studio's `import jsonpatch` discipline) so the studio boots — and every non-chat route
works — on a box without the SDK or without an API key. The module top imports stdlib + config +
the three pure `pipeline` modules + the SDK-free seam only.

Architecture:
  - `ToolCtx` — the studio injects per-project readers + an `emit(dict)` (hub.publish_event).
  - Editor tools — thin `@tool` adapters over the pure builders in `pipeline.agent_tools` /
                    `pipeline.proposals` / `pipeline.notes`; each returns a compact JSON string.
  - `SdkAgentSession` — the real `AgentSession`, owns one long-lived `ClaudeSDKClient`;
                    tests inject `FakeAgentSession` to avoid live SDK calls.
  - `AgentHost` — per-project actor: one asyncio worker owns the session and serializes turns; the
                    studio route just enqueues via `post_turn`. Auth/connect failures surface as an
                    `agent_error` event, never an exception into the request.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import config
from pipeline.agent_providers import ProviderAdapter, registry_from_config
from pipeline import addons, agent_credentials, agent_tools, bridge_token, channels, copilot, notes, proposals
from pipeline import cast as cast_mod
from pipeline import project as project_mod
from pipeline import script_video as script_video_mod
from pipeline import staging
from pipeline.agent_session import AgentEvent, AgentSession
from pipeline.errors import ValidationError
from studio.errors import agent_error_data

# The editor tool names as they appear on the wire (mcp__<serverkey>__<tool>). The server key is the
# `mcp_servers` dict key ("editor"), NOT the create_sdk_mcp_server name — keep them equal to avoid drift.
_EDITOR_TOOLS = (
    "get_project", "propose_patch", "propose_options", "read_transcript", "find_word",
    "get_channel",
    "read_notes", "read_preferences", "append_note",
    "read_story", *copilot.TOOLS,
)
_ALLOWED_EDITOR = tuple(f"mcp__editor__{t}" for t in _EDITOR_TOOLS)
# In "plan" mode the co-editor is read-only: it may inspect the doc/transcript but proposes/renders
# nothing. These are the editor tools that write nothing durable and apply nothing.
_READONLY_EDITOR = frozenset(f"mcp__editor__{t}" for t in (
    "get_project", "read_transcript", "find_word", "get_channel",
    "read_notes", "read_preferences", "read_story",
))

# Compute verbs live in a separate stdio server (scripts/mcp_gpu.py), a loopback bridge to the
# studio's job registry.
_GPU_TOOLS = (
    "transcribe", "make_proxy", "detect_silence",
    "render_final", "render_preview_segment", "get_job",
)
_ALLOWED_GPU = tuple(f"mcp__gpu__{t}" for t in _GPU_TOOLS)
# Built-in names the bundled CLI in claude-agent-sdk 0.2.115 recognises (it warns on unknown entries).
# tools=[] removes built-ins; this explicit deny list also prevents permission pre-approval.
_BUILTIN_TOOLS = (
    "Agent", "Artifact", "AskUserQuestion", "Bash", "BashOutput", "CronCreate", "CronDelete", "CronList",
    "DesignSync", "Edit", "EndConversation", "EnterPlanMode", "EnterWorktree", "ExitPlanMode", "ExitWorktree",
    "Glob", "Grep", "LSP", "ListConnectors", "ListMcpResourcesTool", "Monitor", "NotebookEdit", "PowerShell",
    "Projects", "PushNotification", "REPL", "Read", "ReadMcpResourceDirTool", "ReadMcpResourceTool",
    "RemoteTrigger", "ReportFindings", "ScheduleWakeup", "SearchMcpRegistry", "SendMessage", "SendUserFile",
    "SendUserMessage", "ShareOnboardingGuide", "ShowOnboardingRolePicker", "Skill", "SuggestConnectors", "Task",
    "TaskCreate", "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TaskUpdate", "TodoWrite", "ToolSearch",
    "WaitForMcpServers", "WebFetch", "WebSearch", "Workflow", "Write", "KillShell", "AgentOutput",
    "AgentOutputTool", "BashOutputTool", "Brief", "KillBash", "ListMcpResources", "ListPlugins", "ListSkills",
    "ReadMcpResource", "ReadMcpResourceDir", "SearchPlugins", "SearchSkills", "SuggestPluginInstall",
    "SuggestSkills",
)
_STUDIO_SECRET_ENV = (
    "PEXELS_API_KEY", "PIXABAY_API_KEY",
    "CUTGRAPH_INTERNAL_TOKEN",
)
# render tool-results the SdkAgentSession surfaces to the UI as `job` chips (+ Approve for render_final).
_GPU_JOB_TOOLS = frozenset({"mcp__gpu__render_final", "mcp__gpu__render_preview_segment"})


_PERSONA_PATH = config.CODE_ROOT / "pipeline" / "prompts" / "copilot.md"

# Field for field what pipeline.proposals.write validates, so a schema-conforming call is accepted first time.
_PATCH_OPS = {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
    "op": {"type": "string", "enum": ["add", "remove", "replace", "move", "copy", "test"]},
    "path": {"type": "string", "description": "JSON Pointer into the project, e.g. /tracks/0/clips/3"},
    "from": {"type": "string"}, "value": {},
}, "required": ["op", "path"]}}
_PROPOSE_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "description": "short; becomes the undo label"},
        "rationale": {"type": "string", "minLength": 1, "description": "plain-language reason, shown to the human"},
        "intentGroups": {
            "type": "array", "minItems": 1,
            "description": 'the ops grouped by named intent, e.g. [{"id":"trim-intro","intent":"Trim the slow '
                           'intro","ops":[{"op":"remove","path":"/tracks/0/clips/0"}]}]',
            "items": {"type": "object", "properties": {
                "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$", "description": "unique in this proposal"},
                "intent": {"type": "string", "minLength": 1},
                "ops": _PATCH_OPS,
            }, "required": ["id", "intent", "ops"]},
        },
        "citations": {
            "type": "array", "minItems": 1,
            "description": "transcript spans that support the edit, in transcript seconds (the tSec/startSec "
                           "values find_word and read_story return), e.g. "
                           '[{"kind":"transcript","start":12.4,"end":15.0,"summary":"the speaker restarts the sentence"}]',
            "items": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["transcript"]},
                "start": {"type": "number", "minimum": 0, "description": "span start, seconds"},
                "end": {"type": "number", "exclusiveMinimum": 0, "description": "span end, seconds; after start"},
                "summary": {"type": "string", "minLength": 1, "description": "what the span shows, one line"},
            }, "required": ["kind", "start", "end", "summary"]},
        },
        "material": {"type": "boolean", "description": "true for a material choice; then preview is required"},
        "preview": {"type": "object", "properties": {
            "startF": {"type": "integer", "minimum": 0, "description": "timeline frame"},
            "endF": {"type": "integer", "minimum": 1, "description": "timeline frame; after startF"},
            "previewRef": {"type": "string", "pattern": r"^/media/preview/[A-Za-z0-9_-]+\.mp4$",
                           "description": "the output render_preview_segment returned"},
        }, "required": ["startF", "endF", "previewRef"]},
        "savesSec": {"type": "number", "description": "seconds the edit removes, when it tightens"},
    },
    "required": ["title", "rationale", "intentGroups", "citations"],
}


def _do_propose_patch(ctx: "ToolCtx", args: dict) -> str:
    """The propose_patch body, factored out of the SDK `@tool` wrapper so it is testable WITHOUT the
    SDK. Writes a guarded proposal against the CURRENT doc and emits the `proposal` event
    carrying the REAL (guarded) ops (the old emit omitted ops, so a live-session accept PATCHed an
    empty op list and silently applied NOTHING while recording the card as accepted)."""
    args = agent_credentials.redact_data(args)
    doc, revision = ctx.read_doc()
    try:
        proposal = proposals.write(
            ctx.slug, ops=args.get("ops") or [], title=args.get("title", ""), rationale=args.get("rationale", ""),
            base_revision=revision, doc=doc, saves_sec=args.get("savesSec"), intent_groups=args.get("intentGroups"),
            citations=args.get("citations"), material=bool(args.get("material")), preview=args.get("preview"),
            require_evidence="citations" in args,
        )
    except ValidationError as e:
        # The message names the offending field (e.g. citations[0].end) so the model can fix it in one retry.
        return json.dumps({"error": str(e)}, separators=(",", ":"))
    ctx.emit(proposals.proposal_event(proposal))
    return json.dumps({"id": proposal["id"]}, separators=(",", ":"))


def _do_read_story(ctx: "ToolCtx", cursor: int = 0) -> str:
    doc, revision = ctx.read_doc()
    if doc is None:
        return json.dumps({"available": False, "reason": "no project"}, separators=(",", ":"))
    return copilot.story_outline(doc, revision, ctx.read_transcript(), cursor,
                                 ctx.read_library(doc.get("workspaceId")))


def _do_copilot(ctx: "ToolCtx", tool: str, args: dict) -> str:
    """One Story tool: build its proposal against the CURRENT doc + transcript, persist it as a
    pending proposal and emit the `proposal` event. Returns {id,title,changes,savesSec?}, a no-op
    {proposed:false,reason}, or {error} for the agent to revise; it never applies anything."""
    args = agent_credentials.redact_data(args)
    doc, revision = ctx.read_doc()
    if doc is None:
        return json.dumps({"error": "no project"}, separators=(",", ":"))
    try:
        built = copilot.propose(tool, doc, ctx.read_transcript(), args, ctx.read_library)
    except copilot.NothingToPropose as e:
        return json.dumps({"proposed": False, "reason": str(e)}, separators=(",", ":"))
    except copilot.ToolError as e:
        return json.dumps({"error": str(e)}, separators=(",", ":"))
    # doc=None: these ops are built server-side against exactly this revision and carry their own id
    # tests. The store's derived guards read the PRE-edit doc, which is wrong for a multi-step scene
    # plan whose later ops address indices an earlier split or merge already shifted.
    proposal = proposals.write(
        ctx.slug, ops=built.ops, title=built.title, rationale=built.rationale, base_revision=revision,
        saves_sec=built.saves_sec, marks=built.marks,
    )
    ctx.emit(proposals.proposal_event(proposal))
    return json.dumps({"id": proposal["id"], **built.result()}, separators=(",", ":"))


def _classify_assistant_error(err: object) -> str:
    """`AssistantMessage.error` is an `AssistantMessageError` LITERAL STRING, not an object (SDK
    types.py:1005-1012) — so the old `getattr(err,"type")` read never matched and every auth failure
    was mislabelled `runtime`, killing the connection banner. Classify the string directly."""
    s = str(err or "")
    if s == "authentication_failed":
        return "auth"
    if s == "billing_error":
        return "billing"
    return "runtime"


def _no_channel() -> "str | None":
    """Default `channel`: a ctx built without a channel reader has no channel profile."""
    return None


def _no_preferences(scope: str) -> dict:
    return {"scope": scope, "events": []}


@dataclass(frozen=True)
class AgentSettings:
    """The selected provider/profile plus co-editor knobs, validated at the registry boundary.

    The running implementation remains the Claude SDK adapter. Frozen + value-equal settings let
    AgentHost rebuild a warm session when a validated selection changes; unavailable providers fail
    closed to Claude's configured default rather than reaching that adapter with foreign settings.
    """

    provider_id: str = "claude"
    profile_id: str = "default"
    model: "str | None" = None       # None ⇒ config.AGENT_MODEL (which is None ⇒ SDK default)
    effort: "str | None" = None      # None ⇒ SDK default reasoning effort
    mode: str = "default"            # "default" | "plan"

    @classmethod
    def from_request(cls, body: dict, *, registry=None) -> "AgentSettings":  # noqa: ANN001
        selection = (registry or registry_from_config(config)).normalize(body)
        return cls(provider_id=selection.provider_id, profile_id=selection.profile_id,
                   model=selection.model_id, effort=selection.effort, mode=selection.mode)


@dataclass
class ToolCtx:
    """Per-project injection point. The studio builds one of these so the in-process tools read the
    current doc/transcript and emit events onto the project's SSE queue — all without agent_host
    knowing anything about FastAPI, the Hub, or the on-disk layout."""

    slug: str
    read_doc: Callable[[], "tuple[dict | None, int]"]      # -> (doc, revision); missing ⇒ (None, 0)
    read_transcript: Callable[[], "dict | None"]           # reshaped {words,segments} or None
    fps: Callable[[], int]
    emit: Callable[[dict], None]                            # hub.publish_event(slug, msg)
    channel: Callable[[], "str | None"] = _no_channel        # the project doc's channel field or None
    read_preferences: Callable[[str], dict] = _no_preferences
    read_library: Callable[["str | None"], list[dict]] = copilot.library_assets  # workspace + brand assets
    settings: AgentSettings = field(default_factory=AgentSettings)  # co-editor knobs; AgentHost sets


def load_persona() -> str:
    """The main system prompt — the editor-in-the-room persona (system_prompt=<str>,
    NOT an AgentDefinition subagent)."""
    return _PERSONA_PATH.read_text(encoding="utf-8")


# ── In-process tools (thin adapters; all logic is in the pure pipeline modules) ────────────
def build_editor_server(ctx: ToolCtx):  # noqa: ANN201 — SDK type is lazy
    """Create the in-process ("editor") MCP server. Lazy SDK import so this is only reached on the
    SDK session path (build_options); the tool LOGIC it wraps is unit-tested via the pure builders."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    note_writer = notes.AgentNoteWriter(ctx.slug)

    def _text(payload_str: str, *, is_error: bool = False) -> dict:
        block: dict[str, Any] = {"content": [{"type": "text", "text": payload_str}]}
        if is_error:
            block["is_error"] = True
        return block

    @tool(
        "get_project",
        "Project skeleton (ids+frames+anchors). No args ⇒ a compact OVERVIEW (per-track "
        "count+first+last). Pass track=<id> (+ optional cursor) to page that track's full clip/item "
        "list; a page carries nextCursor when more remains. Track index = position in tracks; clip "
        "index = cursor + position.",
        {"type": "object", "properties": {
            "track": {"type": "string"}, "cursor": {"type": "integer"},
        }},
    )
    async def get_project(args: dict) -> dict:
        doc, revision = ctx.read_doc()
        if doc is None:
            return _text(json.dumps({"revision": 0, "tracks": []}, separators=(",", ":")))
        track = args.get("track")
        cursor = int(args.get("cursor", 0) or 0)
        return _text(agent_tools.project_skeleton(doc, revision, track=track, cursor=cursor))

    @tool(
        "read_transcript",
        "Compact transcript. granularity 'segments' (default, paginated — pass cursor for the next "
        "page via nextCursor) or 'words' within a bounded [start,end).",
        {"type": "object", "properties": {
            "granularity": {"type": "string", "enum": ["segments", "words"]},
            "start": {"type": "integer"}, "end": {"type": "integer"},
            "cursor": {"type": "integer"},
        }},
    )
    async def read_transcript(args: dict) -> dict:
        tdoc = ctx.read_transcript()
        if tdoc is None:
            return _text(json.dumps({"error": "no transcript"}, separators=(",", ":")), is_error=True)
        gran = args.get("granularity", "segments")
        rng = None
        if "start" in args and "end" in args:
            rng = (int(args["start"]), int(args["end"]))
        cursor = int(args.get("cursor", 0) or 0)
        return _text(agent_tools.read_transcript(tdoc, gran, rng, cursor))

    @tool("find_word", "Locate a phrase; up to 20 hits {wordIdx,tSec,frame,context}.", {"phrase": str})
    async def find_word(args: dict) -> dict:
        tdoc = ctx.read_transcript()
        if tdoc is None:
            return _text(json.dumps({"error": "no transcript"}, separators=(",", ":")), is_error=True)
        return _text(agent_tools.find_word(tdoc, args["phrase"], ctx.fps()))


    @tool(
        "get_channel",
        "The video's channel profile {name,voice,audience,accentColor}; match its configured voice "
        "and audience. {available:false} when the video has no channel set.",
        {"type": "object", "properties": {}},
    )
    async def get_channel(args: dict) -> dict:
        return _text(agent_tools.channel_profile(channels.load(), ctx.channel()))

    @tool(
        "propose_patch",
        "Write a reviewable RFC-6902 patch proposal (never applied directly). Group ops by named intent; "
        "cite transcript spans in seconds to support the edit. "
        "For a material choice, first call render_preview_segment and supply its /media/preview ref. "
        "Accepting remains one reversible editor undo checkpoint. Returns {id}, or {error} naming the field to fix.",
        _PROPOSE_PATCH_SCHEMA,
    )
    async def propose_patch(args: dict) -> dict:
        return _text(_do_propose_patch(ctx, args))

    @tool(
        "propose_options",
        "Offer a judge/choose gallery (hooks, captions, styles) the human picks from; writes nothing "
        "durable and applies nothing — you await the pick as your next turn. Each option is "
        "{label, description?, previewRef?} where previewRef is a /media/preview/<id>.mp4 or a spec.",
        {"type": "object",
         "properties": {"kind": {"type": "string"}, "options": {"type": "array"}},
         "required": ["kind", "options"]},
    )
    async def propose_options(args: dict) -> dict:
        kind = str(args["kind"])
        options = args.get("options") or []
        ctx.emit({"type": "options", "kind": kind, "options": options})
        return _text(agent_tools.options_labels(kind, options))

    @tool(
        "read_story",
        "The Story map the Story tools address: scenes {id,role,title,words:[start,end),shot}, the "
        "library suggest_broll picks from, and segment rows [wordIdx,startSec,text] ({…} = already cut). "
        "Paginated: pass cursor=nextCursor. Prefer it to read_transcript for Story work.",
        {"type": "object", "properties": {"cursor": {"type": "integer"}}},
    )
    async def read_story(args: dict) -> dict:
        return _text(_do_read_story(ctx, int(args.get("cursor", 0) or 0)))

    reason = {"type": "string", "description": "one line, shown to the human"}
    word = {"type": "integer", "minimum": 0}

    @tool(
        "plan_scenes",
        "Propose scene edits, applied in order as one undo step: split a scene at a word (the new scene "
        "follows it), merge a scene with the next one, rename, or change its role. Proposal only.",
        {"type": "object", "properties": {
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "op": {"type": "string", "enum": ["split", "merge", "rename", "role"]},
                "scene": {"type": "string"}, "word": word, "title": {"type": "string"},
                "role": {"type": "string", "enum": list(project_mod.SCENE_ROLES)},
            }, "required": ["op", "scene"]}},
            "title": {"type": "string"}, "why": reason,
        }, "required": ["edits"]},
    )
    async def plan_scenes(args: dict) -> dict:
        return _text(_do_copilot(ctx, "plan_scenes", args))

    @tool(
        "cut_fillers",
        "Propose ripple cuts of filler words and false starts. Without ranges it detects um/uh, "
        "'you know,', 'I mean,', 'like,', cut-off words and repeats (in scene=<id> or everywhere). Or "
        "pass the ranges you judged to be false starts or redundant (endWordIdx inclusive, <=80 words).",
        {"type": "object", "properties": {
            "scene": {"type": "string"},
            "ranges": {"type": "array", "items": {"type": "object", "properties": {
                "wordIdx": word, "endWordIdx": word, "reason": {"type": "string"},
            }, "required": ["wordIdx"]}},
            "title": {"type": "string"}, "why": reason,
        }},
    )
    async def cut_fillers(args: dict) -> dict:
        return _text(_do_copilot(ctx, "cut_fillers", args))

    @tool(
        "suggest_broll",
        "Propose cues anchored to words from the video's workspace + brand library, placed like a "
        "drawer drop (video/image on a video track, music ducked on an audio track). `asset` names a "
        "library item; otherwise the best name/tag match for `query` (or the words) is picked.",
        {"type": "object", "properties": {
            "cues": {"type": "array", "items": {"type": "object", "properties": {
                "wordIdx": word, "endWordIdx": word,
                "kind": {"type": "string", "enum": ["broll", "music", "sfx"]},
                "query": {"type": "string"}, "asset": {"type": "string"},
            }, "required": ["wordIdx"]}},
            "title": {"type": "string"}, "why": reason,
        }, "required": ["cues"]},
    )
    async def suggest_broll(args: dict) -> dict:
        return _text(_do_copilot(ctx, "suggest_broll", args))

    look = {
        "character": {"type": "string", "description": "a character id from the video's cast"},
        "pose": {"type": "string", "enum": list(copilot.POSES)},
        "expression": {"type": "string", "description": "a built-in or custom expression of that character"},
        "move": {"type": "string", "enum": list(copilot.MOVES)},
        "bubble": {"type": "string", "enum": list(copilot.BUBBLES),
                   "description": "a thought, speech, '!', '?' or exclaims (two simultaneous neutral '!!' marks) over that character, "
                                  "popping in on cueWord"},
    }

    # What the short-film ids do, beside their enums.
    SHOT_ID_NOTES = {
        "camera": "bounded centre-framed push-in and independently framed pull-out; pan moves the stage from the left",
        "backdrop": "vision is a generic bright radial backdrop",
        "headlineAt": "costar: a duo's or an ensemble's headline over the co-star",
        "diagram": "grid, chain, radial, tree and list are independent content-sized boards; 1-6 labels, blank nodes when unlabelled",
    }

    @tool(
        "set_shot",
        "Propose scene shots, merged over each scene's current shot (null clears a field). mode: "
        "footage (default), animation (full-frame scene) or cameo (the character over the footage); pose, "
        "expression and move are the character's own. costar adds a second character to an animation; "
        "its interaction plays on cueWord.",
        {"type": "object", "properties": {
            "shots": {"type": "array", "items": {"type": "object", "properties": {
                "scene": {"type": "string"},
                **{key: {"type": "string", "enum": list(ids), **({"description": SHOT_ID_NOTES[key]} if key in SHOT_ID_NOTES else {})}
                   for key, ids in copilot.SHOT_IDS.items()},
                **look,
                "headline": {"type": "string"},
                "hideHeadline": {"type": "boolean",
                                 "description": "animation only: true draws no headline or eyebrow on the stage, a duo or "
                                                "an ensemble (a screen, title or sign-off layout ignores it)"},
                "cueWord": word,
                "costar": {"type": ["object", "null"], "properties": look},
                "ensemble": {"type": ["array", "null"], "maxItems": copilot.ENSEMBLE_MAX, "items": {"type": "object", "properties": look},
                             "description": "more characters after the lead and costar in an animation; replaces the line-up"},
                **staging.TOOL_PROPS,
            }, "required": ["scene"]}},
            "title": {"type": "string"}, "why": reason,
        }, "required": ["shots"]},
    )
    async def set_shot(args: dict) -> dict:
        return _text(_do_copilot(ctx, "set_shot", args))

    @tool("read_notes", "Read the project's NOTES.md decision log (newest entries that fit).", {})
    async def read_notes(args: dict) -> dict:
        return _text(agent_tools.notes_tail(notes.read(ctx.slug)))

    @tool("read_preferences", "Read compact LOCAL decision outcomes for scope 'project' or 'channel'. "
          "It returns editorial intent/outcome only — never footage, frames, or transcript text.",
          {"type": "object", "properties": {"scope": {"type": "string", "enum": ["project", "channel"]}}, "required": ["scope"]})
    async def read_preferences(args: dict) -> dict:
        return _text(agent_tools.preference_summary(ctx.read_preferences(str(args["scope"]))))

    @tool("append_note", "Append a decision to NOTES.md (title + body).", {"title": str, "body": str})
    async def append_note(args: dict) -> dict:
        args = agent_credentials.redact_data(args)
        try:
            note_writer.append(args["title"], args["body"])
        except ValueError as e:
            return _text(json.dumps({"error": str(e)}, separators=(",", ":")), is_error=True)
        return _text(json.dumps({"ok": True}, separators=(",", ":")))

    return create_sdk_mcp_server(
        "editor", "1.0.0",
        tools=[get_project, read_transcript, find_word, get_channel,
               propose_patch, propose_options, read_notes, read_preferences, append_note,
               read_story, plan_scenes, cut_fillers, suggest_broll, set_shot],
    )


def _make_can_use_tool(mode: str):  # noqa: ANN202 — returns the SDK's lazy callback type
    """Defense in depth for calls that the SDK options have not already approved."""
    allowed = _allowed_tools(mode)

    async def can_use_tool(name: str, inp: dict, context: Any):  # noqa: ANN202 — SDK type is lazy
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if name in allowed:
            return PermissionResultAllow()
        if mode == "plan":
            return PermissionResultDeny(
                message=f"{name} is unavailable in plan mode — switch the co-editor to Default to act")
        return PermissionResultDeny(message=f"{name} is not permitted for the editor co-pilot")

    return can_use_tool


def _allowed_tools(mode: str) -> tuple[str, ...]:
    if mode == "plan":
        return tuple(name for name in _ALLOWED_EDITOR if name in _READONLY_EDITOR)
    if mode == "default":
        return _ALLOWED_EDITOR + _ALLOWED_GPU
    raise ValueError("unsupported co-pilot permission mode")


def _sdk_environment(key: str | None = None) -> dict[str, str]:
    # The pinned SDK's version probe otherwise launches the CLI with the parent environment.
    os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"
    return {
        **dict.fromkeys(_STUDIO_SECRET_ENV, ""),
        **agent_credentials.sdk_environment(key),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


def _bundled_cli() -> str:
    import claude_agent_sdk

    directory = Path(claude_agent_sdk.__file__).parent
    executable = directory / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")
    if not executable.is_file():
        raise addons.AssistantUnavailable(addons.ASSISTANT_INCOMPLETE)
    return str(executable)


def build_options(project_dir: Path, ctx: ToolCtx, key: str | None = None):  # noqa: ANN201 — SDK type is lazy
    """Assemble the ClaudeAgentOptions for a locked-down co-editor: persona as the main system prompt,
    explicit MCP tools only, deny-by-default permission callback, no filesystem
    settings. model/effort/permission_mode come from ctx.settings — frozen at connect, so AgentHost
    rebuilds the session when they change. Requires the optional SDK."""
    settings = ctx.settings
    allowed = _allowed_tools(settings.mode)
    env = _sdk_environment(key)
    from claude_agent_sdk import ClaudeAgentOptions

    # Compute stdio server: bridges to the studio's job registry over loopback HTTP. The studio's own
    # token is given only to the GPU server; CUTGRAPH_PROJECT scopes every job to this session's slug.
    token_file = bridge_token.create()
    gpu_env = {"CUTGRAPH_STUDIO_URL": os.environ.get("CUTGRAPH_STUDIO_URL", "http://127.0.0.1:8765"),
               "CUTGRAPH_STATE_HOME": str(config.STATE_HOME),
               "CUTGRAPH_PROJECT": ctx.slug, "CUTGRAPH_INTERNAL_TOKEN_FILE": str(token_file),
               "ANTHROPIC_API_KEY": ""}
    gpu_command = [sys.executable, str(config.CODE_ROOT / "scripts" / "mcp_gpu.py")]

    kwargs: dict = {
        "system_prompt": load_persona(),
        "cwd": str(project_dir),
        "tools": [],
        "mcp_servers": {
            "editor": build_editor_server(ctx),
            "gpu": {"type": "stdio", "command": gpu_command[0],
                    "args": gpu_command[1:], "env": gpu_env},
        },
        "allowed_tools": list(allowed),
        "disallowed_tools": list(_BUILTIN_TOOLS),
        "permission_mode": settings.mode,
        "can_use_tool": _make_can_use_tool(settings.mode),
        "setting_sources": [],
        "strict_mcp_config": True,
        # The SDK merges the parent environment, so an allowlist cannot remove inherited secrets.
        "env": env,
        "cli_path": _bundled_cli(),
    }
    model = settings.model or config.AGENT_MODEL
    if model:
        kwargs["model"] = model
    if settings.effort:
        kwargs["effort"] = settings.effort
    return ClaudeAgentOptions(**kwargs)


def _tool_result_json(content: Any) -> dict:
    """A ToolResultBlock's content is a str or a list of content blocks; the GPU bridge always returns a
    compact JSON string in one text block. Extract it and parse; {} on any shape we don't recognize."""
    text: str | None = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                break
            inner = getattr(block, "text", None)
            if isinstance(inner, str):
                text = inner
                break
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _job_event(kind: str, content: Any) -> dict:
    """Map a GPU render tool-result into a `job` chip payload. render_final returns {jobId,status};
    render_preview_segment returns {output} (inline, already done). Falls back to the output path as id
    when there is no jobId so the UI reducer (which keys by id) still registers the chip."""
    data = _tool_result_json(content)
    job_id = str(data.get("jobId") or data.get("output") or "")
    status = str(data.get("status") or ("done" if data.get("output") else "queued"))
    event: dict = {"kind": kind, "id": job_id, "status": status}
    if data.get("output"):
        event["output"] = data["output"]
    return event


# ── Describe: a model-backed one-shot behind Cast → Describe ────────────────────────────────
# Cast → Describe's copilot path: a single tool-less structured completion through the SDK's `query()`
# (never the warm `ClaudeSDKClient` a project's AgentHost owns, and never a Story tool), constrained by
# `output_format` to the rig's own spec space so the reply IS the proposal, not prose to parse. Any
# rig, not just the default rig — `_describe_schema`/`_describe_prompt` are built from `RIGS[rig]` at call time.

_DESCRIBE_TIMEOUT_S = 20.0
_DESCRIBE_TEXT_MAX = 200  # matches pipeline.cast's own bound on a description's length


def _describe_schema(rig: str) -> dict:
    parts = cast_mod.RIGS[rig]["parts"]
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "a short 1-3 word label for the look"},
            "spec": {
                "type": "object",
                "properties": {part: {"type": "string", "enum": list(options)} for part, options in parts.items()},
                "required": list(parts),
                "additionalProperties": False,
            },
        },
        "required": ["name", "spec"],
        "additionalProperties": False,
    }


def _describe_prompt(text: str, rig: str) -> str:
    parts = cast_mod.RIGS[rig]["parts"]
    space = "; ".join(f"{part}: {', '.join(options)}" for part, options in parts.items())
    builtins = ", ".join(cast_mod.RIGS[rig]["expressions"])
    return (
        f"A character's face is built from independent parts, each with a fixed set of options — {space}. "
        f"Its built-in expressions, for reference only, are: {builtins}. Propose one option per part, and "
        f"a short 1-3 word name for the look, for this description: {text!r}"
    )


async def _describe_by_copilot(text: str, rig: str) -> dict:
    """One `query()` turn, lazily importing the SDK (invariant: this module is the only one that
    touches it). Raises on anything short of a clean structured reply — auth, billing, a runtime
    error, or the session ending without a result — for `describe_expression` to fall back on."""
    env = _sdk_environment()
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    options = ClaudeAgentOptions(
        env=env,
        cli_path=_bundled_cli(),
        system_prompt="Choose facial expression parts for an animated character rig. Answer only the "
                      "requested structured output; never call a tool.",
        tools=[], allowed_tools=[], mcp_servers={}, strict_mcp_config=True, setting_sources=[],
        permission_mode="default", max_turns=1,
        output_format={"type": "json_schema", "schema": _describe_schema(rig)},
    )
    if config.AGENT_MODEL:
        options.model = config.AGENT_MODEL
    # query() is an async generator wearing an AsyncIterator return type; cast it back so a ResultMessage
    # returning early can aclose() it ourselves, in this same task, rather than leave that to event-loop
    # teardown later (which warns: "aclose(): asynchronous generator is already running").
    messages = cast(AsyncGenerator, query(prompt=_describe_prompt(agent_credentials.redact(text), rig), options=options))
    try:
        async for message in messages:
            if isinstance(message, ResultMessage):
                if message.is_error or not isinstance(message.structured_output, dict):
                    raise RuntimeError(message.result or "describe: no structured output")
                return agent_credentials.redact_data(message.structured_output)
        raise RuntimeError("describe: the session ended without a result")
    finally:
        await messages.aclose()


async def describe_expression(text: object, rig: str) -> dict:
    """Cast → Describe: the co-pilot's proposal for `text` on `rig`, shaped and validated like
    the deterministic path (`pipeline.cast.describe`). Falls back to it — offline, always — on
    anything that keeps the model from a clean answer: no SDK, no API key, a timeout (~20s), or an
    invalid proposal (a name or spec that doesn't fit the rig)."""
    if addons.available("assistant") and isinstance(text, str) and text.strip():
        try:
            agent_credentials.resolve_key()
            text = agent_credentials.redact(text)
            raw = await asyncio.wait_for(_describe_by_copilot(text.strip()[:_DESCRIBE_TEXT_MAX], rig),
                                         timeout=_DESCRIBE_TIMEOUT_S)
            return cast_mod.validate_description(agent_credentials.redact_data(raw), rig, "copilot")
        except Exception:
            pass
    return cast_mod.describe(text, rig)


# ── Draft: a model-backed one-shot behind Home → "From an idea" → "✦ Draft it" ──────────────
# Same shape as Describe above: a single tool-less structured completion through the SDK's `query()`,
# constrained by `output_format` to {title, scenes:[{role,title,body}]}. The reply is rendered into
# `## Role: Title` markdown — pipeline.script_video.parse_script's own syntax — and re-parsed with
# that SAME function before it is trusted, so a clean reply always lands as valid scenes. Unlike
# Describe there is no offline describer to fall back to: a script is too much to fake, so anything
# short of a clean, parseable reply raises `ScriptDraftUnavailable` for the route to turn into a
# friendly message. The blank editor is the fallback, never a canned script pretending to be AI.

_DRAFT_TIMEOUT_S = 45.0
_DRAFT_IDEA_MAX = 500
_DRAFT_SCENES_MIN, _DRAFT_SCENES_MAX = 2, 6


def _draft_schema() -> dict:
    scene = {
        "type": "object",
        "properties": {
            "role": {"type": "string", "enum": list(project_mod.SCENE_ROLES)},
            "title": {"type": "string", "description": f"a short scene title, under {project_mod.SCENE_TITLE_MAX} characters"},
            "body": {"type": "string", "description": "the words spoken in this scene, as 1-3 plain sentences with no markdown"},
        },
        "required": ["role", "title", "body"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "a short, specific video title (3-8 words)"},
            "scenes": {"type": "array", "minItems": _DRAFT_SCENES_MIN, "maxItems": _DRAFT_SCENES_MAX, "items": scene},
        },
        "required": ["title", "scenes"],
        "additionalProperties": False,
    }


def _draft_prompt(idea: str, format_hint: str | None) -> str:
    roles = ", ".join(project_mod.SCENE_ROLES)
    length = format_hint or "a short video, about 30-60 seconds spoken aloud"
    return (
        "Draft a scene-structured script for a short vertical explainer video, starring an animated "
        f"character. Scenes run in order, each naming its role, one of: {roles} — start with Hook, "
        "end with Wrap or Payoff, and use Problem/Approach/Build/Example/Check/Beat for whatever comes "
        f"between as fits the idea. Aim for {length}. Each scene's body is the words spoken in it, as "
        f"plain sentences with no markdown or stage directions. The idea: {idea!r}"
    )


async def _draft_by_copilot(idea: str, format_hint: str | None) -> dict:
    """One `query()` turn, lazily importing the SDK (invariant: this module is the only one that
    touches it). Raises on anything short of a clean structured reply — auth, billing, a runtime
    error, or the session ending without a result — for `draft_script` to fall back on."""
    env = _sdk_environment()
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    options = ClaudeAgentOptions(
        env=env,
        cli_path=_bundled_cli(),
        system_prompt="Draft short-video scripts as structured scenes for a video editor. Answer only "
                      "the requested structured output; never call a tool.",
        tools=[], allowed_tools=[], mcp_servers={}, strict_mcp_config=True, setting_sources=[],
        permission_mode="default", max_turns=1,
        output_format={"type": "json_schema", "schema": _draft_schema()},
    )
    if config.AGENT_MODEL:
        options.model = config.AGENT_MODEL
    # query() is an async generator wearing an AsyncIterator return type; cast it back so a ResultMessage
    # returning early can aclose() it ourselves, in this same task, rather than leave that to event-loop
    # teardown later (which warns: "aclose(): asynchronous generator is already running").
    prompt = _draft_prompt(agent_credentials.redact(idea),
                           agent_credentials.redact(format_hint) if format_hint else None)
    messages = cast(AsyncGenerator, query(prompt=prompt, options=options))
    try:
        async for message in messages:
            if isinstance(message, ResultMessage):
                if message.is_error or not isinstance(message.structured_output, dict):
                    raise RuntimeError(message.result or "draft: no structured output")
                return agent_credentials.redact_data(message.structured_output)
        raise RuntimeError("draft: the session ended without a result")
    finally:
        await messages.aclose()


def _render_script(raw: dict) -> tuple[str, str]:
    """A copilot reply `{title, scenes}` as `(title, script)`: the script is `## Role: Title` markdown,
    one section per scene, in `pipeline.script_video.parse_script`'s own syntax."""
    title = str(raw.get("title") or "").strip()[: project_mod.SCENE_TITLE_MAX]
    sections = []
    for scene in raw.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        body = str(scene.get("body") or "").strip()
        if not body:
            continue
        role = scene.get("role") if scene.get("role") in project_mod.SCENE_ROLES else "Beat"
        scene_title = str(scene.get("title") or "").strip()
        sections.append(f"## {role}: {scene_title}\n{body}")
    return title, "\n\n".join(sections)


class ScriptDraftError(ValueError):
    """Bad input: no idea to draft from."""


class ScriptDraftUnavailable(RuntimeError):
    """The co-pilot couldn't draft a script right now — offline, not logged in, a timeout (~45s), or a
    reply that didn't render into scenes `pipeline.script_video.parse_script` accepts. Always
    offline-safe: the caller's blank editor still works, with no fake template standing in for the
    co-pilot."""


async def draft_script(idea: object, format_hint: object = None) -> dict:
    """The co-pilot's scene-structured script for `idea`, as `{title, script, source}` — the
    same shape `POST /api/script-video` accepts, validated by that same parser before it is returned.
    Raises `ScriptDraftError` for missing input, `ScriptDraftUnavailable` for anything that keeps the
    model from a clean, parseable answer."""
    if not isinstance(idea, str) or not idea.strip():
        raise ScriptDraftError("Describe the idea first.")
    if not addons.available("assistant"):
        raise ScriptDraftUnavailable(addons.unavailable_message("assistant"))
    hint = format_hint.strip() if isinstance(format_hint, str) and format_hint.strip() else None
    try:
        agent_credentials.resolve_key()
        idea = agent_credentials.redact(idea)
        hint = agent_credentials.redact(hint) if hint else None
        raw = await asyncio.wait_for(
            _draft_by_copilot(idea.strip()[:_DRAFT_IDEA_MAX], hint), timeout=_DRAFT_TIMEOUT_S,
        )
    except Exception as e:
        raise ScriptDraftUnavailable(
            "Claude can't draft a script right now. Check your connection or verify your key in Settings, Add-ons. "
            "Write it yourself below; nothing is lost."
        ) from e
    title, script = _render_script(agent_credentials.redact_data(raw))
    if not script_video_mod.parse_script(script):
        raise ScriptDraftUnavailable("That draft didn't come out as valid scenes. Try Draft it again, or write it yourself.")
    return {"title": title, "script": script, "source": "copilot"}


# connect() returns while the stdio GPU server may still be `pending`, and a turn sent then runs
# without its tools. The wait is bounded so a slow or broken server delays one connect, never a session.
_MCP_SERVERS = ("editor", "gpu")
_MCP_READY_TIMEOUT_S = 10.0
_MCP_READY_POLL_S = 0.1


async def _await_mcp_servers(client: Any) -> None:
    """Return once every co-editor MCP server has left `pending` (connected or failed), or at the bound."""
    try:
        async with asyncio.timeout(_MCP_READY_TIMEOUT_S):
            while True:
                status = await client.get_mcp_status()
                states = {server.get("name"): server.get("status") for server in status.get("mcpServers") or []}
                if all(states.get(name) not in (None, "pending") for name in _MCP_SERVERS):
                    return
                await asyncio.sleep(_MCP_READY_POLL_S)
    except Exception:  # noqa: BLE001 — readiness is best effort; the turn proceeds with what is connected
        return


class SdkAgentSession:
    """The real `AgentSession`: one long-lived `ClaudeSDKClient` warm session, driven one turn at a
    time. Bound to the asyncio context where `aconnect` ran (SDK constraint) — the `AgentHost` worker
    owns exactly one of these per project. Tests inject a fake session to avoid live SDK calls."""

    def __init__(self, project: str, ctx: ToolCtx) -> None:
        self._project = project
        self._ctx = ctx
        self._client: Any = None
        self._fingerprint: str | None = None

    async def aconnect(self, key: str | None = None) -> None:
        key = key if key is not None else agent_credentials.require_key()
        from claude_agent_sdk import ClaudeSDKClient

        # cwd is THIS project's own dir, not the shared PROJECTS_DIR — otherwise the granted Read
        # tool could enumerate/read every other project's outputs/projects/<slug>.json sibling. The
        # agent reads project content through the in-process MCP tools, so an isolated cwd loses nothing.
        project_dir = config.PROJECTS_DIR / self._project
        project_dir.mkdir(parents=True, exist_ok=True)
        options = build_options(project_dir, self._ctx, key)
        client = ClaudeSDKClient(options)
        try:
            await client.connect(None)  # warm streaming session; feed turns via query()
            await _await_mcp_servers(client)
        except BaseException:
            await client.disconnect()
            raise
        self._client = client
        self._fingerprint = agent_credentials.key_fingerprint(key)

    async def send_turn(self, text: str, emit) -> None:  # noqa: ANN001 — Emit from the seam
        key = agent_credentials.resolve_key()
        while self._client is None or agent_credentials.key_fingerprint(key) != self._fingerprint:
            await self.aclose()
            if not key:
                raise ValidationError(agent_credentials.CONNECT_MESSAGE)
            await self.aconnect(key)
            key = agent_credentials.resolve_key()
        from claude_agent_sdk import (
            AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
        )

        assert self._client is not None, "session not connected"
        # AgentHost prepends the current, delimited snapshot immediately before this provider call;
        # the system prompt remains a safe connect-time fallback for a newly created session.
        await self._client.query(agent_credentials.redact(text))
        async def redact_emit(event: AgentEvent) -> None:
            await emit(replace(event, data=agent_credentials.redact_data(event.data)))
        # A GPU render call is a ToolUseBlock (name carries the verb) whose RESULT returns later as a
        # ToolResultBlock (only a tool_use_id) — correlate them so the reply becomes a `job` chip.
        pending_gpu: dict[str, str] = {}
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                err = getattr(msg, "error", None)
                if err is not None:
                    await redact_emit(AgentEvent("agent_error",
                                          agent_error_data({"kind": _classify_assistant_error(err), "message": str(err)}),
                                          authentication_failed=err == "authentication_failed",
                                          key_fingerprint=self._fingerprint))
                    continue
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        await redact_emit(AgentEvent("chat_text", {"text": block.text}))
                    elif isinstance(block, ToolUseBlock):
                        await redact_emit(AgentEvent("tool_use", {"name": block.name, "input": block.input}))
                        if block.name in _GPU_JOB_TOOLS:
                            pending_gpu[block.id] = block.name.rsplit("__", 1)[-1]
            elif isinstance(msg, UserMessage):
                for block in getattr(msg, "content", None) or []:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id in pending_gpu:
                        kind = pending_gpu.pop(block.tool_use_id)
                        await redact_emit(AgentEvent("job", _job_event(kind, block.content)))
            elif isinstance(msg, ResultMessage):
                if getattr(msg, "is_error", False):
                    await redact_emit(AgentEvent("agent_error", agent_error_data({
                        "kind": "runtime",
                        "message": getattr(msg, "result", None) or "turn ended in error",
                    })))
                await redact_emit(AgentEvent("turn_done", {}))

    async def aclose(self) -> None:
        client, self._client = self._client, None
        self._fingerprint = None
        if client is not None:
            await client.disconnect()


def make_sdk_session(project: str, ctx: ToolCtx) -> AgentSession:
    return SdkAgentSession(project, ctx)


class ClaudeProviderAdapter:
    """The currently installed provider adapter; future adapters register at this same seam."""

    provider_id = "claude"

    def create_session(self, project: str, context: ToolCtx) -> AgentSession:
        return make_sdk_session(project, context)


_PROVIDER_ADAPTERS: dict[str, ProviderAdapter] = {"claude": ClaudeProviderAdapter()}


def make_provider_session(project: str, ctx: ToolCtx) -> AgentSession:
    """Dispatch a validated selection to its provider adapter; never treat foreign settings as Claude."""
    adapter = _PROVIDER_ADAPTERS.get(ctx.settings.provider_id)
    if adapter is None:
        raise RuntimeError(f"provider adapter unavailable: {ctx.settings.provider_id}")
    return adapter.create_session(project, ctx)


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, addons.AssistantUnavailable) or "clinotfound" in type(exc).__name__.lower():
        return "assistant_not_installed"
    if isinstance(exc, ValidationError) and str(exc) == agent_credentials.CONNECT_MESSAGE:
        return "auth"
    text = f"{type(exc).__name__} {exc}".lower()
    if any(k in text for k in ("auth", "login", "credential", "unauthorized")):
        return "auth"
    return "runtime"


_SENTINEL = object()


class AgentHost:
    """Per-project actor. One asyncio worker task owns the session and processes turns serially; HTTP
    handlers just `post_turn` (enqueue). This honours the SDK's one-runtime-context-per-client rule
    and the workers=1 invariant (all state is process-global)."""

    def __init__(
        self,
        make_session: Callable[[str, ToolCtx], AgentSession],
        make_ctx: Callable[[str], ToolCtx],
        *,
        reaper_interval_s: float | None = None,
        shutdown_grace_s: float = 5.0,
    ) -> None:
        self._make_session = make_session
        self._make_ctx = make_ctx
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        # per-project accept/reject/status feedback prepended to the NEXT turn's query.
        self._pending_context: dict[str, list[str]] = {}
        # last-activity clock for the warm-session LRU/TTL eviction.
        self._last_active: dict[str, float] = {}
        self._busy: set[str] = set()
        self._reaper_interval_s = reaper_interval_s
        self._reaper: asyncio.Task | None = None
        self._shutdown_grace_s = shutdown_grace_s
        # The co-editor knobs the running session was built with, per project. A turn whose settings
        # differ forces a rebuild (options are frozen at connect).
        self._settings: dict[str, AgentSettings] = {}

    def push_context(self, project: str, note: str) -> None:
        """Queue a structured accept/reject/status note to be PREPENDED to `project`'s next
        turn, so the warm session actually learns a proposal's fate (accepted/rejected + reason) instead
        of only having a dead chat.jsonl line no host code ever reads. Cheap + SDK-free (safe from any
        route); the note is delivered on the next `post_turn`."""
        if note:
            self._pending_context.setdefault(project, []).append(note)

    async def post_turn(
        self, project: str, text: str, settings: "AgentSettings | None" = None,
    ) -> None:
        self._ensure_reaper()
        self._last_active[project] = time.monotonic()
        await self._evict_stale()
        # A user turn carries the panel's model/effort/mode. If they differ from what the warm
        # session was built with, tear it down first — options are frozen at connect, so the fresh worker
        # rebuilds with the new settings. Synthesized/internal turns (choose, push_context) pass None and
        # never trigger a rebuild. First turn: no prior settings, so nothing to rebuild.
        if settings is not None:
            prior = self._settings.get(project)
            if project in self._workers and prior is not None and prior != settings:
                await self._evict(project)
            self._settings[project] = settings
        q = self._queues.get(project)
        if q is None:
            await self._evict_for_capacity(project)
            q = asyncio.Queue()
            self._queues[project] = q
            self._workers[project] = asyncio.create_task(self._run(project, q))
        await q.put(text)

    def _ensure_reaper(self) -> None:
        if self._reaper is not None and not self._reaper.done():
            return
        self._reaper = asyncio.create_task(self._reap_idle(), name="cutgraph-agent-reaper")

    async def _reap_idle(self) -> None:
        configured = self._reaper_interval_s
        interval = configured if configured is not None else min(60.0, max(1.0, config.AGENT_SESSION_TTL_S / 2))
        try:
            while True:
                await asyncio.sleep(interval)
                await self._evict_stale()
        except asyncio.CancelledError:
            raise

    async def _evict(self, project: str) -> None:
        """Graceful per-project teardown: drain the queue via the sentinel and await the worker (which
        closes the SDK client), reclaiming its claude.exe + mcp_gpu.py children."""
        q = self._queues.pop(project, None)
        task = self._workers.pop(project, None)
        self._last_active.pop(project, None)
        self._busy.discard(project)
        self._pending_context.pop(project, None)
        self._settings.pop(project, None)
        if q is not None:
            await q.put(_SENTINEL)
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
            except Exception:  # noqa: BLE001
                pass

    async def _evict_stale(self) -> None:
        now = time.monotonic()
        for project in [p for p, t in list(self._last_active.items())
                        if (p in self._workers and p not in self._busy
                            and self._queues.get(p) is not None and self._queues[p].empty()
                            and now - t > config.AGENT_SESSION_TTL_S)]:
            await self._evict(project)

    async def _evict_for_capacity(self, incoming: str) -> None:
        """Keep the number of warm sessions under the LRU cap before spawning a new one."""
        while len(self._workers) >= config.AGENT_MAX_WARM_SESSIONS:
            lru = min((p for p in self._workers if p != incoming),
                      key=lambda p: self._last_active.get(p, 0.0), default=None)
            if lru is None:
                break
            await self._evict(lru)

    async def _run(self, project: str, q: asyncio.Queue) -> None:
        ctx = self._make_ctx(project)
        # Hand the session the settings this worker was (re)created for — post_turn set them before
        # spawning us. ToolCtx defaults to AgentSettings() so the fake-session tests need no change.
        ctx.settings = self._settings.get(project) or AgentSettings()
        session = self._make_session(project, ctx)
        connected = False

        async def process_turn(text: str) -> None:
            nonlocal connected
            error_emitted = False
            done_requested = False

            async def emit(ev: AgentEvent) -> None:
                nonlocal error_emitted, done_requested
                if ev.type == "turn_done":
                    done_requested = True
                    return
                if done_requested and ev.type != "agent_error":
                    return
                if ev.type == "agent_error":
                    if error_emitted:
                        return
                    error_emitted = True
                    ev = replace(ev, data=agent_error_data(ev.data))
                msg = {"type": ev.type, **ev.data}
                if ev.type == "agent_error" and ev.authentication_failed:
                    msg.update(authentication_failed=True, key_fingerprint=ev.key_fingerprint)
                ctx.emit(agent_credentials.redact_data(msg))

            try:
                if not connected:
                    await session.aconnect()
                    connected = True
                pending = self._pending_context.pop(project, None)
                if pending:
                    text = "\n".join([*pending, "", text])
                await session.send_turn(agent_credentials.redact(text), emit)
            except asyncio.CancelledError:
                await emit(AgentEvent("agent_error", {"kind": "runtime", "message": "turn_cancelled"}))
                raise
            except Exception as exc:
                message = "agent_session_unavailable" if isinstance(exc, OSError) else str(exc) or "agent session unavailable"
                await emit(AgentEvent("agent_error", {"kind": _classify_error(exc), "message": message},
                                      authentication_failed=isinstance(exc, ValidationError)
                                      and str(exc) == agent_credentials.CONNECT_MESSAGE))
            finally:
                ctx.emit({"type": "turn_done"})

        shutdown_message = "agent_host_shutdown"
        try:
            while True:
                text = await q.get()
                if text is _SENTINEL:
                    break
                self._busy.add(project)
                try:
                    await process_turn(text)
                finally:
                    self._busy.discard(project)
                    self._last_active[project] = time.monotonic()
        except asyncio.CancelledError:
            shutdown_message = "turn_cancelled"
            raise
        finally:
            # Every post_turn accepted into this actor receives a terminal pair, including turns still
            # queued when connect or execution is cancelled. Sentinels are control messages, not turns.
            while True:
                try:
                    queued = q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if queued is _SENTINEL:
                    continue
                ctx.emit({"type": "agent_error", "kind": "runtime", "message": shutdown_message})
                ctx.emit({"type": "turn_done"})
            close_task = asyncio.create_task(session.aclose())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                await asyncio.shield(close_task)
                raise
            except Exception:  # noqa: BLE001
                pass

    async def aclose_all(self) -> None:
        reaper, self._reaper = self._reaper, None
        if reaper is not None:
            reaper.cancel()
            try:
                await reaper
            except asyncio.CancelledError:
                pass
        # Detach every actor before awaiting, as _evict does: a turn posted while they drain then starts
        # a fresh, tracked worker instead of joining a closing queue or being dropped with an orphan.
        queues, tasks = list(self._queues.values()), list(self._workers.values())
        self._queues.clear()
        self._workers.clear()
        self._last_active.clear()
        self._busy.clear()
        self._pending_context.clear()
        self._settings.clear()
        for q in queues:
            q.put_nowait(_SENTINEL)
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=self._shutdown_grace_s)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


# Keep the async Emit type referenced for readers of the seam (agent_session.Emit is the contract).
_Emit = Callable[[AgentEvent], Awaitable[None]]
