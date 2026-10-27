"""_reshape_transcript filler passthrough (scripts/studio_app.py).

The single Python edit that makes merged fillers visible to BOTH the panel (GET /api/transcript) and the
agent (read_transcript, same reshape). Fillers ride a SEPARATE `fillers` array `[{w,start,end,segIdx}]`
served alongside `words`; the flat `words` list (the wordIdx contract) never carries a filler.
"""
from __future__ import annotations

from studio import helpers


def _doc() -> dict:
    return {
        "language": "en", "duration": 2.0,
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "Hello world", "words": [
                {"word": "Hello", "start": 0.1, "end": 0.4, "score": 0.9},
                {"word": "world", "start": 0.8, "end": 1.2, "score": 0.9},
            ]},
        ],
        "fillers": [
            {"word": "um", "start": 0.5, "end": 0.7, "score": 0.5, "segIdx": 0},
        ],
    }


def test_reshape_serves_fillers_alongside_words() -> None:
    out = helpers._reshape_transcript(_doc())
    # The flat word list carries ONLY WhisperX words — never a filler (wordIdx space untouched).
    assert [w["w"] for w in out["words"]] == ["Hello", "world"]
    # Fillers ride the separate array with display fields + a segIdx (no index into the word space).
    assert out["fillers"] == [{"w": "um", "start": 0.5, "end": 0.7, "segIdx": 0}]


def test_reshape_back_compat_transcript_without_fillers() -> None:
    # A legacy transcript with no `fillers` key reshapes cleanly to an empty fillers array.
    legacy = {
        "language": "en", "duration": 1.0,
        "segments": [{"start": 0.0, "end": 1.0, "text": "Hi", "words": [
            {"word": "Hi", "start": 0.1, "end": 0.3, "score": 0.9},
        ]}],
    }
    out = helpers._reshape_transcript(legacy)
    assert out["words"][0]["w"] == "Hi"
    assert out["fillers"] == []
