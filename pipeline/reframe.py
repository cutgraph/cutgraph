"""9:16 reframing with a pure median-rect reducer and an optional face tracker.

`span_crop_rect` and `resolve_reframe` use only the standard library to reduce an existing
`<stem>.reframe.json` into one static crop rectangle for a render span. The compiler consumes
that rectangle when the document's `crop.mode` is "reframe".

`analyze` lazily imports MediaPipe and OpenCV, tracks faces in the source, and writes the
metadata sidecar. The module can be imported without those optional dependencies. If metadata
is absent, `resolve_reframe` returns None and the compiler uses a static center crop.
Reframing uses the median of the in-span crops rather than per-frame pan smoothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import config
from pipeline import contracts
from pipeline.face_detection import FaceDetector, create_face_detector
from pipeline.errors import ValidationError

STAGE = "reframe"


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def span_crop_rect(
    frame_crops: list[dict], src_in_f: int, src_out_f: int, target_w: int, target_h: int,
) -> "dict | None":
    """PURE. Collapse the per-frame crop rectangles whose `frame_number` falls in [src_in_f, src_out_f)
    into ONE static crop rect (the per-key median), then adjust it to the exact target aspect so the
    downstream `scale=target_w:target_h` never distorts. Returns
    `{crop_x, crop_y, crop_w, crop_h}` (integer source pixels) or None when no frame is in range (⇒
    compile uses the static center crop). `frame_crops` are dicts with the contracts.FrameCrop keys."""
    in_range = [fc for fc in frame_crops
                if src_in_f <= int(fc["frame_number"]) < src_out_f]
    if not in_range:
        return None
    cx = _median([float(fc["crop_x"]) for fc in in_range])
    cy = _median([float(fc["crop_y"]) for fc in in_range])
    cw = _median([float(fc["crop_w"]) for fc in in_range])
    ch = _median([float(fc["crop_h"]) for fc in in_range])

    # Enforce the target aspect: shrink the over-long axis and re-center so cw:ch == target_w:target_h.
    target_ar = target_w / target_h
    if ch > 0 and (cw / ch) > target_ar:
        new_cw = ch * target_ar
        cx += (cw - new_cw) / 2.0
        cw = new_cw
    elif ch > 0:
        new_ch = cw / target_ar
        cy += (ch - new_ch) / 2.0
        ch = new_ch

    return {
        "crop_x": max(0, round(cx)),
        "crop_y": max(0, round(cy)),
        "crop_w": max(1, round(cw)),
        "crop_h": max(1, round(ch)),
    }


def _metadata_from_dict(d: dict) -> contracts.ReframeMetadata:
    return contracts.ReframeMetadata(
        clip_id=d["clip_id"],
        aspect_ratio=d["aspect_ratio"],
        face_coverage_pct=float(d["face_coverage_pct"]),
        frame_crops=[
            contracts.FrameCrop(
                frame_number=int(fc["frame_number"]),
                timestamp=float(fc["timestamp"]),
                cx=float(fc["cx"]),
                cy=float(fc["cy"]),
                crop_x=int(fc["crop_x"]),
                crop_y=int(fc["crop_y"]),
                crop_w=int(fc["crop_w"]),
                crop_h=int(fc["crop_h"]),
            )
            for fc in d.get("frame_crops", [])
        ],
    )


def load_metadata(path: Path) -> "contracts.ReframeMetadata | None":
    """Parse + validate a reframe.json at `path`; None if it is missing, unreadable, or invalid."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        meta = _metadata_from_dict(json.loads(path.read_text(encoding="utf-8")))
        meta.validate()
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, ValidationError):
        return None
    return meta


def resolve_reframe(source_stem: str) -> "contracts.ReframeMetadata | None":
    """Load `config.REFRAME_DIR/<source_stem>.reframe.json` (validated), or None when absent/invalid —
    the signal for compile to use the static center-crop fallback."""
    return load_metadata(config.REFRAME_DIR / f"{source_stem}.reframe.json")


def write_sidecar(
    source_stem: str, meta: contracts.ReframeMetadata, *, output_dir: Path | None = None
) -> Path:
    """Atomically write a sidecar, optionally to a worker job-owned directory."""
    import dataclasses
    import os

    path = (output_dir or config.REFRAME_DIR) / f"{source_stem}.reframe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dataclasses.asdict(meta)), encoding="utf-8")
    os.replace(tmp, path)
    return path


def analyze(source: Path, *, fps: int, target_w: int = 1080, target_h: int = 1920,
            clip_id: str = "s0", output_dir: Path | None = None) -> contracts.ReframeMetadata:
    """Face-track the source with lazily imported MediaPipe and OpenCV. Samples the
    source and emits one FrameCrop per sampled frame — a `target_w:target_h` window centered on the
    detected face (falling back to the frame center when no face is found). Writes the sidecar and
    returns the metadata. The compiler reduces these samples to a static crop."""
    import cv2  # type: ignore[import-not-found]
    try:
        import mediapipe as mp  # type: ignore[import-not-found]
    except ImportError:
        mp = None

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError(f"reframe: cannot open {source}")
    detector: FaceDetector | None = None

    target_ar = target_w / target_h
    frame_crops: list[contracts.FrameCrop] = []
    detected = 0
    idx = 0
    try:
        detector = create_face_detector(
            cv2,
            mp,
            model_selection=1,
            min_detection_confidence=0.4,
        )
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            # A full-height 9:16 column is the crop; only its X center follows the face.
            crop_w = min(w, int(round(h * target_ar)))
            crop_h = min(h, int(round(crop_w / target_ar)))
            face_cx, face_cy = w / 2.0, h / 2.0
            detections = detector.detect(frame)
            if detections:
                detected += 1
                box = detections[0]
                face_cx = (box.xmin + box.width / 2.0) * w
                face_cy = (box.ymin + box.height / 2.0) * h
            crop_x = int(round(min(max(0.0, face_cx - crop_w / 2.0), w - crop_w)))
            crop_y = int(round(min(max(0.0, face_cy - crop_h / 2.0), h - crop_h)))
            frame_crops.append(contracts.FrameCrop(
                frame_number=idx, timestamp=idx / fps, cx=face_cx, cy=face_cy,
                crop_x=crop_x, crop_y=crop_y, crop_w=crop_w, crop_h=crop_h,
            ))
            idx += 1
    finally:
        cap.release()
        if detector is not None:
            detector.close()

    if not frame_crops:
        raise RuntimeError(f"reframe: no frames decoded from {source}")
    coverage = 100.0 * detected / len(frame_crops)
    meta = contracts.ReframeMetadata(
        clip_id=clip_id, aspect_ratio=f"{target_w}:{target_h}",
        face_coverage_pct=coverage, frame_crops=frame_crops,
    )
    meta.validate()
    write_sidecar(Path(source).stem, meta, output_dir=output_dir)
    return meta
