"""build_karaoke_ass — the ONE karaoke `\\k` generator.

Pure + isolated (no ffmpeg): assert the generated ASS text directly. Covers the
"\\k timings sum to span length" golden, per-word emphasis colour overrides, brace/backslash escaping
(no doc corruption), and byte-stability of the legacy default `_ass_document` against a frozen golden.
"""
from __future__ import annotations

import re
from pathlib import Path

from pipeline import caption_burn as cb

_GOLDEN = Path(__file__).parent / "fixtures" / "karaoke_default_golden.ass"

# The exact word list used to freeze the golden (see fixtures/karaoke_default_golden.ass).
_WORDS = [
    {"word": "The", "start": 0.0, "end": 0.4},
    {"word": "quick", "start": 0.4, "end": 0.8},
    {"word": "brown", "start": 0.8, "end": 1.3},
    {"word": "fox", "start": 1.3, "end": 1.7},
    {"word": "jumps", "start": 1.7, "end": 2.2},
    {"word": "over", "start": 2.2, "end": 2.5},
    {"word": "lazy", "start": 2.5, "end": 3.0},
]

_STYLE = {
    "wordsPerPage": 3, "font": "Inter", "bold": True,
    "baseColor": "&HFFFFFF", "activeColor": "&H00CCFF66", "emphasisColor": "&H00CCFF66",
    "marginVPct": 0.15, "alignment": 2, "outlineRatio": 14,
}


def _dialogue_lines(doc: str) -> list[str]:
    return [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]


def test_k_centiseconds_sum_to_page_span() -> None:
    """Per page the `\\k` centiseconds sum to (page_end - page_start) — the karaoke fill covers exactly
    the page's on-screen span."""
    doc = cb.build_karaoke_ass(_WORDS, 1080, 1920, style=_STYLE)
    lines = _dialogue_lines(doc)
    n = _STYLE["wordsPerPage"]
    assert len(lines) == (len(_WORDS) + n - 1) // n
    for page, ln in enumerate(lines):
        group = _WORDS[page * n:page * n + n]
        span = group[-1]["end"] - group[0]["start"]
        ks = [int(x) for x in re.findall(r"\\k(\d+)", ln)]
        assert len(ks) == len(group)
        assert abs(sum(ks) / 100.0 - span) < 0.02


def test_emphasis_word_carries_colour_override() -> None:
    """A word whose clip-relative index is emphasised is wrapped in a `{\\c<emphasis>&}..{\\c<active>&}`
    override AFTER its `\\k` tag: the trailing reset restores the PAGE's karaoke fill (activeColor), NOT
    baseColor."""
    doc = cb.build_karaoke_ass(_WORDS, 1080, 1920, style=_STYLE, emphasis_idx=frozenset({1}))
    assert "{\\c&H00CCFF66&}quick{\\c&H00CCFF66&}" in doc
    # a non-emphasised word carries no colour override
    assert "{\\c&H00CCFF66&}The" not in doc


def test_emphasis_restores_page_fill_for_following_words() -> None:
    """the reset after an emphasised (non-terminal) word must restore the page's karaoke fill
    (PrimaryColour = activeColor), never baseColor — a baseColor reset persists to the end of the single
    Dialogue page line and whites-out every following word's `\\k` fill. 'quick' (idx 1) is emphasised;
    'brown' (idx 2, same page) must keep the accent fill."""
    doc = cb.build_karaoke_ass(_WORDS, 1080, 1920, style=_STYLE, emphasis_idx=frozenset({1}))
    # The emphasis colour-override reset is the active fill, not the white baseColor.
    assert "quick{\\c&H00CCFF66&}" in doc
    # No baseColor (white) override tag appears anywhere — that reset is exactly the following-word bug.
    assert "{\\c&H00FFFFFF&}" not in doc
    # 'brown' follows on the same page and carries no white-out override of its own.
    assert "brown" in doc


def test_emphasis_on_non_first_page_maps_by_absolute_index() -> None:
    """Emphasis is an ABSOLUTE clip-relative index, so it must colour the right word on a later page.
    idx 4 = 'jumps' on page 1 (wordsPerPage 3: page0 The/quick/brown, page1 fox/jumps/over)."""
    doc = cb.build_karaoke_ass(_WORDS, 1080, 1920, style=_STYLE, emphasis_idx=frozenset({4}))
    assert "{\\c&H00CCFF66&}jumps{\\c&H00CCFF66&}" in doc
    # page-0 words are untouched (the paging maps by i+j, not within-page position).
    assert "{\\c&H00CCFF66&}The" not in doc
    assert "{\\c&H00CCFF66&}brown" not in doc


def test_out_of_range_emphasis_index_is_ignored_not_crash() -> None:
    """An emphasis index beyond the word count is a safe no-op (never raises, colours nothing)."""
    doc = cb.build_karaoke_ass(_WORDS, 1080, 1920, style=_STYLE, emphasis_idx=frozenset({99}))
    assert "\\c&H00CCFF66&}" not in doc.split("[Events]", 1)[1]  # no override tag in any Dialogue line


def test_word_text_is_escaped() -> None:
    """Braces/backslashes riding in from a word are neutralized so they can't corrupt the ASS override
    syntax (the caption_burn:80 raw-insert gotcha)."""
    words = [{"word": "a{b}\\c", "start": 0.0, "end": 0.5}]
    doc = cb.build_karaoke_ass(words, 1080, 1920, style=_STYLE)
    assert "a(b)/c" in doc
    assert "a{b}" not in doc


def test_default_document_is_byte_stable() -> None:
    """The legacy long-form default (unspecified-channel) render is byte-identical to the frozen golden
    — caption extraction must not change existing caption output."""
    got = cb._ass_document(_WORDS, 1080, 1920)
    assert got == _GOLDEN.read_text(encoding="utf-8")


def test_ass_colour_idempotent_on_8hex() -> None:
    """`_ass_colour` pads a 6-hex config colour but passes an already-8-hex channel colour through
    unchanged (never doubles the alpha into a 10-hex token)."""
    assert cb._ass_colour("&H00FFFF") == "&H0000FFFF"      # 6-hex config → AA=00 prepended
    assert cb._ass_colour("&H00CCFF66") == "&H00CCFF66"    # 8-hex channel → unchanged
