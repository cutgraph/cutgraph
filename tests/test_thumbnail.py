"""A video's thumbnail: the document field, the default rule and profile resolution the export
shares with the preview through tests/fixtures/thumbnail_parity.json, the PNG the
Node renderer writes into the content-addressed cache, and the Studio routes that render and serve it."""
from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

import httpx
import pytest

from pipeline.errors import ValidationError

import config
from pipeline import project as project_mod
from pipeline import thumbnail
from scripts import studio_app
from studio.routes import workspaces

FIXTURE = Path(__file__).parent / "fixtures" / "thumbnail_parity.json"


def _fx() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _doc(**extra: object) -> dict:
    return {"schemaVersion": 3, "name": "talk", "fps": 30, "width": 1920, "height": 1080, "revision": 1,
            "transcript": None, "tracks": [], **extra}


def _check(thumbnail_doc: object) -> None:
    project_mod.validate(project_mod.from_dict(_doc(schemaVersion=project_mod.CURRENT_SCHEMA_VERSION,
                                                  thumbnail=thumbnail_doc)))


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


# ── the document ─────────────────────────────────────────────────────────────────

def test_a_thumbnail_round_trips_and_a_video_without_one_is_unchanged() -> None:
    stored = {"headline": "Museum Visit", "eyebrow": "New", "badges": [{"text": "EP 1", "tone": "ink"}],
              "character": "default-character", "pose": "cheer", "expression": "excited",
              "ensemble": [{"character": "notes-guest", "pose": "flex", "expression": "proud"}], "backdrop": "vision"}
    doc = _doc(thumbnail=stored)
    proj = project_mod.from_dict(doc)
    project_mod.validate(proj)
    assert project_mod.to_dict(proj)["thumbnail"] == stored
    assert "thumbnail" not in project_mod.to_dict(project_mod.from_dict(_doc()))
    _check({})


def test_a_member_may_carry_a_label_over_its_head() -> None:
    """Labels sit over two ensemble members, with an unlabelled member between them."""
    stored = {"headline": "The Museum Visit", "character": "default-character", "ensemble": [
        {"character": "visitor-reader", "badge": {"text": "ONE", "tone": "accent"}},
        {"character": "notes-guest"},
        {"character": "reviewer", "badge": {"text": "TWO", "tone": "accent"}},
    ]}
    proj = project_mod.from_dict(_doc(thumbnail=stored))
    project_mod.validate(proj)
    assert project_mod.to_dict(proj)["thumbnail"] == stored
    resolved = thumbnail.render_request(_doc(thumbnail=stored), None, "16:9")["poster"]["ensemble"]
    assert [m.get("badge") for m in resolved] == [{"text": "ONE", "tone": "accent"}, None, {"text": "TWO", "tone": "accent"}]


@pytest.mark.parametrize("bad, message", [
    ("Garden", "thumbnail must be an object"),
    ({"mode": "animation"}, "unknown fields: ['mode']"),
    ({"ensemble": [{"character": "a", "move": "hop"}]}, "unknown fields: ['move']"),
    ({"ensemble": [{"character": "a", "bubble": "think"}]}, "unknown fields: ['bubble']"),
    ({"ensemble": [{"character": "a"}] * 4}, "at most 3"),
    ({"ensemble": [{"pose": "flex"}]}, "must name a character"),
    ({"badges": [{"text": "NEW", "tone": "plaid"}]}, "tone must be one of"),
    ({"headline": "x" * 61}, "longer than 60"),
    ({"eyebrow": "x" * 41}, "longer than 40"),
    ({"pose": 3}, "must be a string"),
    ({"ensemble": [{"character": "a", "badge": {"text": "ONE"}}]}, "badge must be {text, tone}"),
    ({"ensemble": [{"character": "a", "badge": {"text": " ", "tone": "ink"}}]}, "badge text must be 1 to 24"),
    ({"ensemble": [{"character": "a", "badge": {"text": "TWO", "tone": "neon"}}]}, "badge tone must be one of"),
])
def test_a_thumbnail_holds_only_what_a_poster_draws_by_the_shot_rules(bad: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message.replace("[", r"\[").replace("]", r"\]")):
        _check(bad)


# ── defaults and resolution ──────────────────────────────────────────────────────

def test_render_requests_match_the_parity_fixture() -> None:
    fx = _fx()
    for case in fx["cases"]:
        assert thumbnail.render_request(case["project"], fx["cast"], case["format"]) == case["request"], case["name"]


def test_the_fixture_covers_the_default_rule_and_the_edge_cases() -> None:
    posters = [c["request"]["poster"] for c in _fx()["cases"]]
    defaults, stored, quiet, unseen, missing = posters
    assert defaults["headline"] == "Museum Visit" and defaults["eyebrow"] == "" and defaults["character"] == "lens"
    assert [m["palette"] for m in defaults["ensemble"]] == ["periwinkle", "periwinkle", "periwinkle"], "the rest of the cast, in cast order"
    assert stored["custom"] and stored["ensemble"][0]["custom"], "custom faces on the lead and a member"
    assert "expression" not in stored["ensemble"][2], "a face the member's profile doesn't have is dropped"
    assert quiet["headline"] == "quiet-video" and quiet["ensemble"] == [] and quiet["palette"] == "periwinkle"
    assert unseen["character"] == "lens" and unseen["pose"] == "shrug", "an unseen lead is the default; its supported pose is retained"
    assert unseen["ensemble"][0]["character"] == "lens", "the default lead is left out of the ones behind it"
    assert missing["character"] == missing["ensemble"][0]["character"] == "retired-rig"
    assert "custom" not in missing and "custom" not in missing["ensemble"][0]


def test_first_lead_is_the_first_animated_scenes_character() -> None:
    scenes = [{"shot": {"mode": "footage", "character": "a"}}, {"shot": {"mode": "cameo"}}, {"shot": {"mode": "animation", "character": "b"}}]
    assert thumbnail.first_lead({"scenes": scenes}) is None
    assert thumbnail.first_lead({"scenes": scenes[:1] + scenes[2:]}) == "b"
    assert thumbnail.first_lead({}) is None


# ── rendering ────────────────────────────────────────────────────────────────────

@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "outputs" / "projects")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    return tmp_path


def test_ensure_thumbnail_renders_each_format_once_into_the_cache(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc(title="Museum Visit", thumbnail={"backdrop": "vision"})
    wide = thumbnail.ensure_thumbnail(doc, "16:9")
    tall = thumbnail.ensure_thumbnail(doc, "9:16")
    assert wide.parent == state / "outputs" / "thumbnails"
    assert (_png_size(wide), _png_size(tall)) == ((1280, 720), (1080, 1920))
    assert not list(wide.parent.glob("*.partial*"))

    def no_render(request: dict, dest: Path) -> dict:
        raise AssertionError("rendered again")

    monkeypatch.setattr(thumbnail, "render_thumbnail", no_render)
    assert thumbnail.ensure_thumbnail(doc, "16:9") == wide, "an unchanged thumbnail comes from the cache"
    with pytest.raises(AssertionError, match="rendered again"):
        thumbnail.ensure_thumbnail(_doc(title="Another title", thumbnail={}), "16:9")


def test_a_failed_render_leaves_nothing_behind(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(thumbnail, "RENDERER", state / "missing.ts")
    request = thumbnail.render_request(_doc(), {}, "16:9")
    with pytest.raises(thumbnail.ThumbnailRenderError):
        thumbnail.render_thumbnail(request, state / "outputs" / "thumbnails" / "x.png")
    assert not list((state / "outputs" / "thumbnails").glob("*"))


def _call(method: str, url: str, **kw: object) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=studio_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
            return await client.request(method, url, **kw)  # type: ignore[arg-type]
    return asyncio.run(go())


def test_the_render_route_renders_the_saved_thumbnail_and_the_media_route_serves_it(state: Path) -> None:
    config.PROJECTS_DIR.mkdir(parents=True)
    project_mod.write_project(config.PROJECTS_DIR / "talk.json", project_mod.from_dict(_doc(thumbnail={"headline": "Hi"})))
    res = _call("POST", "/api/project/talk/thumbnail/render", json={"format": "9:16"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["format"], body["width"], body["height"]) == ("9:16", 1080, 1920)
    png = _call("GET", body["url"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"


@pytest.mark.parametrize("url, payload, status", [
    ("/api/project/talk/thumbnail/render", {"format": "1:1"}, 400),
    ("/api/project/talk/thumbnail/render", {}, 400),
    ("/api/project/nobody/thumbnail/render", {"format": "16:9"}, 404),
])
def test_the_render_route_refuses_unknown_formats_and_videos(state: Path, url: str, payload: dict, status: int) -> None:
    config.PROJECTS_DIR.mkdir(parents=True)
    project_mod.write_project(config.PROJECTS_DIR / "talk.json", project_mod.from_dict(_doc()))
    assert _call("POST", url, json=payload).status_code == status


@pytest.mark.parametrize("name", ["../talk.json", "abc.png", "0" * 64 + ".jpg", "0" * 64 + ".png"])
def test_the_media_route_serves_only_rendered_thumbnails(state: Path, name: str) -> None:
    assert _call("GET", f"/media/thumbnail/{name}").status_code == 404


def test_a_video_with_a_thumbnail_is_covered_by_it() -> None:
    assert workspaces._video_summary("talk", _doc(thumbnail={}), 0.0)["thumbnail"] is True
    assert "thumbnail" not in workspaces._video_summary("talk", _doc(), 0.0)
