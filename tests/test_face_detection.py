from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline.face_detection import FaceBox, create_face_detector


class _Frame:
    shape = (100, 200, 3)


class _ColorCv2:
    COLOR_BGR2RGB = 1
    COLOR_BGR2GRAY = 2

    def __init__(self) -> None:
        self.converted: list[int] = []
        self.data: object = SimpleNamespace(haarcascades="")
        self.CascadeClassifier: object = None

    def cvtColor(self, frame, code):
        self.converted.append(code)
        return frame


def test_legacy_mediapipe_detector_maps_boxes_and_closes() -> None:
    cv2 = _ColorCv2()
    closed: list[bool] = []

    class LegacyDetector:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"model_selection": 1, "min_detection_confidence": 0.4}

        def process(self, frame):
            return SimpleNamespace(
                detections=[
                    SimpleNamespace(
                        location_data=SimpleNamespace(
                            relative_bounding_box=SimpleNamespace(
                                xmin=0.1, ymin=0.2, width=0.3, height=0.4
                            )
                        )
                    )
                ]
            )

        def close(self) -> None:
            closed.append(True)

    mediapipe = SimpleNamespace(
        solutions=SimpleNamespace(face_detection=SimpleNamespace(FaceDetection=LegacyDetector))
    )
    detector = create_face_detector(
        cv2, mediapipe, model_selection=1, min_detection_confidence=0.4
    )

    assert detector.detect(_Frame()) == [FaceBox(0.1, 0.2, 0.3, 0.4)]
    detector.close()
    assert cv2.converted == [cv2.COLOR_BGR2RGB]
    assert closed == [True]


def test_tasks_only_mediapipe_uses_opencv_haar_fallback() -> None:
    cv2 = _ColorCv2()
    cascade_calls: list[str] = []

    class Cascade:
        def empty(self) -> bool:
            return False

        def detectMultiScale(self, gray, **kwargs):
            assert kwargs == {"scaleFactor": 1.1, "minNeighbors": 5}
            return [(10, 20, 40, 50)]

    cv2.data = SimpleNamespace(haarcascades="haar/")

    def make_cascade(path: str):
        cascade_calls.append(path)
        return Cascade()

    cv2.CascadeClassifier = make_cascade
    detector = create_face_detector(
        cv2, SimpleNamespace(tasks=object()), model_selection=0, min_detection_confidence=0.5
    )

    assert detector.detect(_Frame()) == [FaceBox(0.05, 0.2, 0.2, 0.5)]
    detector.close()
    assert cascade_calls == ["haar/haarcascade_frontalface_default.xml"]
    assert cv2.converted == [cv2.COLOR_BGR2GRAY]


def test_legacy_constructor_attribute_failure_uses_haar_fallback() -> None:
    cv2 = _ColorCv2()
    cv2.data = SimpleNamespace(haarcascades="haar/")
    cv2.CascadeClassifier = lambda _path: SimpleNamespace(
        empty=lambda: True,
    )

    class BrokenLegacyDetector:
        def __init__(self, **kwargs) -> None:
            raise AttributeError("legacy API is incomplete")

    mediapipe = SimpleNamespace(
        solutions=SimpleNamespace(face_detection=SimpleNamespace(FaceDetection=BrokenLegacyDetector))
    )
    detector = create_face_detector(
        cv2, mediapipe, model_selection=0, min_detection_confidence=0.5
    )

    assert detector.detect(_Frame()) == []
    detector.close()


def test_legacy_processing_failure_is_not_hidden_by_fallback() -> None:
    cv2 = _ColorCv2()

    class FailingLegacyDetector:
        def process(self, frame):
            raise RuntimeError("legacy processing failed")

        def close(self) -> None:
            pass

    mediapipe = SimpleNamespace(
        solutions=SimpleNamespace(
            face_detection=SimpleNamespace(FaceDetection=lambda **kwargs: FailingLegacyDetector())
        )
    )
    detector = create_face_detector(
        cv2, mediapipe, model_selection=0, min_detection_confidence=0.5
    )

    with pytest.raises(RuntimeError, match="legacy processing failed"):
        detector.detect(_Frame())
