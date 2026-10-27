"""Dependency-free data contracts for media stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from pipeline.errors import ValidationError


# ── Transcription ─────────────────────────────────────────────────────────────

@dataclass
class WordToken:
    word: str
    start: float
    end: float
    score: float


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[WordToken]


@dataclass
class FillerToken:
    """A verbatim disfluency ("um", "uh", …) that fills a gap WhisperX deleted.
    Fillers live in a SEPARATE top-level array, NEVER in a segment's `words` — the flat segments→words
    enumeration (the cross-phase `wordIdx` contract read by anchors, corrections, and caption emphasis)
    must stay byte-identical whether or not a filler pass has run. `segIdx` names the containing segment
    so the panel can interleave fillers by time for DISPLAY without minting an index into the word space."""
    word: str
    start: float
    end: float
    score: float
    segIdx: int


@dataclass
class TranscriptResult:
    language: str
    duration: float
    segments: list[TranscriptSegment]
    fillers: list[FillerToken] = field(default_factory=list)

    def validate(self) -> None:
        if not self.language:
            raise ValidationError("language required")
        if not (self.duration > 0):
            raise ValidationError("duration must be positive")
        if not self.segments:
            raise ValidationError("segments must not be empty")
        for seg in self.segments:
            if not seg.words:
                raise ValidationError(f"segment at {seg.start} has no words")


# ── Reframe ───────────────────────────────────────────────────────────────────

@dataclass
class FrameCrop:
    frame_number: int
    timestamp: float
    cx: float
    cy: float
    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int


@dataclass
class ReframeMetadata:
    clip_id: str
    aspect_ratio: str
    face_coverage_pct: float
    frame_crops: list[FrameCrop]

    def validate(self) -> None:
        if not (self.face_coverage_pct >= 0.0):
            raise ValidationError()
        if not self.frame_crops:
            raise ValidationError("no frame crops")


# ── Stage Result envelope ─────────────────────────────────────────────────────

@dataclass
class StageResult:
    status: str          # "ok" | "error"
    stage: str
    clip_id: str
    output_path: Optional[str] = None
    reason: Optional[str] = None
    details: Optional[str] = None
    metrics: dict = field(default_factory=dict)
