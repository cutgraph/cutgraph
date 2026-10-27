"""Short-film staging: a scene's shot can carry a camera move, a layout and the layouts' own
fields (a screen's library images, up to three phones; a title card's badges; a sign-off's
letters). The document validates them and the schema mirrors the rules, the ids mirror motion-core's, the
export resolves each phone's image and re-renders when one changes, the co-pilot's set_shot proposes them,
and the Node renderer draws the resolved images."""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import jsonschema
import pytest

from pipeline.errors import ValidationError

import config
from pipeline import copilot, library, scene_render, staging
from pipeline import project as project_mod
from scripts import agent_host

ROOT = Path(__file__).resolve().parent.parent
MEDIA = "data/source/talk.mp4"
WORDS = [{"word": f"w{i}", "start": i * 0.5 + 0.1, "end": i * 0.5 + 0.4} for i in range(12)]
TITLE = {"mode": "animation", "layout": "title", "backdrop": "black", "headline": "Tone reference",
         "badges": [{"text": "ONE", "tone": "ink"}, {"text": "TWO", "tone": "paper"}]}
SIGNOFF = {"mode": "animation", "layout": "signoff", "camera": "push-in", "mark": "DONE", "expression": "idea",
           "eyebrow": "End card", "headline": "Museum Visit"}


def _doc(shot: dict | None, workspace: str | None = None) -> dict:
    return {
        "schemaVersion": project_mod.CURRENT_SCHEMA_VERSION, "name": "zzstage", "fps": 30, "width": 320, "height": 180, "revision": 1, "transcript": None,
        **({"workspaceId": workspace} if workspace else {}),
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": "c0", "media": {"source": MEDIA, "proxy": None}, "srcInF": 0, "srcOutF": 180, "tlStartF": 0, "gainDb": 0.0},
        ]}],
        "scenes": [
            {"id": "s1", "title": "Launch", "role": "Payoff", "startWord": 0, **({"shot": shot} if shot else {})},
            {"id": "s2", "title": "Middle", "role": "Beat", "startWord": 6},
        ],
    }


def _schema() -> dict:
    return json.loads((ROOT / "schema" / "project.schema.json").read_text(encoding="utf-8"))


def _png(path: Path, rgb: tuple[int, int, int], w: int = 9, h: int = 19) -> Path:
    """A solid-colour PNG, written by hand so the test needs no imaging library."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return path


# ── the document ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("shot", [TITLE, SIGNOFF, {"mode": "animation", "layout": "screen", "screen": {"asset": "0123456789abcdef"},
                                                   "camera": "pull-out", "backdrop": "vision"}, {"layout": "stage"},
                                  {"mode": "animation", "layout": "screen", "screen": {"asset": "0123456789abcdef", "more": ["fedcba9876543210"]}},
                                  {"mode": "animation", "layout": "screen",
                                   "screen": {"asset": "0123456789abcdef", "more": ["fedcba9876543210", "0123456789abcdef"]}}])
def test_staged_shots_validate_round_trip_and_match_the_schema(shot: dict) -> None:
    p = project_mod.from_dict(_doc(shot))
    project_mod.validate(p)
    d = project_mod.to_dict(p)
    assert d["scenes"][0]["shot"] == shot
    jsonschema.validate(instance=d, schema=_schema())


@pytest.mark.parametrize("staged, message", [
    ({"camera": "dolly"}, "shot.camera must be one of"),
    ({"layout": "split"}, "shot.layout must be one of"),
    ({"screen": "0123456789abcdef"}, "shot.screen must be"),
    ({"screen": {"asset": "../../etc/passwd"}}, "shot.screen must be"),
    ({"screen": {"asset": "0123456789abcdef", "fit": "cover"}}, "shot.screen must be"),
    ({"screen": {"more": ["0123456789abcdef"]}}, "shot.screen must be"),
    ({"screen": {"asset": "0123456789abcdef", "more": []}}, "shot.screen.more must list 1 to 2"),
    ({"screen": {"asset": "0123456789abcdef", "more": ["0123456789abcdef"] * 3}}, "shot.screen.more must list 1 to 2"),
    ({"screen": {"asset": "0123456789abcdef", "more": "fedcba9876543210"}}, "shot.screen.more must list 1 to 2"),
    ({"screen": {"asset": "0123456789abcdef", "more": ["../x"]}}, "shot.screen.more must list 1 to 2"),
    ({"badges": []}, "shot.badges must list 1 to 4"),
    ({"badges": [{"text": "A", "tone": "ink"}] * 5}, "shot.badges must list 1 to 4"),
    ({"badges": [{"text": "A"}]}, "a badge is"),
    ({"badges": [{"text": "  ", "tone": "ink"}]}, "a badge's text"),
    ({"badges": [{"text": "X" * 25, "tone": "ink"}]}, "a badge's text"),
    ({"badges": [{"text": "A", "tone": "neon"}]}, "a badge's tone"),
    ({"mark": ""}, "shot.mark must be"),
    ({"mark": "ABCDE"}, "shot.mark must be"),
    ({"mark": "A!"}, "shot.mark must be"),
])
def test_bad_staging_is_rejected_by_validate_and_the_schema(staged: dict, message: str) -> None:
    doc = _doc({"mode": "animation", **staged})
    with pytest.raises(ValidationError, match=message):
        project_mod.validate(project_mod.from_dict(doc))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=doc, schema=_schema())


def test_the_schema_and_the_ids_mirror_motion_core() -> None:
    shot, defs = _schema()["$defs"]["Shot"]["properties"], _schema()["$defs"]
    assert shot["camera"]["enum"] == list(project_mod.SHOT_CAMERAS)
    assert shot["layout"]["enum"] == list(project_mod.SHOT_LAYOUTS)
    assert shot["mark"]["pattern"] == project_mod.SHOT_MARK_PATTERN
    assert shot["badges"]["maxItems"] == project_mod.SHOT_BADGES_MAX
    assert defs["Badge"]["properties"]["tone"]["enum"] == list(project_mod.BADGE_TONES)
    assert defs["Badge"]["properties"]["text"]["maxLength"] == project_mod.SHOT_BADGE_TEXT_MAX
    src = (ROOT / "motion-core" / "src" / "staging.ts").read_text(encoding="utf-8")

    def ts_list(name: str) -> tuple[str, ...]:
        body = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]", src)
        assert body, name
        return tuple(re.findall(r"'([^']+)'", body.group(1)))

    def ts_int(name: str) -> int:
        found = re.search(rf"export const {name} = (\d+);", src)
        assert found, name
        return int(found.group(1))

    assert ts_list("LAYOUTS") == project_mod.SHOT_LAYOUTS
    assert ts_list("CAMERAS") == project_mod.SHOT_CAMERAS
    assert ts_list("BADGE_TONES") == project_mod.BADGE_TONES
    assert ts_int("BADGES_MAX") == project_mod.SHOT_BADGES_MAX
    assert ts_int("SCREEN_MORE_MAX") == project_mod.SHOT_SCREEN_MORE_MAX == defs["PhoneScreen"]["properties"]["more"]["maxItems"]
    assert staging.TOOL_PROPS["screen"]["properties"]["more"]["maxItems"] == project_mod.SHOT_SCREEN_MORE_MAX
    assert ts_int("MARK_MAX") == 4 and re.search(r"\{1,4\}", project_mod.SHOT_MARK_PATTERN)
    glyphs = set(re.findall(r"^\s+'?([A-Z0-9&])'?: \[", src, re.M))
    assert glyphs == set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789&"), "every character a mark may hold has a glyph"


def test_the_export_keeps_staging_in_the_spec_and_leaves_a_staged_footage_scene_out() -> None:
    [spec] = scene_render.scene_specs(_doc(SIGNOFF), WORDS)
    assert spec["shot"] == SIGNOFF
    assert scene_render.scene_specs(_doc({"layout": "title", "badges": TITLE["badges"]}), WORDS) == []


# ── the export ──────────────────────────────────────────────────────────────────

@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    transcripts = tmp_path / "outputs" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "talk.words.json").write_text(json.dumps({"segments": [{"words": WORDS}]}), encoding="utf-8")
    return tmp_path


def _import(state: Path, workspace: str, name: str, rgb: tuple[int, int, int]) -> dict:
    asset, _dup = library.import_asset(workspace, _png(state / name, rgb), display_name=name)
    return asset


def test_ensure_scenes_renders_a_screen_with_its_library_image_and_re_renders_when_it_changes(
        state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake(request: dict, dest: Path) -> dict:
        calls.append(request)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"scene")
        return {"event": "done"}

    monkeypatch.setattr(scene_render, "render_scene", fake)
    garden = library.create_workspace("Garden")["id"]
    red, blue = _import(state, garden, "notes.png", (230, 20, 30)), _import(state, "brand", "brand.png", (20, 40, 230))
    screen = {"mode": "animation", "layout": "screen", "headline": "Notes"}
    scene_render.ensure_scenes(_doc({**screen, "screen": {"asset": red["id"]}}, garden), root=state)
    red_file = library.asset_file_path(garden, red["id"])
    assert red_file is not None and Path(calls[-1]["shot"]["screen"]["image"]) == red_file.resolve()
    _layers, again = scene_render.ensure_scenes(_doc({**screen, "screen": {"asset": red["id"]}}, garden), root=state)
    assert again == 0, "cached"
    _layers, swapped = scene_render.ensure_scenes(_doc({**screen, "screen": {"asset": blue["id"]}}, garden), root=state)
    assert swapped == 1 and Path(calls[-1]["shot"]["screen"]["image"]).name == blue["file"], "the brand kit's image"
    _layers, placeholder = scene_render.ensure_scenes(_doc(screen, garden), root=state)
    assert placeholder == 1 and "screen" not in calls[-1]["shot"]
    scene_render.ensure_scenes(_doc({**screen, "screen": {"asset": red["id"]}}), root=state)
    assert "image" not in calls[-1]["shot"]["screen"], "another video can't see Garden's library"


def test_every_phone_resolves_like_phone_1_and_the_cache_key_counts_each_image(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake(request: dict, dest: Path) -> dict:
        calls.append(request)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"scene")
        return {"event": "done"}

    monkeypatch.setattr(scene_render, "render_scene", fake)
    garden = library.create_workspace("Garden")["id"]
    red, green = _import(state, garden, "gallery.png", (230, 20, 30)), _import(state, garden, "floor-map.png", (20, 200, 40))
    blue = _import(state, "brand", "entrance.png", (20, 40, 230))
    three = {"mode": "animation", "layout": "screen", "headline": "Walk",
             "screen": {"asset": red["id"], "more": [green["id"], blue["id"]]}}
    _layers, n = scene_render.ensure_scenes(_doc(three, garden), root=state)
    screen = calls[-1]["shot"]["screen"]
    assert n == 1 and screen["asset"] == red["id"] and Path(screen["image"]).name == red["file"]
    assert [(m["asset"], Path(m["image"]).name) for m in screen["more"]] == [(green["id"], green["file"]), (blue["id"], blue["file"])], \
        "phone 2 from the workspace, phone 3 from the brand kit"
    assert scene_render.ensure_scenes(_doc(three, garden), root=state)[1] == 0, "cached"
    swapped = {**three, "screen": {"asset": red["id"], "more": [blue["id"], green["id"]]}}
    assert scene_render.ensure_scenes(_doc(swapped, garden), root=state)[1] == 1, "phones 2 and 3 swapped: another picture"
    missing = {**three, "screen": {"asset": red["id"], "more": ["0123456789abcdef"]}}
    scene_render.ensure_scenes(_doc(missing, garden), root=state)
    assert calls[-1]["shot"]["screen"]["more"] == [{"asset": "0123456789abcdef"}], "an asset no library holds: the app screen"

    spec = scene_render.scene_specs(_doc(three, garden), WORDS)[0]
    request = scene_render.render_request(spec, width=320, height=180, fps=30, workspace_id=garden)

    def move(phone: dict) -> dict:
        return {**phone, "image": "E:/elsewhere/" + Path(phone["image"]).name}

    screen = request["shot"]["screen"]
    moved = {**request, "shot": {**request["shot"], "screen": {**move(screen), "more": [move(m) for m in screen["more"]]}}}
    assert moved != request and scene_render.cache_key(moved, "v") == scene_render.cache_key(request, "v"), \
        "each image counts by its content-addressed name, not the state home"
    one = scene_render.render_request(scene_render.scene_specs(_doc({**three, "screen": {"asset": red["id"]}}, garden), WORDS)[0],
                                      width=320, height=180, fps=30, workspace_id=garden)
    red_file = library.asset_file_path(garden, red["id"])
    assert red_file is not None and one["shot"]["screen"] == {"asset": red["id"], "image": str(red_file.resolve())}, \
        "one phone resolves exactly as before"


@pytest.mark.skipif(not (shutil.which("node") and shutil.which("ffmpeg")), reason="needs Node 24+ and ffmpeg")
def test_the_node_renderer_draws_the_resolved_image_in_the_phone(state: Path) -> None:
    garden = library.create_workspace("Garden")["id"]
    red = _import(state, garden, "notes.png", (230, 20, 30))
    doc = _doc({"mode": "animation", "layout": "screen", "backdrop": "black", "headline": "Notes",
                "screen": {"asset": red["id"]}}, garden)
    spec = scene_render.scene_specs(doc, WORDS)[0]
    request = scene_render.render_request(spec, width=320, height=180, fps=30, workspace_id=garden)
    dest = state / "screen.mp4"
    scene_render.render_scene({**request, "durF": 30}, dest)
    frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(dest), "-vf", "select=eq(n\\,29)", "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    x, y = round(320 * 0.7), round(180 * 0.6)  # inside the phone's screen (motion-core screenLayout, 16:9)
    r, g, b = frame[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]
    assert r > 180 and g < 80 and b < 90, f"the phone shows the red library image: {(r, g, b)}"
    assert "ffmpeg" in (state / "run.log").read_text(encoding="utf-8"), "the encode is logged"


@pytest.mark.skipif(not (shutil.which("node") and shutil.which("ffmpeg")), reason="needs Node 24+ and ffmpeg")
def test_the_node_renderer_draws_each_phone_its_own_image(state: Path) -> None:
    garden = library.create_workspace("Garden")["id"]
    red, green = _import(state, garden, "gallery.png", (230, 20, 30)), _import(state, garden, "floor-map.png", (20, 200, 40))
    blue = _import(state, "brand", "entrance.png", (20, 40, 230))
    doc = _doc({"mode": "animation", "layout": "screen", "backdrop": "black", "headline": "Walk",
                "screen": {"asset": red["id"], "more": [green["id"], blue["id"]]}}, garden)
    request = scene_render.render_request(scene_render.scene_specs(doc, WORDS)[0], width=320, height=180, fps=30, workspace_id=garden)
    dest = state / "three.mp4"
    scene_render.render_scene({**request, "durF": 45}, dest)
    frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(dest), "-vf", "select=eq(n\\,44)", "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout

    def px(x: int, y: int) -> tuple[int, ...]:
        return tuple(frame[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3])

    # Phone 1, then phone 2 on its right and phone 3 on its left, where each shows (motion-core screenLayout,
    # 16:9, three phones: centres (1340, 544), (1630, 593) and (1050, 593) at 1920 x 1080).
    r, g, b = px(223, 91)
    assert r > 180 and g < 80 and b < 90, f"phone 1 shows the red image: {(r, g, b)}"
    r, g, b = px(279, 99)
    assert g > 150 and r < 90 and b < 110, f"phone 2 shows the green image: {(r, g, b)}"
    r, g, b = px(168, 99)
    assert b > 180 and r < 90 and g < 110, f"phone 3 shows the blue image: {(r, g, b)}"


# ── the co-pilot ────────────────────────────────────────────────────────────────

def _tdoc() -> dict:
    return {"words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in WORDS], "segments": [], "fillers": []}


def _apply(doc: dict, ops: list[dict]) -> dict:
    return project_mod.to_dict(project_mod.apply_ops(project_mod.from_dict(doc), ops))


LIBRARY = [
    {"workspaceId": "garden-1", "workspaceName": "Garden", "asset": {"id": "a1b2c3d4e5f60718", "name": "notes.png", "kind": "image"}},
    {"workspaceId": "garden-1", "workspaceName": "Garden", "asset": {"id": "9999aaaa0000bbbb", "name": "rainfall.mp4", "kind": "video"}},
]


def test_set_shot_stages_a_scene() -> None:
    doc = _doc({"mode": "animation"})
    p = copilot.propose("set_shot", doc, _tdoc(), {"shots": [
        {"scene": "s1", "layout": "screen", "camera": "push-in", "screen": {"asset": "Notes"}, "backdrop": "vision"}]},
        assets=lambda ws: LIBRARY)
    shot = _apply(doc, p.ops)["scenes"][0]["shot"]
    assert shot == {"mode": "animation", "layout": "screen", "camera": "push-in", "screen": {"asset": "a1b2c3d4e5f60718"}, "backdrop": "vision"}
    assert p.marks[0]["label"] == "Animate “Launch” · screen · push-in"
    staged = _apply(doc, p.ops)
    titled = copilot.propose("set_shot", staged, _tdoc(), {"shots": [
        {"scene": "s1", "layout": "title", "screen": None, "camera": None, "mark": " ab ",
         "badges": [{"text": " ONE ", "tone": "ink"}, {"text": "TWO", "tone": "paper"}, {"text": "THREE"}]}]})
    assert _apply(staged, titled.ops)["scenes"][0]["shot"] == {
        "mode": "animation", "layout": "title", "backdrop": "vision", "mark": "AB",
        "badges": [{"text": "ONE", "tone": "ink"}, {"text": "TWO", "tone": "paper"}, {"text": "THREE", "tone": "ink"}]}


@pytest.mark.parametrize("given, message", [
    ({"screen": {"asset": "rainfall"}}, r"screen.asset: no library image named 'rainfall' \(have: notes.png\)"),
    ({"screen": "notes.png"}, "screen must be {asset"),
    ({"screen": {"asset": "Notes", "more": ["rainfall"]}}, r"screen.more\[0\]: no library image named 'rainfall'"),
    ({"screen": {"asset": "Notes", "more": ["notes"] * 3}}, "screen.more must list up to 2"),
    ({"screen": {"asset": "Notes", "more": "notes"}}, "screen.more must list up to 2"),
    ({"screen": {"asset": "Notes", "fit": "cover"}}, "screen must be {asset"),
    ({"badges": []}, "badges must list 1 to 4"),
    ({"badges": [{"text": "SAMPLE", "tone": "neon"}]}, r"badges\[0\].tone must be one of ink, paper, accent"),
    ({"badges": [{"label": "SAMPLE"}]}, r"badges\[0\] must be"),
    ({"mark": "HELLO"}, "mark must be 1 to 4 letters"),
    ({"layout": "split"}, "layout must be one of stage, screen, title, signoff"),
    ({"camera": "zoom"}, "camera must be one of push-in, pull-out"),
    ({"unknownField": "wide"}, "unknown fields: unknownField"),
])
def test_set_shot_validates_staging(given: dict, message: str) -> None:
    with pytest.raises(copilot.ToolError, match=message):
        copilot.propose("set_shot", _doc({"mode": "animation"}), _tdoc(), {"shots": [{"scene": "s1", **given}]},
                        assets=lambda ws: LIBRARY)


def test_set_shot_puts_up_to_three_phones_on_a_screen() -> None:
    library = [*LIBRARY, {"workspaceId": "brand", "workspaceName": "Brand",
                          "asset": {"id": "0f1e2d3c4b5a6978", "name": "floor-map.jpg", "kind": "image"}}]
    doc = _doc({"mode": "animation", "layout": "screen"})
    p = copilot.propose("set_shot", doc, _tdoc(), {"shots": [
        {"scene": "s1", "screen": {"asset": "notes", "more": ["FLOOR-MAP", "0f1e2d3c4b5a6978"]}}]}, assets=lambda ws: library)
    assert _apply(doc, p.ops)["scenes"][0]["shot"]["screen"] == {
        "asset": "a1b2c3d4e5f60718", "more": ["0f1e2d3c4b5a6978", "0f1e2d3c4b5a6978"]}
    one = copilot.propose("set_shot", doc, _tdoc(), {"shots": [{"scene": "s1", "screen": {"asset": "notes", "more": []}}]},
                          assets=lambda ws: library)
    assert _apply(doc, one.ops)["scenes"][0]["shot"]["screen"] == {"asset": "a1b2c3d4e5f60718"}, "no more: one phone"


def test_set_shot_tool_schema_carries_the_staging_fields(monkeypatch) -> None:
    sdk = pytest.importorskip("claude_agent_sdk")
    registered: list = []
    monkeypatch.setattr(sdk, "create_sdk_mcp_server", lambda name, version, tools: registered.extend(tools))
    ctx = agent_host.ToolCtx(slug="zzstage", read_doc=lambda: (_doc(None), 1), read_transcript=_tdoc,
                             fps=lambda: 30, emit=lambda e: None)
    agent_host.build_editor_server(ctx)
    items = next(t for t in registered if t.name == "set_shot").input_schema["properties"]["shots"]["items"]["properties"]
    assert items["layout"]["enum"] == list(project_mod.SHOT_LAYOUTS) and items["camera"]["enum"] == list(project_mod.SHOT_CAMERAS)
    assert set(staging.TOOL_PROPS) <= set(items) and items["badges"]["items"]["properties"]["tone"]["enum"] == list(project_mod.BADGE_TONES)
    assert "vision" in items["backdrop"]["enum"] and "black" in items["backdrop"]["enum"]
