"""Pure tool builders. No SDK, no fs — just payload shape + the compact-JSON
contract (every builder < 2 KB; words never dumped without a bounded range)."""
from __future__ import annotations

import json

from pipeline import agent_tools

_DOC = {
    "name": "p", "fps": 30, "width": 1920, "height": 1080,
    "tracks": [
        {"id": "t1", "kind": "speech", "clips": [
            {"id": "c1", "media": {"source": "a.mp4"}, "srcInF": 0, "srcOutF": 30, "tlStartF": 0},
            {"id": "c2", "media": {"source": "a.mp4"}, "srcInF": 40, "srcOutF": 90, "tlStartF": 30},
        ]},
        {"id": "t2", "kind": "text", "items": [
            {"id": "x1", "text": "A very long title that should be truncated well past forty characters",
             "pos": {"x": 0.5, "y": 0.5}, "preset": "lower_third", "tlStartF": 10, "durF": 60,
             "anchor": {"mode": "word", "ref": {"media": "a.mp4", "wordIdx": 3}}},
        ]},
    ],
}

_TDOC = {
    "language": "en", "duration": 3.0,
    "words": [
        {"w": "hello", "start": 0.0, "end": 0.4, "segIdx": 0},
        {"w": "there", "start": 0.4, "end": 0.8, "segIdx": 0},
        {"w": "world", "start": 0.9, "end": 1.3, "segIdx": 0},
        {"w": "again", "start": 1.4, "end": 1.9, "segIdx": 1},
    ],
    "segments": [
        {"segIdx": 0, "start": 0.0, "end": 1.3, "text": "hello there world"},
        {"segIdx": 1, "start": 1.4, "end": 1.9, "text": "again"},
    ],
}


def _load(s: str) -> dict:
    assert len(s) < 2048
    return json.loads(s)


def test_project_skeleton_overview_is_summarized_and_compact() -> None:
    out = _load(agent_tools.project_skeleton(_DOC, 7))
    assert out["revision"] == 7
    assert out["fps"] == 30
    assert "durationF" in out
    speech = out["tracks"][0]
    assert speech["id"] == "t1" and speech["kind"] == "speech" and speech["count"] == 2
    # per-track first/last only: first=c1, last=c2, no full clip list leaked
    assert speech["first"] == {"id": "c1", "srcInF": 0, "srcOutF": 30, "tlStartF": 0}
    assert speech["last"]["id"] == "c2"
    assert "clips" not in speech
    assert "media" not in speech["first"]          # media refs / gains never leaked
    text_track = out["tracks"][1]
    assert text_track["count"] == 1
    assert text_track["first"]["anchor"] == "word"  # anchor mode surfaced
    assert len(text_track["first"]["text"]) <= 40   # text truncated
    assert "last" not in text_track                 # single element ⇒ no separate last


def test_project_skeleton_track_page_is_addressable() -> None:
    out = _load(agent_tools.project_skeleton(_DOC, 7, track="t1"))
    assert out["track"] == "t1" and out["cursor"] == 0 and out["count"] == 2
    assert [c["id"] for c in out["clips"]] == ["c1", "c2"]
    assert "nextCursor" not in out                   # both fit in one page


def test_project_skeleton_100_clip_project_paginates_under_2kb() -> None:
    """Real-sized fixture: a 100-clip track never crashes on size — the overview fits and every
    clip is reachable by paging with nextCursor."""
    clips = [{"id": f"c{i}", "media": {"source": "a.mp4"}, "srcInF": i * 30,
              "srcOutF": i * 30 + 30, "tlStartF": i * 30} for i in range(100)]
    doc = {"name": "big", "fps": 30, "width": 1920, "height": 1080,
           "tracks": [{"id": "sp", "kind": "speech", "clips": clips}]}

    overview = _load(agent_tools.project_skeleton(doc, 1))   # _load asserts < 2048
    assert overview["tracks"][0]["count"] == 100
    assert overview["tracks"][0]["first"]["id"] == "c0"
    assert overview["tracks"][0]["last"]["id"] == "c99"

    seen: list[str] = []
    cursor = 0
    for _ in range(200):  # guard against an infinite loop
        page = _load(agent_tools.project_skeleton(doc, 1, track="sp", cursor=cursor))
        seen += [c["id"] for c in page["clips"]]
        if "nextCursor" not in page:
            break
        assert page["nextCursor"] > cursor          # cursor strictly advances
        cursor = page["nextCursor"]
    assert seen == [f"c{i}" for i in range(100)]     # every clip addressable, in order


def test_read_transcript_defaults_to_segments() -> None:
    out = _load(agent_tools.read_transcript(_TDOC))
    assert out["granularity"] == "segments"
    assert [s["i"] for s in out["segments"]] == [0, 1]
    # the flat word list must not appear anywhere in a segments call
    assert "words" not in out


def test_read_transcript_words_requires_a_range() -> None:
    out = _load(agent_tools.read_transcript(_TDOC, "words"))
    assert "error" in out
    assert "words" not in out                      # refused — no word list dumped


def test_read_transcript_words_within_bounded_range() -> None:
    out = _load(agent_tools.read_transcript(_TDOC, "words", (1, 3)))
    assert out["granularity"] == "words"
    assert out["range"] == [1, 3]
    assert [w["i"] for w in out["words"]] == [1, 2]
    assert [w["w"] for w in out["words"]] == ["there", "world"]


def test_read_transcript_1hour_paginates_under_2kb() -> None:
    """Real-sized fixture: a 1-hour transcript (~1200 segments) never crashes the default view;
    it paginates via nextCursor and every segment is reachable."""
    segs = [{"segIdx": i, "start": i * 3.0, "end": i * 3.0 + 3.0, "text": f"segment number {i} spoken"}
            for i in range(1200)]
    tdoc = {"language": "en", "duration": 3600.0, "words": [], "segments": segs}

    seen: list[int] = []
    cursor = 0
    for _ in range(2000):
        page = _load(agent_tools.read_transcript(tdoc, "segments", None, cursor))  # _load asserts < 2048
        assert page["granularity"] == "segments"
        seen += [s["i"] for s in page["segments"]]
        if "nextCursor" not in page:
            break
        assert page["nextCursor"] > cursor
        cursor = page["nextCursor"]
    assert seen == list(range(1200))               # every segment addressable, in order


def test_read_transcript_words_over_wide_range_is_bounded() -> None:
    words = [{"w": f"w{i}", "start": i * 0.3, "end": i * 0.3 + 0.3, "segIdx": 0} for i in range(1000)]
    tdoc = {"language": "en", "duration": 300.0, "words": words, "segments": []}
    out = _load(agent_tools.read_transcript(tdoc, "words", (0, 1000)))  # _load asserts < 2048
    assert out["granularity"] == "words"
    assert out["words"][0]["i"] == 0
    assert "nextCursor" in out                     # an over-wide range is clamped/paginated, not dumped


def test_notes_tail_returns_newest_and_is_valid_json() -> None:
    """read_notes tail: valid JSON (never a sliced string), NEWEST entries kept, {truncated,
    totalLines} present — the opposite of the old `dumps(...)[:2000]` that kept the OLDEST + broke JSON."""
    lines = [f"## line {i} — some decision text padded out to take space" for i in range(200)]
    text = "\n".join(lines)
    out = _load(agent_tools.notes_tail(text))       # _load asserts < 2048 AND json.loads succeeds
    assert out["truncated"] is True
    assert out["totalLines"] == 200
    assert "line 199" in out["notes"]               # newest kept
    assert "line 0 —" not in out["notes"]           # oldest dropped


def test_notes_tail_small_is_whole_and_untruncated() -> None:
    out = _load(agent_tools.notes_tail("## only\n\nbody\n"))
    assert out["truncated"] is False
    assert "only" in out["notes"]


def test_find_word_returns_capped_hits_with_frames() -> None:
    out = _load(agent_tools.find_word(_TDOC, "world", 30))
    assert out["hits"][0]["wordIdx"] == 2
    assert out["hits"][0]["frame"] == 27           # round(0.9 * 30)
    assert "world" in out["hits"][0]["context"]


def test_find_word_multiword_sequence() -> None:
    out = _load(agent_tools.find_word(_TDOC, "there world", 30))
    assert [h["wordIdx"] for h in out["hits"]] == [1]
    assert out["hits"][0]["endWordIdx"] == 2       # run end reported


def test_find_word_is_whole_word_not_substring() -> None:
    """'cut' must NOT hit 'executed' or 'haircut' (the old `token in window` substring bug)."""
    tdoc = {"words": [
        {"w": "he", "start": 0.0}, {"w": "executed", "start": 0.2},
        {"w": "the", "start": 0.6}, {"w": "haircut", "start": 0.9}, {"w": "cut", "start": 1.4},
    ]}
    out = _load(agent_tools.find_word(tdoc, "cut", 30))
    assert [h["wordIdx"] for h in out["hits"]] == [4]   # only the standalone "cut"


def test_find_word_matches_across_whisperx_punctuation() -> None:
    """a multi-word phrase matches across tokens that carry trailing punctuation ('you,')."""
    tdoc = {"words": [
        {"w": "well", "start": 0.0}, {"w": "thank", "start": 0.4}, {"w": "you,", "start": 0.8},
        {"w": "everyone", "start": 1.2},
    ]}
    out = _load(agent_tools.find_word(tdoc, "Thank you", 30))
    assert [h["wordIdx"] for h in out["hits"]] == [1]   # case- and punctuation-insensitive


_CHANNELS = {
    "channels": {
        "studio": {
            "name": "Paper folding", "voice": "friendly, clear, step-by-step",
            "audience": "beginners learning paper folding", "accentColor": "&H00CCFF66",
            "captionStyle": {"activeColor": "&H00CCFF66", "wordsPerPage": 3},
        }
    }
}


def test_channel_profile_is_compact_and_omits_caption_style() -> None:
    out = _load(agent_tools.channel_profile(_CHANNELS, "studio"))
    assert out["available"] is True
    assert out["channel"] == "studio"
    assert out["name"] == "Paper folding"
    assert out["accentColor"] == "&H00CCFF66"
    # the heavy captionStyle is resolved by the caption engine, never leaked to the planner
    assert "captionStyle" not in json.dumps(out)


def test_channel_profile_unknown_or_none_is_unavailable() -> None:
    for ch in (None, "no-such-channel"):
        out = _load(agent_tools.channel_profile(_CHANNELS, ch))
        assert out["available"] is False
        assert "reason" in out


def test_channel_profile_empty_doc_is_unavailable() -> None:
    out = _load(agent_tools.channel_profile({}, "studio"))
    assert out["available"] is False


def test_options_labels_carries_labels_only() -> None:
    """propose_options echoes only the LABELS back to the agent (< 2 KB) — descriptions and previewRef
    video links ride the SSE event to the UI, never back into the agent's context."""
    opts = [
        {"label": "Ask a question", "description": "open with the objection", "previewRef": "/media/preview/a.mp4"},
        {"label": "Cold cut to the payoff", "description": "no preamble at all"},
        {"label": "Stat first"},
    ]
    out = _load(agent_tools.options_labels("hook", opts))
    assert out["kind"] == "hook"
    assert out["labels"] == ["Ask a question", "Cold cut to the payoff", "Stat first"]
    # the rich payload is NOT leaked into the compact tool return
    assert "description" not in json.dumps(out)
    assert "previewRef" not in json.dumps(out)


def test_options_labels_ignores_malformed_options() -> None:
    out = _load(agent_tools.options_labels("caption", [{"label": "A"}, "nope", None, {"nolabel": 1}]))
    assert out["labels"] == ["A", ""]
