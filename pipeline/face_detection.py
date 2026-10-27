"""Small compatibility seam for the optional face detectors used by media analysis and reframing.

MediaPipe releases before the Tasks-only package exposed ``mp.solutions.face_detection``.
Tasks-only installations need the same deterministic
OpenCV fallback rather than each implementing a subtly different missing-face path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class FaceBox:
    """A face rectangle normalized to the source frame's width and height."""

    xmin: float
    ymin: float
    width: float
    height: float


class FaceDetector(Protocol):
    def detect(self, frame: Any) -> list[FaceBox]:
        """Return detected faces in normalized frame coordinates."""
        ...

    def close(self) -> None:
        """Release detector resources."""
        ...


class _MediaPipeDetector:
    def __init__(self, cv2: Any, face_detection: Any, model_selection: int, confidence: float) -> None:
        self._cv2 = cv2
        self._detector = face_detection.FaceDetection(
            model_selection=model_selection,
            min_detection_confidence=confidence,
        )

    def detect(self, frame: Any) -> list[FaceBox]:
        result = self._detector.process(self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB))
        detections = getattr(result, "detections", None) or []
        boxes: list[FaceBox] = []
        for detection in detections:
            box = detection.location_data.relative_bounding_box
            boxes.append(FaceBox(float(box.xmin), float(box.ymin), float(box.width), float(box.height)))
        return boxes

    def close(self) -> None:
        self._detector.close()


class _OpenCvHaarDetector:
    def __init__(self, cv2: Any) -> None:
        self._cv2 = cv2
        cascade_path = self._cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = self._cv2.CascadeClassifier(cascade_path)

    def detect(self, frame: Any) -> list[FaceBox]:
        if self._cascade.empty():
            return []
        gray = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)
        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            return []
        boxes = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        return [
            FaceBox(float(x) / width, float(y) / height, float(w) / width, float(h) / height)
            for x, y, w, h in boxes
        ]

    def close(self) -> None:
        """OpenCV's cascade has no external resource to release."""


def create_face_detector(
    cv2: Any,
    mediapipe: Any,
    *,
    model_selection: int,
    min_detection_confidence: float,
) -> FaceDetector:
    """Prefer legacy MediaPipe solutions, falling back to a local OpenCV Haar detector.

    ``mediapipe`` is intentionally ``Any`` because it is a lazy, optional dependency and
    the installed Tasks-only release has no usable static surface for the legacy API.
    """
    solutions = getattr(mediapipe, "solutions", None)
    face_detection = getattr(solutions, "face_detection", None)
    if face_detection is not None and getattr(face_detection, "FaceDetection", None) is not None:
        try:
            return _MediaPipeDetector(cv2, face_detection, model_selection, min_detection_confidence)
        except (AttributeError, ImportError):
            # Legacy solutions can be present but unusable in a partial/API-incompatible install.
            # Do not catch processing errors: those should remain visible to the caller.
            pass
    return _OpenCvHaarDetector(cv2)
