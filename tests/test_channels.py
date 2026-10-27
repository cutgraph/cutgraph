"""channels.py loader — profile lookup + captionStyle merge over the config caption defaults.

Uses a temp channels.json via monkeypatching config.CHANNELS_JSON so the test is hermetic and does not
depend on the checked-in file's exact colors."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

import config
from pipeline import channels
from pipeline import compile as compile_mod

_DOC = {
    "channels": {
        "studio": {
            "name": "Paper folding",
            "voice": "friendly, clear, step-by-step",
            "audience": "beginners learning paper folding",
            "accentColor": "&H00CCFF66",
            "captionStyle": {"activeColor": "&H00CCFF66", "wordsPerPage": 4},
        }
    }
}


@pytest.fixture(autouse=True)
def _example(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CHANNELS_EXAMPLE_JSON", tmp_path / "channels.example.json")


def _write(tmp_path, doc: dict):
    path = tmp_path / "channels.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_load_parses_the_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CHANNELS_JSON", _write(tmp_path, _DOC))
    assert channels.load()["channels"]["studio"]["name"] == "Paper folding"


def test_load_missing_file_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CHANNELS_JSON", tmp_path / "does-not-exist.json")
    assert channels.load() == {}


def test_profile_returns_channel_or_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CHANNELS_JSON", _write(tmp_path, _DOC))
    prof = channels.profile("studio")
    assert prof is not None and prof["voice"] == "friendly, clear, step-by-step"
    assert channels.profile("unknown") is None
    assert channels.profile(None) is None


def test_resolve_caption_style_merges_channel_over_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CHANNELS_JSON", _write(tmp_path, _DOC))
    style = channels.resolve_caption_style("studio")
    # channel overrides:
    assert style["activeColor"] == "&H00CCFF66"
    assert style["wordsPerPage"] == 4
    # unspecified fields fall back to config defaults:
    assert style["baseColor"] == config.CAPTION_COLOR_DEFAULT
    assert style["font"] == config.CAPTION_FONT
    # every key build_karaoke_ass reads is present:
    for key in ("wordsPerPage", "font", "bold", "baseColor", "activeColor",
                "emphasisColor", "marginVPct", "alignment", "outlineRatio"):
        assert key in style


def test_resolve_caption_style_unknown_channel_is_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CHANNELS_JSON", _write(tmp_path, _DOC))
    style = channels.resolve_caption_style(None)
    assert style["activeColor"] == config.CAPTION_COLOR_ACTIVE
    assert style["baseColor"] == config.CAPTION_COLOR_DEFAULT


def test_resolve_caption_style_missing_file_is_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CHANNELS_JSON", tmp_path / "nope.json")
    style = channels.resolve_caption_style("studio")
    assert style["activeColor"] == config.CAPTION_COLOR_ACTIVE


def test_checked_in_example_has_both_channels() -> None:
    doc = json.loads((config.CODE_ROOT / "channels.example.json").read_text(encoding="utf-8"))
    assert set(doc["channels"]) == {"quick-tips", "deep-dives"}
    for prof in doc["channels"].values():
        for key in ("name", "voice", "audience", "accentColor", "captionStyle"):
            assert key in prof


def test_state_override_takes_precedence_and_missing_override_uses_example(tmp_path, monkeypatch) -> None:
    override = tmp_path / "channels.json"
    example = _write(tmp_path, {"channels": {"sample": {"name": "Sample"}}})
    example = example.rename(tmp_path / "channels.example.json")
    monkeypatch.setattr(config, "CHANNELS_JSON", override)
    monkeypatch.setattr(config, "CHANNELS_EXAMPLE_JSON", example)
    assert channels.profile("sample") == {"name": "Sample"}
    override.write_text(json.dumps(_DOC), encoding="utf-8")
    assert channels.load() == _DOC
    override.write_text("{invalid", encoding="utf-8")
    assert channels.load() == {}, "an invalid owner override must not select example channels"


def test_channels_api_serves_effective_profiles(tmp_path, monkeypatch) -> None:
    import asyncio

    import httpx

    from studio.app import app

    monkeypatch.setattr(config, "CHANNELS_JSON", _write(tmp_path, _DOC))

    async def call() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/channels")
            assert response.status_code == 200
            assert response.json() == _DOC

    asyncio.run(call())


_INVALID_STYLES = [
    ("activeColor", 5), ("baseColor", "invalid"), ("emphasisColor", []),
    ("wordsPerPage", "x"), ("wordsPerPage", 0), ("wordsPerPage", True),
    ("font", None), ("bold", "false"), ("look", []),
    ("marginVPct", "x"), ("marginVPct", -1), ("marginVPct", float("nan")),
    ("alignment", "x"), ("alignment", 10), ("outlineRatio", "x"),
    ("outlineRatio", 0), ("outlineRatio", float("inf")),
]
_OVERRIDES = [
    pytest.param(json.dumps(_DOC).encode("utf-16"), {}, id="utf16"),
    pytest.param('{"channels": {"studio": {"name": "caf\u00e9"}}}'.encode("cp1252"), {}, id="cp1252"),
    pytest.param(json.dumps(_DOC).encode("utf-8-sig"), _DOC, id="utf8-bom"),
    pytest.param(b'{"channels": []}', {}, id="channels-list"),
    pytest.param(b'{"channels": null}', {}, id="channels-null"),
    pytest.param(b'[]', {}, id="top-list"),
    pytest.param(json.dumps({"channels": {"invalid": [], **_DOC["channels"]}}).encode(), _DOC,
                 id="mixed-profiles"),
    pytest.param(b'{"channels": {"studio": {"captionStyle": []}}}',
                 {"channels": {"studio": {}}}, id="style-list"),
    *[pytest.param(json.dumps({"channels": {"studio": {"captionStyle": {
        "look": "outline", key: value,
    }}}}).encode(), {"channels": {"studio": {"captionStyle": {
        **({"look": "outline"} if key != "look" else {}),
    }}}}, id=f"style-{key}-{i}") for i, (key, value) in enumerate(_INVALID_STYLES)],
]


@pytest.fixture(scope="module")
def caption_media(tmp_path_factory):
    src = tmp_path_factory.mktemp("channel-media") / "rec.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
        check=True, capture_output=True, text=True,
    )
    return src


@pytest.mark.parametrize("payload, expected", _OVERRIDES)
def test_override_is_safe_for_api_and_caption_export(payload, expected, tmp_path, monkeypatch, caption_media) -> None:
    import asyncio

    import httpx

    from studio.app import app

    path = tmp_path / "channels.json"
    path.write_bytes(payload)
    monkeypatch.setattr(config, "CHANNELS_JSON", path)
    assert channels.load() == expected
    style = channels.resolve_caption_style("studio")
    assert style == {**channels.resolve_caption_style(None),
                     **expected.get("channels", {}).get("studio", {}).get("captionStyle", {})}

    async def call() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/channels")
            assert response.status_code == 200
            assert response.json() == expected

    asyncio.run(call())
    (tmp_path / "media").mkdir()
    shutil.copyfile(caption_media, tmp_path / "media" / "rec.mp4")
    transcripts = tmp_path / "outputs" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "rec.words.json").write_text(json.dumps({"segments": [{"words": [
        {"word": "one", "start": 0.0, "end": 0.4},
        {"word": "two", "start": 0.4, "end": 0.9},
    ]}]}), encoding="utf-8")
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    doc = {
        "schemaVersion": 1, "name": "caption-export", "fps": 30, "width": 320, "height": 240,
        "revision": 0, "transcript": None, "channel": "studio", "captions": {"enabled": True},
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": "clip", "media": {"source": "media/rec.mp4", "proxy": None},
             "srcInF": 0, "srcOutF": 30, "tlStartF": 0, "gainDb": 0.0}]}],
    }
    dest = tmp_path / "final" / "caption-export.mp4"
    result = compile_mod.compile_project(doc, dest, project_dir=tmp_path)
    assert result.status == "ok", result.details
    assert dest.is_file() and dest.stat().st_size > 0
    ass = (tmp_path / "outputs" / "captions" / "caption-export.ass").read_text(encoding="utf-8")
    assert "Dialogue:" in ass and "one" in ass and "two" in ass
    assert ("Style: Default" if style["look"] == "outline" else "Style: Pill") in ass
