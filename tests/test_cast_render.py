"""Characters and custom expressions in the export: `render_request` resolves a
scene's character profile and custom expression against its video's cast, so the Node renderer draws
the same character and face as the preview, the scene cache follows the resolved rig and spec, and a
character or expression the video can't see falls back. The shared fixture holds the TS preview
(ui/test/v3CastParity.test.ts) to the same resolution."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import config
from pipeline import cast, library, scene_render

FIXTURE = Path(__file__).parent / "fixtures" / "cast_shot_parity.json"
MEDIA = "data/source/talk.mp4"
WORDS = [{"word": f"w{i}", "start": i * 0.5 + 0.1, "end": i * 0.5 + 0.4} for i in range(12)]
SMUG = {"eyes": "side", "mouth": "smile"}
# Without a brand in the cast, the eyebrow is ours (motion-core EYEBROW / ACCENT).
OURS = {"eyebrow": config.BRAND_EYEBROW, "accent": config.BRAND_ACCENT}
DEFAULT_SHOT = {"character": "lens", "palette": "periwinkle", **OURS}


def _doc(expression: str | None, character: str | None = None, workspace: str | None = None) -> dict:
    shot = {"mode": "cameo", **({"character": character} if character else {}),
            **({"expression": expression} if expression else {})}
    return {
        "schemaVersion": 3, "name": "zzcast", "fps": 30, "width": 320, "height": 180, "revision": 1, "transcript": None,
        **({"workspaceId": workspace} if workspace else {}),
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": "c0", "media": {"source": MEDIA, "proxy": None}, "srcInF": 0, "srcOutF": 180, "tlStartF": 0, "gainDb": 0.0},
        ]}],
        "scenes": [
            {"id": "s1", "title": "Intro", "role": "Hook", "startWord": 0, "shot": shot},
            {"id": "s2", "title": "Middle", "role": "Beat", "startWord": 6},
        ],
    }


def _profile(cid: str, **expressions: dict) -> dict:
    return {"id": cid, "name": cid, "rig": "lens", "palette": "periwinkle", "art": [],
            "expressions": [{"id": k, "name": k, "spec": v} for k, v in expressions.items()]}


def _cast(*extra: dict, **expressions: dict) -> dict:
    """A video cast whose Lens (the default) has these custom expressions."""
    return {"characters": [_profile(cast.DEFAULT_CHARACTER_ID, **expressions), *extra], "default": cast.DEFAULT_CHARACTER_ID, "workspace": None}


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    transcripts = tmp_path / "outputs" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "talk.words.json").write_text(json.dumps({"segments": [{"words": WORDS}]}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def renders(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake(request: dict, dest: Path) -> dict:
        calls.append(request)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"scene")
        return {"event": "done"}

    monkeypatch.setattr(scene_render, "render_scene", fake)
    return calls


def _request(expression: str | None, video_cast: dict | None, character: str | None = None) -> dict:
    spec = scene_render.scene_specs(_doc(expression, character), WORDS)[0]
    return scene_render.render_request(spec, width=320, height=180, fps=30, cast=video_cast)


def test_the_shared_fixture_resolves_like_the_preview(client_rig: None) -> None:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in fx["cases"]:
        assert cast.resolve_shot(case["shot"], fx["cast"]) == (case["expected"]["shot"], case["expected"]["custom"]), case


def test_the_shared_fixture_casts_a_non_default_profile_on_the_default_character_rig() -> None:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    guest = next(c for c in fx["cast"]["characters"] if c["id"] == "notes-guest")
    assert guest["rig"] == "lens" and guest["workspaceId"] == fx["cast"]["workspace"]["id"] != "brand"
    assert fx["cast"]["default"] != "notes-guest"
    cases = [c for c in fx["cases"] if (c["shot"] or {}).get("character") == "notes-guest"]
    assert any(c["expected"]["custom"] for c in cases), "its own custom expression"
    assert any(c["shot"].get("expression") == "smug" and "expression" not in c["expected"]["shot"] for c in cases), \
        "never another character's"


def test_render_request_carries_the_rig_and_the_custom_spec() -> None:
    request = _request("smug", _cast(smug=SMUG))
    assert request["shot"] == {"mode": "cameo", "expression": "smug", **DEFAULT_SHOT}
    assert request["custom"] == SMUG
    builtin = _request("happy", _cast(smug=SMUG))
    assert "custom" not in builtin and builtin["shot"]["expression"] == "happy"
    assert _request(None, None)["shot"] == {"mode": "cameo", **DEFAULT_SHOT}, "no cast: the default rig"


def test_a_deleted_custom_expression_renders_the_role_look() -> None:
    gone = _request("smug", _cast())
    assert gone["shot"] == {"mode": "cameo", **DEFAULT_SHOT} and "custom" not in gone
    assert scene_render.cache_key(gone, "v") == scene_render.cache_key(_request(None, _cast()), "v"), \
        "the same pixels as a scene that never picked an expression"


def test_the_cache_key_follows_the_resolved_rig_and_spec() -> None:
    key = scene_render.cache_key(_request("smug", _cast(smug=SMUG)), "v")
    assert scene_render.cache_key(_request("smug", _cast(smug={**SMUG, "mouth": "grin"})), "v") != key
    assert scene_render.cache_key(_request("smug", _cast(smug=dict(SMUG))), "v") == key
    same_face = _request("smug", _cast(_profile("guest", smug=SMUG)), "guest")
    assert scene_render.cache_key(same_face, "v") == key, "another profile on the same rig with the same face draws the same pixels"
    request = _request("smug", _cast(smug=SMUG))
    assert scene_render.cache_key({**request, "shot": {**request["shot"], "character": "other-rig"}}, "v") != key


def test_ensure_scenes_casts_from_the_videos_workspace(state: Path, renders: list[dict]) -> None:
    garden = library.create_workspace("Garden")["id"]
    cast.create_character(garden, "Guest")
    cast.create_expression("guest", "Grin", SMUG)
    scene_render.ensure_scenes(_doc("grin", "guest", garden), root=state)
    assert renders[-1]["shot"]["character"] == "lens" and renders[-1]["custom"] == SMUG
    cast.update_expression("guest", "grin", spec={**SMUG, "mouth": "grin"})
    _layers, rendered = scene_render.ensure_scenes(_doc("grin", "guest", garden), root=state)
    assert rendered == 1 and renders[-1]["custom"]["mouth"] == "grin", "an edited expression re-renders"
    scene_render.ensure_scenes(_doc("grin", "guest"), root=state)
    assert renders[-1]["shot"] == {"mode": "cameo", **DEFAULT_SHOT} and "custom" not in renders[-1], \
        "outside Garden the Guest and its face are never cast"
    cast.create_expression(cast.DEFAULT_CHARACTER_ID, "Smug", SMUG)
    scene_render.ensure_scenes(_doc("smug"), root=state)
    assert renders[-1]["custom"] == SMUG, "the brand's Lens, everywhere"


@pytest.mark.skipif(not (shutil.which("node") and shutil.which("ffmpeg")), reason="needs Node 24+ and ffmpeg")
def test_the_node_renderer_draws_the_custom_face(state: Path) -> None:
    custom = _request("smug", _cast(smug={"eyes": "arc", "mouth": "grin"}))
    happy = _request("happy", _cast())
    out = {}
    for name, request in (("custom", custom), ("happy", happy), ("role", _request(None, _cast()))):
        dest = state / f"{name}.mov"
        scene_render.render_scene({**request, "durF": 20}, dest)
        out[name] = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(dest), "-vf", "select=eq(n\\,18)", "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout
    assert out["custom"] == out["happy"], "the custom spec {arc, grin} is the built-in happy face"
    assert out["custom"] != out["role"], "and not the Hook role's confused face"


def test_the_shared_fixture_carries_the_garden_brand_or_the_shots_own_eyebrow() -> None:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    brand = fx["cast"]["brand"]
    assert brand == {"name": "MUSEUM VISIT", "accent": "#2bb3a3"}
    shots = [(c["shot"] or {}, c["expected"]["shot"]) for c in fx["cases"]]
    assert all(want["accent"] == brand["accent"] for _stored, want in shots)
    assert all(want["eyebrow"] == stored.get("eyebrow", brand["name"]) for stored, want in shots)
    assert {stored.get("eyebrow") for stored, _want in shots} >= {None, "", "FIELD NOTES"}, "the brand's, a shot's own, hidden"


def test_the_render_request_and_cache_key_follow_the_eyebrow() -> None:
    workspace_cast = {**_cast(), "brand": {"name": "MUSEUM VISIT", "accent": "#2bb3a3"}}
    request = _request(None, workspace_cast)
    assert (request["shot"]["eyebrow"], request["shot"]["accent"]) == ("MUSEUM VISIT", "#2bb3a3")
    keys = {scene_render.cache_key(r, "v") for r in (
        request, _request(None, _cast()), _request(None, {**workspace_cast, "brand": {**workspace_cast["brand"], "accent": "#ffffff"}}))}
    assert len(keys) == 3, "the brand's name and accent are in the cache key"
    own = scene_render.scene_specs(_doc(None), WORDS)[0]
    for eyebrow in ("FIELD NOTES", ""):
        spec = {**own, "shot": {**own["shot"], "eyebrow": eyebrow}}
        shot = scene_render.render_request(spec, width=320, height=180, fps=30, cast=workspace_cast)["shot"]
        assert (shot["eyebrow"], shot["accent"]) == (eyebrow, "#2bb3a3"), "the shot's own eyebrow wins, in the brand's colour"


def test_the_shared_fixture_casts_the_synthetic_rig_in_its_palette(client_rig: None) -> None:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    guest = next(c for c in fx["cast"]["characters"] if c["id"] == "visitor-guide")
    assert (guest["rig"], guest["palette"]) == ("sample-rig", "fern")
    cases = [c for c in fx["cases"] if (c["shot"] or {}).get("character") == "visitor-guide"]
    assert {(c["expected"]["shot"]["character"], c["expected"]["shot"]["palette"]) for c in cases} == {("sample-rig", "fern")}
    assert any(c["shot"].get("pose") == "wave" and c["expected"]["shot"]["pose"] == "wave" for c in cases)
    assert any(c["expected"]["custom"] == {"brows": "raised", "eyes": "open", "mouth": "closed"} for c in cases)
    dropped = next(c for c in cases if c["shot"].get("pose") == "not-a-pose")
    assert "pose" not in dropped["expected"]["shot"] and "expression" not in dropped["expected"]["shot"], \
        "the default rig's own pose and expression fall back to the role look"


def _frame(path: Path, n: int) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", rf"select=eq(n\,{n})", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout


def _near(rgba: bytes, hex_colour: str, tol: int = 8) -> int:
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return sum(1 for i in range(0, len(rgba), 4) if rgba[i + 3] > 250
               and abs(rgba[i] - r) <= tol and abs(rgba[i + 1] - g) <= tol and abs(rgba[i + 2] - b) <= tol)


@pytest.mark.skipif(not (shutil.which("node") and shutil.which("ffmpeg")), reason="needs Node 24+ and ffmpeg")
def test_the_node_renderer_draws_the_default_palette(state: Path) -> None:
    profile = {"id": "guest", "name": "Guest", "rig": "lens", "palette": "periwinkle", "art": [], "expressions": []}
    request = _request("proud", _cast(profile), "guest")
    assert request["shot"] == {"mode": "cameo", "character": "lens", "palette": "periwinkle", "expression": "proud", **OURS}
    dest = state / "character.mov"
    scene_render.render_scene({**request, "durF": 20, "width": 640, "height": 360}, dest)
    assert _near(_frame(dest, 18), "#9fbae9") > 300
