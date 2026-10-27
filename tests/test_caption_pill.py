"""Pill captions in the export (caption_burn.build_pill_ass, burned by compile.build_plan):
a rounded rgba(8,10,12,.72) box per page and one Dialogue per word-state interval with
the spoken word #f5c518, spoken words #f4f6f7 and upcoming words at 50%. It is the default for project
captions; a channel or project caption style with `look: "outline"` keeps the karaoke look.

Isolated: no ffmpeg; every dir is redirected under tmp_path."""
from __future__ import annotations

import json
import re

import config
from pipeline import caption_burn as cb
from pipeline import caption_pages, channels
from pipeline import compile as compile_mod

_PAGE = {"on": 100, "off": 250, "words": [
    {"w": "one", "start": 100, "end": 130},
    {"w": "two", "start": 140, "end": 170},
    {"w": "three", "start": 170, "end": 200},
]}

_WORDS_DOC = {"segments": [{"words": [
    {"word": " alpha", "start": 10.0, "end": 10.4},
    {"word": " beta", "start": 10.4, "end": 10.8},
    {"word": " gamma", "start": 11.0, "end": 11.4},
    {"word": " delta", "start": 11.4, "end": 11.8},
    {"word": " epsilon", "start": 12.0, "end": 12.4},
    {"word": " zeta", "start": 13.7, "end": 14.0},
]}]}


def _dialogues(doc: str, layer: int) -> list[str]:
    return [ln for ln in doc.splitlines() if ln.startswith(f"Dialogue: {layer},")]


def _numbers(path: str) -> list[float]:
    return [float(v) for v in re.findall(r"-?\d+\.\d+", path)]


def test_pill_style_is_bold_inter_with_no_outline_or_shadow() -> None:
    doc = cb.build_pill_ass([_PAGE], 1920, 1080)
    style = next(ln for ln in doc.splitlines() if ln.startswith("Style: "))
    fields = style[len("Style: "):].split(",")
    assert fields[:3] == ["Pill", "Inter", f"{46 * 2478 / 2048:.2f}"]  # an em of 46 px at 1080p
    assert fields[7] == "1"                                              # bold
    assert fields[15:18] == ["1", "0", "0"]                              # plain text: no outline, no shadow
    assert "WrapStyle: 2" in doc                                         # one line, never wrapped


def test_pill_box_is_a_rounded_rect_behind_each_page() -> None:
    doc = cb.build_pill_ass([_PAGE], 1920, 1080)
    [box] = _dialogues(doc, 0)
    assert box.startswith("Dialogue: 0,0:00:01.00,0:00:02.50,Pill,")
    assert "{\\an7\\pos(0,0)\\p1\\1c&H0C0A08&\\1a&H47&}m " in box        # rgba(8,10,12,.72)
    xs, ys = _numbers(box.split("}m ", 1)[1])[0::2], _numbers(box.split("}m ", 1)[1])[1::2]
    cap_y, fs = caption_pages.layout(1920, 1080)
    left, right = min(xs), max(xs)
    assert abs((left + right) / 2 - 960) < 0.01                          # centred
    assert abs((right - left) - (cb._pill_text_width("one two three", fs) + 1.2 * fs)) < 0.02
    assert abs(min(ys) - (cap_y - 1.1 * fs)) < 0.01 and abs(max(ys) - (cap_y + 0.5 * fs)) < 0.01
    assert " b " in box                                                  # rounded corners
    assert abs(_numbers(box.split("}m ", 1)[1])[0] - (left + 0.35 * fs)) < 0.01


def test_pill_words_change_state_on_every_word_boundary() -> None:
    doc = cb.build_pill_ass([_PAGE], 1920, 1080)
    now, said, next_ = "{\\1c&H18C5F5&\\1a&H00&}", "{\\1c&HF7F6F4&\\1a&H00&}", "{\\1c&HF7F6F4&\\1a&H80&}"
    expected = [
        ("0:00:01.00", "0:00:01.30", [now, next_, next_]),
        ("0:00:01.30", "0:00:01.40", [said, next_, next_]),
        ("0:00:01.40", "0:00:01.70", [said, now, next_]),
        ("0:00:01.70", "0:00:02.00", [said, said, now]),
        ("0:00:02.00", "0:00:02.50", [said, said, said]),
    ]
    lines = _dialogues(doc, 1)
    assert len(lines) == len(expected)
    for line, (start, end, tags) in zip(lines, expected):
        assert line.startswith(f"Dialogue: 1,{start},{end},Pill,,0,0,0,,")
        text = line.split(",,0,0,0,,", 1)[1]
        assert text == (f"{{\\an2\\pos(960.00,{1080 * 0.935 + 46 * 494 / 2048:.2f})}}"  # baseline + a descent
                        + " ".join(f"{t}{w}" for t, w in zip(tags, ("one", "two", "three"))))


def test_pill_matches_preview_layout_at_9_16() -> None:
    doc = cb.build_pill_ass([_PAGE], 1080, 1920)
    assert f"Style: Pill,Inter,{58 * 2478 / 2048:.2f}," in doc
    assert f"\\pos(540.00,{1920 * 0.9 + 58 * 494 / 2048:.2f})" in doc


def test_pill_word_text_is_escaped() -> None:
    page = {"on": 0, "off": 50, "words": [{"w": "a{b}\\c", "start": 0, "end": 50}]}
    doc = cb.build_pill_ass([page], 1920, 1080)
    assert "}a(b)/c" in doc and "a{b}" not in doc


def _doc(*, captions: dict, channel: str | None = None) -> dict:
    d: dict = {
        "schemaVersion": 1, "name": "zzpill", "fps": 30, "width": 1920, "height": 1080,
        "revision": 0, "transcript": None, "captions": captions,
        "scenes": [{"id": "a", "title": "A", "role": "Hook", "startWord": 0},
                   {"id": "b", "title": "B", "role": "Wrap", "startWord": 3}],
        "tracks": [{"id": "sp", "kind": "speech", "clips": [
            # keeps src 10-10.97s (alpha, beta; gamma cut away), then 11.4-14s (delta..zeta) from tl 0.97s
            {"id": "s0", "media": {"source": "media/rec.mp4", "proxy": None},
             "srcInF": 300, "srcOutF": 329, "tlStartF": 0, "gainDb": 0.0},
            {"id": "s1", "media": {"source": "media/rec.mp4", "proxy": None},
             "srcInF": 342, "srcOutF": 420, "tlStartF": 29, "gainDb": 0.0},
        ]}],
    }
    if channel is not None:
        d["channel"] = channel
    return d


def _redirect(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "CAPTIONS_DIR", tmp_path / "captions")
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    (tmp_path / "transcripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "transcripts" / "rec.words.json").write_text(json.dumps(_WORDS_DOC), encoding="utf-8")


def _ass(tmp_path) -> str:
    return (tmp_path / "captions" / "zzpill.ass").read_text(encoding="utf-8")


def test_compile_burns_the_pill_by_default(monkeypatch, tmp_path) -> None:
    _redirect(monkeypatch, tmp_path)
    fg = compile_mod.build_plan(_doc(captions={"enabled": True})).filter_complex
    assert "]ass=captions/zzpill.ass:fontsdir=" in fg
    ass = _ass(tmp_path)
    assert "Style: Pill,Inter," in ass and "\\k" not in ass
    lines = [re.sub(r"\{[^}]*\}", "", ln.split(",,0,0,0,,", 1)[1]) for ln in _dialogues(ass, 1)]
    # gamma is cut; the scene change at delta (word 3) and the 1.3 s pause before zeta break the lines.
    assert sorted(set(lines)) == ["alpha beta", "delta epsilon", "zeta"]
    assert len(_dialogues(ass, 0)) == 3
    # delta starts its clip at tl 0.97 s: its source 11.4 s rebased by the clip's own tlStart.
    assert any(ln.startswith("Dialogue: 1,0:00:00.97,") for ln in _dialogues(ass, 1))


def test_configured_channel_gets_the_pill_not_its_karaoke_colours(monkeypatch, tmp_path) -> None:
    _redirect(monkeypatch, tmp_path)
    profile = {"channels": {"sample": {"captionStyle": {
        "baseColor": "&H00332211", "activeColor": "&H00665544", "emphasisColor": "&H00998877",
    }}}}
    channel_path = tmp_path / "channels.json"
    channel_path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(config, "CHANNELS_JSON", channel_path)
    style = channels.resolve_caption_style("sample")
    assert style["look"] == "pill"
    for key, value in profile["channels"]["sample"]["captionStyle"].items():
        assert style[key] == value
        assert value != channels.resolve_caption_style(None)[key]
    compile_mod.build_plan(_doc(captions={"enabled": True}, channel="sample"))
    ass = _ass(tmp_path)
    assert "Style: Pill,Inter," in ass and "&H18C5F5&" in ass
    assert all(colour not in ass for colour in ("332211", "665544", "998877"))


def test_a_channel_can_keep_the_outline_look(monkeypatch, tmp_path) -> None:
    _redirect(monkeypatch, tmp_path)
    profile = {"channels": {"outline": {"captionStyle": {"look": "outline", "activeColor": "&H0000FFFF"}}}}
    (tmp_path / "channels.json").write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(config, "CHANNELS_JSON", tmp_path / "channels.json")
    compile_mod.build_plan(_doc(captions={"enabled": True}, channel="outline"))
    ass = _ass(tmp_path)
    style = channels.resolve_caption_style("outline")
    assert ass.startswith("\n".join(cb._ass_header(1920, 1080, style)))  # the old karaoke header, unchanged
    assert "Style: Pill" not in ass and ass.count("\\k") == 5


def test_a_windowed_pill_build_rebases_to_the_window(monkeypatch, tmp_path) -> None:
    _redirect(monkeypatch, tmp_path)
    compile_mod.build_plan(_doc(captions={"enabled": True}), window=(29, 120))
    lines = _dialogues(_ass(tmp_path), 1)
    assert lines[0].startswith("Dialogue: 1,0:00:00.00,")                # delta rebased to the origin
    assert all("alpha" not in ln for ln in lines)
