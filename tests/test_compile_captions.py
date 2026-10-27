"""compile.build_plan — the caption burn path.

Isolated: _probe_transfer is stubbed (no ffprobe) and every dir is redirected under tmp_path so the
ASS artifact never lands in the git-tracked outputs/captions. captions absent ⇒ NO ass= filter (legacy
long-form byte-identical); captions.enabled ⇒ a ROOT-relative POSIX ass= appended to the
[vout] chain; a windowed build shifts the caption word times to the window origin. These pin the
karaoke look (`look: "outline"`); the default pill look is covered by test_caption_pill.py.
"""
from __future__ import annotations

import json

import config
from pipeline import compile as compile_mod

# rec.words.json (source-time seconds): the parent word clock the short/long-form windows into.
_WORDS_DOC = {
    "language": "en", "duration": 20.0,
    "segments": [
        {"start": 10.0, "end": 14.0, "text": "alpha beta gamma delta epsilon zeta", "words": [
            {"word": "alpha", "start": 10.0, "end": 10.4},
            {"word": "beta", "start": 10.4, "end": 10.8},
            {"word": "gamma", "start": 11.0, "end": 11.4},
            {"word": "delta", "start": 11.4, "end": 11.8},
            {"word": "epsilon", "start": 12.0, "end": 12.4},
            {"word": "zeta", "start": 13.5, "end": 13.9},
        ]},
    ],
}

_OUTLINE = {"enabled": True, "style": {"look": "outline"}}


def _doc(*, captions=None, channel=None, with_text=False) -> dict:
    d: dict = {
        "schemaVersion": 1, "name": "zzcap", "fps": 30, "width": 1080, "height": 1920,
        "revision": 0, "transcript": None,
        "tracks": [
            {"id": "sp", "kind": "speech", "clips": [
                # source frames 300..600 = seconds 10..20 (the whole word clock), tlStartF=0.
                {"id": "s0", "media": {"source": "media/rec.mp4", "proxy": None},
                 "srcInF": 300, "srcOutF": 600, "tlStartF": 0, "gainDb": 0.0},
            ]},
        ],
    }
    if with_text:
        d["tracks"].append({"id": "tx", "kind": "text", "items": [
            {"id": "hook", "text": "Hook!", "preset": "hook",
             "pos": {"x": 0.5, "y": 0.12}, "tlStartF": 0, "durF": 60},
        ]})
    if captions is not None:
        d["captions"] = captions
    if channel is not None:
        d["channel"] = channel
    return d


def _redirect(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "CAPTIONS_DIR", tmp_path / "captions")
    monkeypatch.setattr(config, "OVERLAYS_DIR", tmp_path / "overlays")
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    (tmp_path / "transcripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "transcripts" / "rec.words.json").write_text(json.dumps(_WORDS_DOC), encoding="utf-8")


def _ass_text(tmp_path) -> str:
    return (tmp_path / "captions" / "zzcap.ass").read_text(encoding="utf-8")


def test_captions_none_emits_no_ass(monkeypatch, tmp_path) -> None:
    _redirect(monkeypatch, tmp_path)
    fg = compile_mod.build_plan(_doc()).filter_complex
    assert "ass=" not in fg
    assert not (tmp_path / "captions").exists()


def test_captions_disabled_emits_no_ass(monkeypatch, tmp_path) -> None:
    _redirect(monkeypatch, tmp_path)
    fg = compile_mod.build_plan(_doc(captions={"enabled": False})).filter_complex
    assert "ass=" not in fg


def test_captions_enabled_appends_root_relative_ass(monkeypatch, tmp_path) -> None:
    _redirect(monkeypatch, tmp_path)
    fg = compile_mod.build_plan(_doc(captions=_OUTLINE)).filter_complex
    # The ASS file stays state-relative; fontsdir selects the bundled faces.
    assert "ass=captions/zzcap.ass" in fg
    from pipeline.runtime_tools import font_filter_path
    assert f":fontsdir={font_filter_path(role='sans', directory=True)}[vout]" in fg
    assert "]ass=captions/zzcap.ass:fontsdir=" in fg
    assert "\\" not in fg.split("ass=", 1)[1].split(":fontsdir=", 1)[0]
    # 6 words → 2 pages (default wordsPerPage=3); every word emits a \k fill.
    ass = _ass_text(tmp_path)
    assert ass.count("\\k") == 6


def test_captions_after_drawtext(monkeypatch, tmp_path) -> None:
    """Captions burn AFTER drawtext so the hook overlay sits on top."""
    _redirect(monkeypatch, tmp_path)
    fg = compile_mod.build_plan(_doc(captions={"enabled": True}, with_text=True)).filter_complex
    assert "drawtext=" in fg
    dt = fg.index("drawtext=")
    ass = fg.index("ass=captions/zzcap.ass")
    assert dt < ass  # drawtext precedes the caption filter in the chain
    assert "]ass=captions/zzcap.ass:fontsdir=" in fg


def test_windowed_build_shifts_word_times_to_origin(monkeypatch, tmp_path) -> None:
    """A windowed compile rebases the timeline to t=0; the caption word times shift by the SAME window
    origin (the shared clock stays aligned). Window (60,120) = source seconds 12..14 ⇒ only epsilon+zeta
    survive, and zeta's end shifts from 3.90 (full) to 1.90 (windowed = 3.90 − 2.0s origin)."""
    _redirect(monkeypatch, tmp_path)
    full = compile_mod.build_plan(_doc(captions=_OUTLINE)).filter_complex
    assert "ass=captions/zzcap.ass" in full
    full_ass = _ass_text(tmp_path)
    assert full_ass.count("\\k") == 6
    assert "0:00:03.90" in full_ass  # zeta at source 13.5..13.9 → 3.5..3.9 (shifted by src_in 10s)

    compile_mod.build_plan(_doc(captions=_OUTLINE), window=(60, 120))
    win_ass = _ass_text(tmp_path)
    assert win_ass.count("\\k") == 2                 # only epsilon + zeta fall in [12,14]
    assert win_ass.splitlines()[-1].startswith("Dialogue: 0,0:00:00.00")  # epsilon rebased to origin
    assert "0:00:01.90" in win_ass                   # zeta shifted to 1.90 = 3.90 − 2.0s window origin
    assert "0:00:03.90" not in win_ass


def test_emphasis_flat_wordidx_maps_to_clip_relative_override(monkeypatch, tmp_path) -> None:
    """captions.emphasis is a flat wordIdx into the source word list; the burn maps it to the
    clip-relative position and wraps that word in a colour override whose reset restores the page fill
    (activeColor), never baseColor."""
    _redirect(monkeypatch, tmp_path)
    # flat idx 2 = "gamma" (alpha=0, beta=1, gamma=2).
    compile_mod.build_plan(_doc(captions={**_OUTLINE, "emphasis": [2]})).filter_complex
    ass = _ass_text(tmp_path)
    # default channel active == emphasis == yellow; the trailing reset is the active fill, not white.
    assert "{\\c&H0000FFFF&}gamma{\\c&H0000FFFF&}" in ass
    assert "{\\c&H00FFFFFF&}" not in ass  # no baseColor (white) reset that would white-out later words


def test_caption_words_rebase_across_ripple_cut_seam(monkeypatch, tmp_path) -> None:
    """a mid-clip ripple cut splits the speech spine into TWO clips of the SAME source. Each clip's
    words rebase by its own `ws - srcIn + tlStart`, so a caption page straddling the seam stays on
    contiguous TIMELINE time — not source time. Here clip1 keeps src 10-11s (tl 0-1) and clip2 keeps src
    13-14s (tl 1-2), the gap 11-13s removed; zeta (src 13.5s) must land at tl 1.5s, never 3.5s."""
    from pipeline import project as project_mod  # noqa: PLC0415 — isolated local import
    _redirect(monkeypatch, tmp_path)
    media = project_mod.Media(source="media/rec.mp4")
    clips = [
        project_mod.Clip(id="s0", media=media, srcInF=300, srcOutF=330, tlStartF=0),   # src 10-11 → tl 0-1
        project_mod.Clip(id="s1", media=media, srcInF=390, srcOutF=420, tlStartF=30),  # src 13-14 → tl 1-2
    ]
    words, emph = compile_mod._caption_words(clips, 30, [])
    assert [w["word"] for w in words] == ["alpha", "beta", "zeta"]   # gap words (gamma..epsilon) dropped
    times = {w["word"]: round(w["start"], 3) for w in words}
    assert times["alpha"] == 0.0 and times["beta"] == 0.4           # clip1 at tl origin
    assert times["zeta"] == 1.5                                     # clip2 rebased by its own tlStart (+1s)
