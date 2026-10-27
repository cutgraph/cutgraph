"""Ask bar → co-pilot tool → Accept, end to end through the studio.

A FAKE provider session (no model) calls the real Story tools through the studio's own ToolCtx, the
same seam a Claude turn uses; the proposal then goes through the existing revision-gated accept
route. Every accepted document must pass project.validate()."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import httpx
import pytest

import config
from pipeline import copilot, library
from pipeline import project as project_mod
from pipeline.agent_session import AgentEvent
from scripts import agent_host, studio_app
from studio import compute
from studio import state as studio_state
from studio.routes import chat

NAME = "zzask-talk"
MEDIA = "data/source/zzask-talk.mp4"
SEGMENTS = [
    "So um this is the plan.",
    "You know, we start with a brief.",
    "I I think it works and we we were right about it.",
    "The end of the talk is here now.",
]


def _words_json() -> dict:
    segs, t = [], 0.0
    for text in SEGMENTS:
        words = []
        for w in text.split():
            words.append({"word": " " + w, "start": round(t, 3), "end": round(t + 0.4, 3)})
            t += 0.5
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words})
    return {"language": "en", "duration": t, "segments": segs}


def _project(workspace_id: str | None = None) -> dict:
    doc = {
        "schemaVersion": 3, "name": NAME, "fps": 30, "width": 1920, "height": 1080, "revision": 4,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": "src", "media": {"source": MEDIA, "proxy": None}, "srcInF": 0, "srcOutF": 600, "tlStartF": 0}]}],
        "scenes": [
            {"id": "scene-1", "title": "The plan", "role": "Hook", "startWord": 0},
            {"id": "scene-2", "title": "Proof", "role": "Beat", "startWord": 13},
            {"id": "scene-3", "title": "Wrap", "role": "Wrap", "startWord": 25},
        ],
    }
    if workspace_id:
        doc["workspaceId"] = workspace_id
    return doc


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch) -> Iterator[None]:
    for name in ("PROJECTS_DIR", "TRANSCRIPTS_DIR", "PROPOSALS_DIR", "NOTES_DIR", "CHAT_DIR", "DECISIONS_DIR",
                 "WORKSPACES_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    config.TRANSCRIPTS_DIR.mkdir(parents=True)
    (config.TRANSCRIPTS_DIR / f"{NAME}.words.json").write_bytes(json.dumps(_words_json()).encode())
    studio_state._agent_host_override = None
    yield
    studio_state._agent_host_override = None


def _write_project(doc: dict) -> None:
    project_mod.write_project(config.PROJECTS_DIR / f"{NAME}.json", project_mod.from_dict(doc))


def _fake_host(tool: str, args: dict) -> agent_host.AgentHost:
    """A scripted provider: whatever is asked, it calls `tool` once and says what it proposed."""
    class Scripted:
        def __init__(self, project: str, ctx: agent_host.ToolCtx) -> None:
            self.ctx = ctx

        async def aconnect(self) -> None:
            return None

        async def send_turn(self, text: str, emit) -> None:  # noqa: ANN001
            await emit(AgentEvent("tool_use", {"name": f"mcp__editor__{tool}", "input": args}))
            result = json.loads(agent_host._do_copilot(self.ctx, tool, args))
            await emit(AgentEvent("chat_text", {"text": f"Proposed: {result.get('title')}"}))
            await emit(AgentEvent("turn_done", {}))

        async def aclose(self) -> None:
            return None

    return agent_host.AgentHost(make_session=Scripted, make_ctx=chat._make_tool_ctx)


def _ask_and_accept(text: str, tool: str, args: dict) -> tuple[dict, list[dict], dict]:
    host = _fake_host(tool, args)
    studio_state._agent_host_override = host

    async def scenario() -> tuple[dict, list[dict], dict]:
        transport = httpx.ASGITransport(app=studio_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
            q = studio_state.hub.subscribe(NAME)
            assert (await client.post(f"/api/chat/{NAME}", json={"text": text})).status_code == 200
            seen: list[dict] = []
            while not any(m.get("type") == "turn_done" for m in seen):
                seen.append(await asyncio.wait_for(q.get(), timeout=5))
            studio_state.hub.unsubscribe(NAME, q)
            await host.aclose_all()
            card = next(m for m in seen if m.get("type") == "proposal")
            accepted = await client.post(f"/api/proposal/{NAME}/{card['id']}/accept", json={})
            assert accepted.status_code == 200, accepted.text
        return card, seen, accepted.json()

    return asyncio.run(scenario())


def _words() -> list[dict]:
    return [{"w": w["word"].strip(), "start": w["start"], "end": w["end"]}
            for s in _words_json()["segments"] for w in s["words"]]


def test_tighten_this_cuts_the_selection_as_one_revision() -> None:
    _write_project(_project())
    card, seen, accepted = _ask_and_accept(
        "tighten this\n\n[Story view · selected words 25-28 “The end of the” in scene-3 “Wrap”]",
        "cut_fillers", {"ranges": [{"wordIdx": 25, "endWordIdx": 28}]})
    assert [m["type"] for m in seen] == ["tool_use", "proposal", "chat_text", "turn_done"]
    assert card["marks"] == [{"a": 25, "b": 29, "kind": "cut", "label": "“The end of the”"}]
    assert card["baseRevision"] == 4 and card["savesSec"] > 0
    doc = accepted["project"]
    assert accepted["revision"] == 5 and doc["revision"] == 5
    project_mod.validate(project_mod.from_dict(doc))
    struck = copilot.cut_words(doc, MEDIA, _words())
    assert struck == {25, 26, 27, 28}
    assert accepted["proposal"]["status"] == "accepted"


def test_filler_pass_accepts_and_every_filler_is_struck() -> None:
    _write_project(_project())
    card, _seen, accepted = _ask_and_accept("Cut filler words", "cut_fillers", {})
    assert [m["label"] for m in card["marks"]] == ["“um”", "“you know”", "repeat “i”", "repeat “we”"]
    assert copilot.cut_words(accepted["project"], MEDIA, _words()) == {1, 6, 7, 13, 19}


def test_make_scene_one_an_animation_sets_its_shot_mode() -> None:
    _write_project(_project())
    card, _seen, accepted = _ask_and_accept("make scene 1 an animation", "set_shot",
                                            {"shots": [{"scene": "scene-1", "mode": "animation"}]})
    assert card["title"] == "Animate “The plan”"
    scenes = accepted["project"]["scenes"]
    assert scenes[0]["shot"] == {"mode": "animation"} and "shot" not in scenes[1]
    project_mod.validate(project_mod.from_dict(accepted["project"]))


def test_scene_replan_with_shifting_indices_accepts_cleanly() -> None:
    _write_project(_project())
    _card, _seen, accepted = _ask_and_accept("regroup the opener", "plan_scenes", {"edits": [
        {"op": "split", "scene": "scene-1", "word": 6, "title": "The brief"},
        {"op": "rename", "scene": "scene-2", "title": "It works"},
        {"op": "merge", "scene": "scene-2"},
    ]})
    scenes = accepted["project"]["scenes"]
    assert [(s["id"], s["title"], s["startWord"]) for s in scenes] == [
        ("scene-1", "The plan", 0), ("scene-4", "The brief", 6), ("scene-2", "It works", 13)]


def test_broll_from_the_workspace_library_lands_on_its_word() -> None:
    ws = library.create_workspace("Talks")
    manifest = config.WORKSPACES_DIR / ws["id"] / "library" / "assets.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(json.dumps({"assets": [
        {"id": "1" * 16, "name": "whiteboard.png", "kind": "image", "file": "1" * 16 + ".png", "tags": ["brief"]},
    ]}).encode())
    _write_project(_project(ws["id"]))
    card, _seen, accepted = _ask_and_accept("Add B-roll here", "suggest_broll", {"cues": [{"wordIdx": 12}]})
    assert card["title"] == "Add B-roll: whiteboard.png"
    video = next(t for t in accepted["project"]["tracks"] if t["kind"] == "video")
    assert video["clips"][0]["anchor"]["ref"] == {"media": MEDIA, "wordIdx": 12}
    assert video["clips"][0]["media"]["source"] == f"workspaces/{ws['id']}/library/{'1' * 16}.png"


def test_a_proposal_made_before_another_edit_goes_stale_instead_of_misapplying() -> None:
    _write_project(_project())
    host = _fake_host("set_shot", {"shots": [{"scene": "scene-2", "mode": "cameo"}]})
    studio_state._agent_host_override = host

    async def scenario() -> int:
        transport = httpx.ASGITransport(app=studio_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
            q = studio_state.hub.subscribe(NAME)
            await client.post(f"/api/chat/{NAME}", json={"text": "cameo in scene 2"})
            seen: list[dict] = []
            while not any(m.get("type") == "turn_done" for m in seen):
                seen.append(await asyncio.wait_for(q.get(), timeout=5))
            studio_state.hub.unsubscribe(NAME, q)
            await host.aclose_all()
            card = next(m for m in seen if m.get("type") == "proposal")
            doc = _project()
            doc["scenes"][0]["title"] = "Renamed by hand"
            put = await client.put(f"/api/project/{NAME}", json={"project": doc, "baseRevision": 4})
            assert put.status_code == 200, put.text
            return (await client.post(f"/api/proposal/{NAME}/{card['id']}/accept", json={})).status_code

    assert asyncio.run(scenario()) == 409
    on_disk = json.loads((config.PROJECTS_DIR / f"{NAME}.json").read_text(encoding="utf-8"))
    assert "shot" not in on_disk["scenes"][1]


def test_popen_stays_a_class_while_a_media_stage_holds_the_process_scope() -> None:
    """Opening a new video starts a proxy build inside `_owned_process_scope`; a first Ask turn then
    imports the co-pilot SDK, whose MCP layer evaluates `subprocess.Popen[bytes]` at import time."""
    import subprocess
    import sys

    with compute._owned_process_scope():
        assert subprocess.Popen[bytes] is not None
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=5)
        with studio_state._OWNED_PROCESSES_LOCK:
            assert studio_state._OWNED_PROCESSES[id(child)][0] is child
    assert subprocess.Popen[bytes] is not None
