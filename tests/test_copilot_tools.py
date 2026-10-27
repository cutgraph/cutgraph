"""Story co-pilot tools: schema, validation and proposal shape of plan_scenes, cut_fillers,
suggest_broll and set_shot; the compact story context and its 1-hour token budget; and parity with
the TypeScript they mirror (motion-core's shot ids, the drawer's insert). No model is called."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

import config
from pipeline import cast, copilot, library, story_insert
from pipeline import project as project_mod
from scripts import agent_host

ROOT = Path(__file__).resolve().parents[1]
MEDIA = "data/source/talk.mp4"


def _words(text: str, *, per_segment: int = 8, rate: float = 0.5) -> list[dict]:
    out = []
    for i, w in enumerate(text.split()):
        out.append({"w": w, "start": round(i * rate, 3), "end": round(i * rate + rate * 0.8, 3), "segIdx": i // per_segment})
    return out


TEXT = ("So um this is the plan. You know, we start with a brief. "
        "I I think it works and we we were right about it. The end of the talk is here now.")


def _doc(n_frames: int = 1200, scenes: list[dict] | None = None) -> dict:
    return {
        "schemaVersion": 3, "name": "talk", "fps": 30, "width": 1920, "height": 1080, "revision": 3,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": "src", "media": {"source": MEDIA, "proxy": None}, "srcInF": 0, "srcOutF": n_frames, "tlStartF": 0},
        ]}],
        "scenes": scenes if scenes is not None else [
            {"id": "scene-1", "title": "The plan", "role": "Hook", "startWord": 0},
            {"id": "scene-2", "title": "Proof", "role": "Beat", "startWord": 13},
            {"id": "scene-3", "title": "Wrap", "role": "Wrap", "startWord": 25},
        ],
    }


def _tdoc(text: str = TEXT, fillers: list[dict] | None = None) -> dict:
    return {"words": _words(text), "segments": [], "fillers": fillers or []}


def _cut(doc: dict, tdoc: dict) -> set[int]:
    return copilot.cut_words(doc, MEDIA, tdoc["words"])


def _apply(doc: dict, ops: list[dict]) -> dict:
    return project_mod.to_dict(project_mod.apply_ops(project_mod.from_dict(doc), ops))


# ── cut_fillers ─────────────────────────────────────────────────────────────────

def test_detects_fillers_and_false_starts_as_word_runs() -> None:
    words = _tdoc()["words"]
    found = copilot.detect_fillers(words, 0, len(words), set())
    runs = {(a, b) for a, b, _ in found}
    assert (1, 1) in runs                      # "um"
    assert (6, 7) in runs                      # "You know,"
    assert (13, 13) in runs                    # "I I" -> the first I
    assert (19, 19) in runs                    # "we we" -> the first we
    assert all(words[a]["w"].lower().strip(",.") != "plan" for a, _, _ in found)
    assert copilot.detect_fillers(_words("Uh- wh- what I mean, is this"), 0, 7, set())[:2] == [
        (0, 0, "“Uh-”"), (1, 1, "false start “wh-”")]


def test_cut_fillers_proposes_one_ripple_cut_with_struck_words() -> None:
    doc, tdoc = _doc(), _tdoc()
    p = copilot.propose("cut_fillers", doc, tdoc, {})
    assert p.ops[0] == {"op": "test", "path": "/tracks/0/id", "value": "speech"}
    assert p.ops[1]["op"] == "replace" and p.ops[1]["path"] == "/tracks/0/clips"
    assert [m["kind"] for m in p.marks] == ["cut"] * len(p.marks) and len(p.marks) == 4
    assert p.title == "Cut 4 filler words" and p.saves_sec and p.saves_sec > 0
    after = _apply(doc, p.ops)
    struck = _cut(after, tdoc)
    assert {1, 6, 7, 13, 19} <= struck and 0 not in struck and 2 not in struck and 14 not in struck and 20 not in struck
    assert sum(c["srcOutF"] - c["srcInF"] for c in after["tracks"][0]["clips"]) == 1200 - round(p.saves_sec * 30)


def test_cut_fillers_scope_ranges_and_errors() -> None:
    doc, tdoc = _doc(), _tdoc()
    scoped = copilot.propose("cut_fillers", doc, tdoc, {"scene": "scene-2"})
    assert [m["a"] for m in scoped.marks] == [13, 19]
    chosen = copilot.propose("cut_fillers", doc, tdoc, {"ranges": [{"wordIdx": 25, "endWordIdx": 28, "reason": "aside"}],
                                                        "title": "Drop the aside"})
    assert chosen.title == "Drop the aside" and chosen.marks == [
        {"a": 25, "b": 29, "kind": "cut", "label": "“The end of the” · aside"}]
    assert {25, 26, 27, 28} <= _cut(_apply(doc, chosen.ops), tdoc) and 29 not in _cut(_apply(doc, chosen.ops), tdoc)
    with pytest.raises(copilot.ToolError, match="more than 80 words"):
        copilot.propose("cut_fillers", doc, _tdoc(" ".join(["word"] * 120)), {"ranges": [{"wordIdx": 0, "endWordIdx": 90}]})
    with pytest.raises(copilot.ToolError, match="endWordIdx must be an integer >= 5"):
        copilot.propose("cut_fillers", doc, tdoc, {"ranges": [{"wordIdx": 5, "endWordIdx": 2}]})
    with pytest.raises(copilot.ToolError, match="no scene 'scene-9'"):
        copilot.propose("cut_fillers", doc, tdoc, {"scene": "scene-9"})
    already = _apply(doc, chosen.ops)
    with pytest.raises(copilot.NothingToPropose, match="already cut"):
        copilot.propose("cut_fillers", already, tdoc, {"ranges": [{"wordIdx": 27, "endWordIdx": 28}]})
    with pytest.raises(copilot.NothingToPropose):
        copilot.propose("cut_fillers", doc, _tdoc("A clean sentence with nothing to cut."), {})


def test_verbatim_fillers_cut_with_padding_clipped_to_neighbours() -> None:
    doc = _doc()
    tdoc = _tdoc("Hello there friend", fillers=[{"w": "um,", "start": 0.42, "end": 0.48, "segIdx": 0}])
    p = copilot.propose("cut_fillers", doc, tdoc, {})
    assert p.marks == [{"a": 1, "b": 1, "kind": "cut", "label": "“um”"}]
    clips = _apply(doc, p.ops)["tracks"][0]["clips"]
    # [0.40s end of "Hello", 0.50s start of "there") = frames [12, 15): the 1-frame pad stops at each word.
    assert [(c["srcInF"], c["srcOutF"]) for c in clips] == [(0, 12), (15, 1200)]


def test_verbatim_fillers_dedupe_segment_boundary_repeats() -> None:
    """A WhisperX segment-boundary artifact can tag the same disfluency twice; the proposal should
    still cut it once, not offer the same suggestion twice."""
    doc = _doc()
    tdoc = _tdoc("Hello there friend", fillers=[
        {"w": "um,", "start": 0.42, "end": 0.48, "segIdx": 0},
        {"w": "um,", "start": 0.42, "end": 0.48, "segIdx": 1},
    ])
    p = copilot.propose("cut_fillers", doc, tdoc, {})
    assert p.marks == [{"a": 1, "b": 1, "kind": "cut", "label": "“um”"}]


# ── plan_scenes ─────────────────────────────────────────────────────────────────

def test_plan_scenes_chains_split_rename_merge_as_one_patch() -> None:
    doc, tdoc = _doc(), _tdoc()
    p = copilot.propose("plan_scenes", doc, tdoc, {"edits": [
        {"op": "split", "scene": "scene-1", "word": 6, "role": "Approach"},
        {"op": "rename", "scene": "scene-2", "title": "It works"},
        {"op": "merge", "scene": "scene-2"},
        {"op": "role", "scene": "scene-1", "role": "Problem"},
    ], "why": "Two ideas in the opener"})
    assert p.rationale == "Two ideas in the opener" and p.title == "Replan scenes · 4 changes"
    assert all(op["op"] in {"test", "add", "replace", "remove"} for op in p.ops)
    scenes = _apply(doc, p.ops)["scenes"]
    assert [(s["id"], s["role"], s["startWord"]) for s in scenes] == [
        ("scene-1", "Problem", 0), ("scene-4", "Approach", 6), ("scene-2", "Beat", 13)]
    assert scenes[1]["title"] == "You know, we start" and scenes[2]["title"] == "It works"
    assert p.marks[0] == {"a": 6, "b": 7, "kind": "mark", "label": "Split “The plan” at “You”"}


def test_plan_scenes_rejects_what_it_cannot_do() -> None:
    doc, tdoc = _doc(), _tdoc()
    for edits, message in (
        ([{"op": "split", "scene": "scene-1", "word": 0}], r"word must be an integer >= 1 below 13"),
        ([{"op": "merge", "scene": "scene-3"}], "last scene"),
        ([{"op": "role", "scene": "scene-1", "role": "Villain"}], "role must be one of Hook"),
        ([{"op": "move", "scene": "scene-1"}], "op must be split, merge, rename or role"),
        ([{"op": "rename", "scene": "nope", "title": "x"}], r"no scene 'nope' \(scenes: scene-1, scene-2, scene-3\)"),
        ([], "edits must be a non-empty list"),
    ):
        with pytest.raises(copilot.ToolError, match=message):
            copilot.propose("plan_scenes", doc, tdoc, {"edits": edits})
    reordered = _doc(scenes=[{"id": "scene-1", "title": "A", "role": "Hook", "startWord": 0},
                             {"id": "scene-3", "title": "C", "role": "Wrap", "startWord": 25},
                             {"id": "scene-2", "title": "B", "role": "Beat", "startWord": 13}])
    with pytest.raises(copilot.ToolError, match="reorder on the Board first"):
        copilot.propose("plan_scenes", reordered, tdoc, {"edits": [{"op": "merge", "scene": "scene-1"}]})


# ── set_shot ────────────────────────────────────────────────────────────────────

def test_set_shot_merges_validated_motion_ids() -> None:
    doc, tdoc = _doc(), _tdoc()
    doc["scenes"][1]["shot"] = {"mode": "cameo", "pose": "wave"}
    p = copilot.propose("set_shot", doc, tdoc, {"shots": [
        {"scene": "scene-1", "mode": "animation", "expression": "excited", "cueWord": 4, "headline": "The plan"},
        {"scene": "scene-2", "pose": None, "move": "hop"},
    ]})
    scenes = _apply(doc, p.ops)["scenes"]
    assert scenes[0]["shot"] == {"mode": "animation", "expression": "excited", "cueWord": 4, "headline": "The plan"}
    assert scenes[1]["shot"] == {"mode": "cameo", "move": "hop"}
    assert p.title == "Direct 2 scenes"
    assert p.marks[0] == {"a": 0, "b": 13, "kind": "mark", "label": "Animate “The plan” · excited"}
    one = copilot.propose("set_shot", doc, tdoc, {"shots": [{"scene": "scene-3", "mode": "animation"}]})
    assert one.title == "Animate “Wrap”" and one.ops == [
        {"op": "test", "path": "/scenes/2/id", "value": "scene-3"},
        {"op": "add", "path": "/scenes/2/shot", "value": {"mode": "animation"}}]


def test_set_shot_validation() -> None:
    doc, tdoc = _doc(), _tdoc()
    for shot, message in (
        ({"scene": "scene-1", "pose": "dab"}, "pose must be one of rest, point, hold, cheer, shrug, think, wave"),
        ({"scene": "scene-1", "mode": "3d"}, "mode must be one of footage, animation, cameo"),
        ({"scene": "scene-1", "cueWord": 13}, r"cueWord must be an integer >= 0 below 13"),
        ({"scene": "scene-1", "headline": "x" * 81}, "at most 80"),
        ({"scene": "scene-1", "hideHeadline": "yes"}, "hideHeadline must be true or false"),
        ({"scene": "scene-1", "colour": "red"}, "unknown fields: colour"),
    ):
        with pytest.raises(copilot.ToolError, match=message):
            copilot.propose("set_shot", doc, tdoc, {"shots": [shot]})
    doc["scenes"][0]["shot"] = {"mode": "animation"}
    with pytest.raises(copilot.NothingToPropose):
        copilot.propose("set_shot", doc, tdoc, {"shots": [{"scene": "scene-1", "mode": "animation"}]})


def test_set_shot_toggles_hide_headline() -> None:
    doc, tdoc = _doc(), _tdoc()
    p = copilot.propose("set_shot", doc, tdoc, {"shots": [
        {"scene": "scene-1", "mode": "animation", "headline": "Ship it", "hideHeadline": True}]})
    after = _apply(doc, p.ops)
    assert after["scenes"][0]["shot"] == {"mode": "animation", "headline": "Ship it", "hideHeadline": True}
    cleared = copilot.propose("set_shot", after, tdoc, {"shots": [{"scene": "scene-1", "hideHeadline": None}]})
    assert _apply(after, cleared.ops)["scenes"][0]["shot"] == {"mode": "animation", "headline": "Ship it"}


@pytest.fixture
def garden(tmp_path, monkeypatch, client_rig: None) -> str:
    """A client workspace with the fern sample performer (on its own rig) and a custom face for it."""
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    ws = library.create_workspace("Garden")["id"]
    guest = cast.create_character(ws, "Notes Guest", "sample-rig", palette="fern")
    cast.create_expression(guest["id"], "Focus", {"brows": "raised", "eyes": "open", "mouth": "closed"})
    return ws


def test_set_shot_checks_looks_against_the_rig_of_the_character_they_land_on(garden: str) -> None:
    doc, tdoc = _doc(), _tdoc()
    doc["workspaceId"] = garden
    p = copilot.propose("set_shot", doc, tdoc, {"shots": [
        {"scene": "scene-1", "mode": "animation", "character": "notes-guest", "pose": "wave", "move": "nod", "expression": "focus"},
        {"scene": "scene-2", "mode": "animation", "pose": "shrug",
         "costar": {"character": "notes-guest", "pose": "wave", "expression": "curious"}, "interaction": "highfive"},
    ]})
    scenes = _apply(doc, p.ops)["scenes"]
    assert scenes[0]["shot"] == {"mode": "animation", "character": "notes-guest", "pose": "wave", "move": "nod", "expression": "focus"}
    assert scenes[1]["shot"] == {"mode": "animation", "pose": "shrug", "interaction": "highfive",
                                 "costar": {"character": "notes-guest", "pose": "wave", "expression": "curious"}}
    for shot, message in (
        ({"scene": "scene-1", "character": "notes-guest", "pose": "shrug"},
         "pose must be one of rest, wave, point for Notes Guest"),
        ({"scene": "scene-1", "character": "notes-guest", "expression": "idea"}, "expression must be one of neutral, happy, .*, focus for Notes Guest"),
        ({"scene": "scene-1", "pose": "not-a-pose"}, "pose must be one of rest, .* for Lens"),
        ({"scene": "scene-1", "expression": "focus"}, "expression must be one of neutral, happy, .* for Lens"),
        ({"scene": "scene-1", "costar": {"character": "notes-guest", "pose": "hold"}}, r"costar\.pose must be one of rest, .* for Notes Guest"),
        ({"scene": "scene-1", "costar": {"character": "default-character", "move": "not-a-move"}}, r"costar\.move must be one of idle, .* for Lens"),
        ({"scene": "scene-1", "character": "guest"}, "character must be one of default-character, notes-guest"),
        ({"scene": "scene-1", "costar": {"pose": "wave"}}, "costar.character is required: one of default-character, notes-guest"),
    ):
        with pytest.raises(copilot.ToolError, match=message):
            copilot.propose("set_shot", doc, tdoc, {"shots": [shot]})
    doc["scenes"][0]["shot"] = {"mode": "cameo", "character": "notes-guest", "pose": "wave", "expression": "focus"}
    back = copilot.propose("set_shot", doc, tdoc, {"shots": [{"scene": "scene-1", "character": None, "pose": "point"}]})
    assert _apply(doc, back.ops)["scenes"][0]["shot"] == {"mode": "cameo", "pose": "point"}, \
        "back on the default rig: its pose checked against the default rig's, the Guest's own face dropped"


def test_set_shot_never_casts_a_draft_expression_until_it_is_approved(garden: str) -> None:
    doc, tdoc = _doc(), _tdoc()
    doc["workspaceId"] = garden
    cast.create_expression("notes-guest", "Smirk", {"brows": "raised", "eyes": "open", "mouth": "open"}, draft=True)
    ask = {"shots": [{"scene": "scene-1", "mode": "animation", "character": "notes-guest", "expression": "smirk"}]}
    with pytest.raises(copilot.ToolError, match="expression must be one of .*focus for Notes Guest"):
        copilot.propose("set_shot", doc, tdoc, ask)
    cast.update_expression("notes-guest", "smirk", approved=True)
    assert _apply(doc, copilot.propose("set_shot", doc, tdoc, ask).ops)["scenes"][0]["shot"]["expression"] == "smirk"


# ── suggest_broll ───────────────────────────────────────────────────────────────

def _entry(name: str, kind: str, n: str, **extra) -> dict:
    aid = n * 16
    ext = {"image": ".png", "video": ".mp4", "audio": ".mp3"}[kind]
    return {"workspaceId": "garden-1a2b3c", "workspaceName": "garden",
            "asset": {"id": aid, "name": name, "kind": kind, "file": aid + ext, "tags": [], **extra}}


LIBRARY = [
    _entry("whiteboard sketch.png", "image", "1", tags=["brief", "planning"]),
    _entry("garden-plate.mp4", "video", "2", durationS=4.0, tags=["garden"]),
    _entry("Calm bed.mp3", "audio", "3", durationS=120.0, tags=["music"]),
]


def test_suggest_broll_matches_words_and_places_like_the_drawer() -> None:
    doc, tdoc = _doc(), _tdoc()
    p = copilot.propose("suggest_broll", doc, tdoc, {"cues": [
        {"wordIdx": 12},                                          # "brief." -> tagged whiteboard
        {"wordIdx": 0, "kind": "music"},
    ]}, assets=lambda ws: LIBRARY)
    after = _apply(doc, p.ops)
    kinds = {t["id"]: t for t in after["tracks"]}
    board = kinds["video"]["clips"][0]
    assert board["media"]["source"] == "workspaces/garden-1a2b3c/library/" + "1" * 16 + ".png"
    assert board["anchor"] == {"mode": "word", "ref": {"media": MEDIA, "wordIdx": 12}, "offsetF": 0}
    assert board["tlStartF"] == 180 and board["srcOutF"] == story_insert.BROLL_DEFAULT_F
    bed = kinds["audio"]["clips"][0]
    assert bed["duckDb"] == story_insert.MUSIC_DUCK_DB and bed["tlStartF"] == 0
    assert [t["id"] for t in after["tracks"]] == ["speech", "video", "audio"]
    assert p.title == "Add 2 cues" and p.marks[0]["label"] == "B-roll: whiteboard sketch.png at “brief.”"
    named = copilot.propose("suggest_broll", doc, tdoc, {"cues": [{"wordIdx": 3, "asset": "garden-plate"}]},
                            assets=lambda ws: LIBRARY)
    assert named.title == "Add B-roll: garden-plate.mp4"
    assert _apply(doc, named.ops)["tracks"][1]["clips"][0]["srcOutF"] == 120


def test_suggest_broll_errors_tell_the_model_what_exists() -> None:
    doc, tdoc = _doc(), _tdoc()
    with pytest.raises(copilot.ToolError, match=r"nothing in the library matches seedling \(have: garden-plate.mp4, whiteboard"):
        copilot.propose("suggest_broll", doc, tdoc, {"cues": [{"wordIdx": 2, "query": "seedlings"}]}, assets=lambda ws: LIBRARY)
    with pytest.raises(copilot.ToolError, match="no Sound assets"):
        copilot.propose("suggest_broll", doc, tdoc, {"cues": [{"wordIdx": 2, "kind": "sfx"}]}, assets=lambda ws: LIBRARY)
    with pytest.raises(copilot.ToolError, match="no B-roll asset named 'drone'"):
        copilot.propose("suggest_broll", doc, tdoc, {"cues": [{"wordIdx": 2, "asset": "drone"}]}, assets=lambda ws: LIBRARY)
    cut = _apply(doc, copilot.propose("cut_fillers", doc, tdoc, {}).ops)
    with pytest.raises(copilot.ToolError, match="word 1 is cut"):
        copilot.propose("suggest_broll", cut, tdoc, {"cues": [{"wordIdx": 1, "asset": "garden-plate"}]}, assets=lambda ws: LIBRARY)


def test_library_is_the_video_workspace_plus_brand(tmp_path, monkeypatch) -> None:
    from pipeline import library
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    ws = library.create_workspace("garden")
    for wid, name in ((ws["id"], "plate.png"), ("brand", "logo.png")):
        library.get_workspace(wid)
        manifest = config.WORKSPACES_DIR / wid / "library" / "assets.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_bytes(json.dumps({"assets": [{"id": "a" * 16, "name": name, "kind": "image",
                                                     "file": "a" * 16 + ".png", "tags": []}]}).encode())
    entries = copilot.library_assets(ws["id"])
    assert [(e["workspaceId"], e["asset"]["name"]) for e in entries] == [(ws["id"], "plate.png"), ("brand", "logo.png")]
    assert [e["workspaceId"] for e in copilot.library_assets("unsorted")] == ["brand"]


def test_insert_mirrors_the_drawer_golden_cases() -> None:
    golden = json.loads((ROOT / "tests/fixtures/story_insert_cases.json").read_text(encoding="utf-8"))
    for case in golden["cases"]:
        entry = case["entry"]
        try:
            doc, cid, tl = story_insert.insert_asset(case["project"], golden["words"], golden["media"], case["wordIdx"],
                                                     entry["workspaceId"], entry["asset"])
        except ValueError as e:
            assert case["expected"] == {"error": str(e)}, case["name"]
            continue
        track = next(t for t in doc["tracks"] if any(c["id"] == cid for c in t.get("clips") or []))
        assert {"id": cid, "tlStartF": tl, "trackIds": [t["id"] for t in doc["tracks"]], "track": track["id"],
                "clip": next(c for c in track["clips"] if c["id"] == cid),
                "tab": story_insert.asset_tab(entry["asset"])} == case["expected"], case["name"]


# ── the compact context ─────────────────────────────────────────────────────────

def _pages(doc: dict, tdoc: dict, assets: list[dict] | None = None) -> list[dict]:
    pages, cursor = [], 0
    while True:
        text = copilot.story_outline(doc, 7, tdoc, cursor, assets)
        pages.append(json.loads(text))
        assert len(text) <= copilot.STORY_PAGE_CHARS
        if "nextCursor" not in pages[-1]:
            return pages
        cursor = pages[-1]["nextCursor"]


def test_story_outline_is_the_segment_map_with_cut_words_marked() -> None:
    doc, tdoc = _doc(), _tdoc()
    cut = _apply(doc, copilot.propose("cut_fillers", doc, tdoc, {"scene": "scene-1"}).ops)
    page = json.loads(copilot.story_outline(cut, 7, tdoc, 0, LIBRARY))
    assert page["scenes"][0] == {"id": "scene-1", "role": "Hook", "title": "The plan", "words": [0, 13], "cutWords": 3}
    assert page["segments"][0] == [0, 0.0, "So {um} this is the plan. {You} {know,}"]
    assert page["segments"][1][2].startswith("we start")
    assert page["library"][2] == ["Calm bed.mp3", "music", 120.0, ["music"]]
    assert json.loads(copilot.story_outline(doc, 1, None)) == {"available": False, "reason": "this video has no transcript yet"}


def test_one_hour_context_stays_inside_the_token_budget() -> None:
    """A 1-hour talk (9,000 spoken words at 150 wpm, 750 segments): its whole outline fits
    STORY_TOKEN_BUDGET (~15k tokens), while the WhisperX word-level JSON it replaces is 10x that."""
    spoken = ("So the real reason most projects fail early is that nobody agrees on what done looks like. We start "
              "with a one page brief, who it is for, what done looks like, and what we are definitely not building. "
              "Then every task gets a clear owner and a bounded scope, and we review the results together.").split()
    words = [{"w": spoken[i % len(spoken)], "start": i * 0.4, "end": i * 0.4 + 0.3, "segIdx": i // 12} for i in range(9000)]
    scenes = [{"id": f"scene-{k + 1}", "title": f"Part {k + 1}", "role": "Beat", "startWord": k * 1125} for k in range(8)]
    doc = _doc(n_frames=108_000, scenes=scenes)
    pages = _pages(doc, {"words": words, "fillers": []}, LIBRARY)
    rows = [r for p in pages for r in p["segments"]]
    assert [r[0] for r in rows] == list(range(0, 9000, 12))
    total = sum(copilot.estimate_tokens(copilot.dumps(p)) for p in pages)
    word_level = copilot.estimate_tokens(json.dumps(
        [{"word": " " + w["w"], "start": w["start"], "end": w["end"], "score": 0.93} for w in words]))
    assert total <= copilot.STORY_TOKEN_BUDGET, total
    assert word_level > 100_000 and word_level > 8 * total


# ── parity with the TypeScript it mirrors ────────────────────────────────────────

def _ts_list(source: str, name: str) -> tuple[str, ...]:
    body = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]", source)
    assert body, name
    return tuple(re.findall(r"'([^']+)'", body.group(1)))


def test_shot_ids_match_motion_core() -> None:
    src = ROOT / "motion-core" / "src"
    scene, proj, duo = ((src / name).read_text(encoding="utf-8") for name in ("scene.ts", "project.ts", "duo.ts"))
    for key, ids in (("poses", copilot.POSES), ("moves", copilot.MOVES)):
        assert set(ids) == {i for rig in cast.RIGS.values() for i in rig[key]}, "every rig's, as tests/test_cast.py mirrors them"
    assert {"flex", "hips", "shrug"} <= set(copilot.POSES) and "sway" in copilot.MOVES
    assert copilot.BACKDROPS == _ts_list(scene, "BACKDROPS")
    assert copilot.DIAGRAMS == _ts_list(scene, "DIAGRAMS")
    assert project_mod.SHOT_MODES == _ts_list(proj, "SHOT_MODES")
    assert project_mod.INTERACTIONS == _ts_list(duo, "INTERACTIONS")


# ── the tools the model sees ────────────────────────────────────────────────────

def test_editor_server_registers_typed_story_tools(monkeypatch) -> None:
    sdk = pytest.importorskip("claude_agent_sdk")
    registered: list = []
    monkeypatch.setattr(sdk, "create_sdk_mcp_server", lambda name, version, tools: registered.extend(tools))
    doc, tdoc = _doc(), _tdoc()
    emitted: list[dict] = []
    ctx = agent_host.ToolCtx(slug="zzcopilot", read_doc=lambda: (doc, 3), read_transcript=lambda: tdoc,
                             fps=lambda: 30, emit=emitted.append, read_library=lambda ws: LIBRARY)
    agent_host.build_editor_server(ctx)
    tools = {t.name: t for t in registered}
    assert set(tools) == set(agent_host._EDITOR_TOOLS)
    assert {"read_story", *copilot.TOOLS} <= set(tools)
    for name in copilot.TOOLS:
        assert len(tools[name].description) < 320
    shot_items = tools["set_shot"].input_schema["properties"]["shots"]["items"]["properties"]
    assert shot_items["pose"]["enum"] == list(copilot.POSES) and shot_items["mode"]["enum"] == ["footage", "animation", "cameo"]
    assert shot_items["interaction"]["enum"] == list(project_mod.INTERACTIONS)
    assert shot_items["hideHeadline"]["type"] == "boolean"
    assert set(shot_items["costar"]["properties"]) == {"character", "pose", "expression", "move", "bubble"}
    assert tools["plan_scenes"].input_schema["required"] == ["edits"]
    story = json.loads(asyncio.run(tools["read_story"].handler({}))["content"][0]["text"])
    assert story["scenes"][0]["id"] == "scene-1"
    bad = json.loads(asyncio.run(tools["set_shot"].handler({"shots": [{"scene": "scene-1", "pose": "dab"}]}))["content"][0]["text"])
    assert "pose must be one of" in bad["error"] and not emitted


def test_story_tools_are_proposal_only_and_read_story_is_plan_safe() -> None:
    assert set(agent_host._EDITOR_TOOLS) == {
        "get_project", "propose_patch", "propose_options", "read_transcript", "find_word",
        "get_channel", "read_notes", "read_preferences", "append_note", "read_story",
        "cut_fillers", "plan_scenes", "set_shot", "suggest_broll",
    }
    for name in copilot.TOOLS:
        assert f"mcp__editor__{name}" in agent_host._ALLOWED_EDITOR
        assert f"mcp__editor__{name}" not in agent_host._READONLY_EDITOR
    assert "mcp__editor__read_story" in agent_host._READONLY_EDITOR


def test_do_copilot_writes_a_pending_proposal_with_marks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "PROPOSALS_DIR", tmp_path / "proposals")
    doc, tdoc = _doc(), _tdoc()
    sink: list[dict] = []
    ctx = agent_host.ToolCtx(slug="zzcopilot", read_doc=lambda: (doc, 3), read_transcript=lambda: tdoc,
                             fps=lambda: 30, emit=sink.append)
    out = json.loads(agent_host._do_copilot(ctx, "cut_fillers", {"scene": "scene-2"}))
    assert out["title"] == "Cut 2 filler words" and out["changes"] == ["repeat “i”", "repeat “we”"] and out["savesSec"] > 0
    event = sink[0]
    assert event["type"] == "proposal" and event["status"] == "pending" and event["baseRevision"] == 3
    assert event["marks"] == [{"a": 13, "b": 14, "kind": "cut", "label": "repeat “i”"},
                              {"a": 19, "b": 20, "kind": "cut", "label": "repeat “we”"}]
    assert event["ops"][0]["op"] == "test"
    from pipeline import proposals
    assert proposals.read("zzcopilot", out["id"])["marks"] == event["marks"]  # type: ignore[index]
    assert json.loads(agent_host._do_copilot(ctx, "cut_fillers", {"ranges": [{"wordIdx": 999}]}))["error"].startswith("ranges[0].wordIdx")
    assert json.loads(agent_host._do_copilot(ctx, "set_shot", {"shots": [{"scene": "scene-1"}]})) == {
        "proposed": False, "reason": "those shots are already set"}
    assert len(sink) == 1
