"""pipeline/reframe.py — the pure 9:16 rect reducer + metadata loader (isolated, no MediaPipe).

`span_crop_rect` medians the in-span per-frame crop rects into one aspect-correct rect; `load_metadata`
/ `resolve_reframe` parse+validate a reframe.json (None when absent/invalid — the center-crop fallback
signal). The MediaPipe producer `analyze` is not exercised here.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from pipeline import contracts
from pipeline import reframe
from pipeline.face_detection import FaceBox


def _frame(n: int, *, crop_x: int, crop_y: int = 60, crop_w: int = 540, crop_h: int = 960) -> dict:
    return {"frame_number": n, "timestamp": n / 30.0, "cx": crop_x + crop_w / 2,
            "cy": crop_y + crop_h / 2, "crop_x": crop_x, "crop_y": crop_y,
            "crop_w": crop_w, "crop_h": crop_h}


def test_span_crop_rect_medians_in_range_frames() -> None:
    # 5 in-range frames whose crop_x medians to 690; crop 540x960 is already exactly 9:16 so the aspect
    # adjustment is a no-op — the returned rect is the per-key median.
    frames = [_frame(n, crop_x=x) for n, x in zip(range(5), [680, 685, 690, 695, 700])]
    rect = reframe.span_crop_rect(frames, 0, 90, 1080, 1920)
    assert rect == {"crop_x": 690, "crop_y": 60, "crop_w": 540, "crop_h": 960}


def test_span_crop_rect_none_when_no_frame_in_range() -> None:
    frames = [_frame(n, crop_x=690) for n in range(5)]
    assert reframe.span_crop_rect(frames, 100, 110, 1080, 1920) is None


def test_span_crop_rect_enforces_target_aspect() -> None:
    # A too-wide source rect (800x1000, ar 0.80) is shrunk on the wide axis to the 9:16 target and
    # re-centered, never distorted by the downstream scale.
    frames = [_frame(n, crop_x=100, crop_y=0, crop_w=800, crop_h=1000) for n in range(3)]
    rect = reframe.span_crop_rect(frames, 0, 90, 1080, 1920)
    assert rect is not None
    assert abs(rect["crop_w"] / rect["crop_h"] - 1080 / 1920) < 0.01
    assert rect["crop_w"] < 800 and rect["crop_x"] > 100  # narrowed + recentered inward


def test_span_crop_rect_tall_source_shrinks_height() -> None:
    # a too-TALL source rect (300x1000, ar 0.30 < 9:16) hits the `elif ch > 0` branch — HEIGHT is
    # shrunk to the 9:16 target and re-centered vertically while WIDTH is preserved (the wide-branch test
    # above only exercises the `if` path).
    frames = [_frame(n, crop_x=200, crop_y=0, crop_w=300, crop_h=1000) for n in range(3)]
    rect = reframe.span_crop_rect(frames, 0, 90, 1080, 1920)
    assert rect is not None
    assert rect["crop_w"] == 300                                  # width preserved (the short axis)
    assert abs(rect["crop_w"] / rect["crop_h"] - 1080 / 1920) < 0.01
    assert rect["crop_h"] < 1000 and rect["crop_y"] > 0           # height narrowed + recentered downward


def test_resolve_reframe_missing_file_is_none() -> None:
    assert reframe.resolve_reframe("no-such-source-stem-zzz") is None


def test_load_metadata_valid_fixture(tmp_path) -> None:
    doc = {
        "clip_id": "s0", "aspect_ratio": "1080:1920", "face_coverage_pct": 92.0,
        "frame_crops": [_frame(n, crop_x=690) for n in range(3)],
    }
    path = tmp_path / "clip.reframe.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    meta = reframe.load_metadata(path)
    assert meta is not None
    assert meta.clip_id == "s0" and len(meta.frame_crops) == 3
    assert meta.frame_crops[0].crop_w == 540


def test_load_metadata_invalid_is_none(tmp_path) -> None:
    bad = tmp_path / "bad.reframe.json"
    bad.write_text('{"clip_id": "s0", "aspect_ratio": "9:16", "face_coverage_pct": 1.0, '
                   '"frame_crops": []}', encoding="utf-8")  # empty frame_crops ⇒ validate() fails
    assert reframe.load_metadata(bad) is None
    assert reframe.load_metadata(tmp_path / "missing.json") is None


def test_worker_reframe_sidecar_can_use_a_job_owned_output_directory(tmp_path: Path) -> None:
    meta = contracts.ReframeMetadata(
        "source01", "9:16", 0.0,
        [contracts.FrameCrop(0, 0.0, 1.0, 1.0, 0, 0, 1, 1)],
    )
    job_dir = tmp_path / "worker-runs" / "job01" / "artifacts"
    written = reframe.write_sidecar("input-abc", meta, output_dir=job_dir)
    assert written == job_dir / "input-abc.reframe.json"
    assert reframe.load_metadata(written) == meta


def test_analyze_closes_detector_and_writes_schema_valid_fallback_output(
    tmp_path: Path, monkeypatch
) -> None:
    class Frame:
        shape = (100, 200, 3)

    class Capture:
        def __init__(self) -> None:
            self.released = False
            self.frames = iter([Frame()])

        def isOpened(self) -> bool:
            return True

        def read(self):
            try:
                return True, next(self.frames)
            except StopIteration:
                return False, None

        def release(self) -> None:
            self.released = True

    capture = Capture()

    class Cv2:
        def VideoCapture(self, source: str) -> Capture:
            assert source.endswith("clip.mp4")
            return capture

    class Detector:
        def __init__(self) -> None:
            self.closed = False

        def detect(self, frame: Frame) -> list[FaceBox]:
            return [FaceBox(0.25, 0.25, 0.2, 0.2)]

        def close(self) -> None:
            self.closed = True

    detector = Detector()
    monkeypatch.setitem(sys.modules, "cv2", Cv2())
    monkeypatch.setitem(sys.modules, "mediapipe", SimpleNamespace(tasks=object()))
    monkeypatch.setattr(reframe, "create_face_detector", lambda *args, **kwargs: detector)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fixture")
    meta = reframe.analyze(source, fps=30, output_dir=tmp_path)

    assert meta.face_coverage_pct == 100.0
    assert len(meta.frame_crops) == 1
    assert meta.frame_crops[0].crop_w == 56
    assert meta.frame_crops[0].crop_h == 100
    assert capture.released
    assert detector.closed
    assert reframe.load_metadata(tmp_path / "clip.reframe.json") == meta


def test_analyze_releases_capture_when_detector_construction_fails(
    tmp_path: Path, monkeypatch
) -> None:
    class Capture:
        def __init__(self) -> None:
            self.released = False

        def isOpened(self) -> bool:
            return True

        def release(self) -> None:
            self.released = True

    capture = Capture()

    class Cv2:
        def VideoCapture(self, source: str) -> Capture:
            return capture

    monkeypatch.setitem(sys.modules, "cv2", Cv2())
    monkeypatch.setitem(sys.modules, "mediapipe", SimpleNamespace(tasks=object()))

    def fail_detector(*args, **kwargs):
        raise RuntimeError("detector setup failed")

    monkeypatch.setattr(reframe, "create_face_detector", fail_detector)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fixture")

    with pytest.raises(RuntimeError, match="detector setup failed"):
        reframe.analyze(source, fps=30, output_dir=tmp_path)
    assert capture.released
