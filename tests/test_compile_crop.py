"""compile.build_plan — the 9:16 crop-then-scale path.

Isolated: _probe_transfer is stubbed so no ffprobe runs; we assert the generated filtergraph. crop
absent ⇒ the legacy letterbox scale+pad (regression hinge); crop=center ⇒ a static crop
+ scale, no pad; crop=reframe with a fixture reframe.json ⇒ a concrete crop=cw:ch:cx:cy rect.
"""
from __future__ import annotations

import json

from pipeline import compile as compile_mod


def _doc(*, crop=None, width: int = 1080, height: int = 1920) -> dict:
    d: dict = {
        "schemaVersion": 1, "name": "zzshort", "fps": 30, "width": width, "height": height,
        "revision": 0, "transcript": None,
        "tracks": [
            {"id": "sp", "kind": "speech", "clips": [
                {"id": "s0", "media": {"source": "media/rec.mp4", "proxy": None},
                 "srcInF": 0, "srcOutF": 90, "tlStartF": 0, "gainDb": 0.0},
            ]},
        ],
    }
    if crop is not None:
        d["crop"] = crop
    return d


def test_crop_none_contains_speech_never_stretches(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    fg = compile_mod.build_plan(_doc()).filter_complex
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in fg
    assert "pad=" not in fg
    assert "crop=" not in fg


def test_crop_center_emits_crop_then_scale_no_pad(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    fg = compile_mod.build_plan(_doc(crop={"mode": "center"})).filter_complex
    assert "crop=w='min(iw,ih*1080/1920)':h='min(ih,iw*1920/1080)':x='(iw-ow)/2':y='(ih-oh)/2'" in fg
    assert "scale=1080:1920" in fg
    assert "pad=" not in fg


def test_crop_reframe_emits_concrete_rect(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    meta = {
        "clip_id": "s0", "aspect_ratio": "1080:1920", "face_coverage_pct": 90.0,
        "frame_crops": [
            {"frame_number": n, "timestamp": n / 30.0, "cx": 960, "cy": 540,
             "crop_x": 690, "crop_y": 60, "crop_w": 540, "crop_h": 960}
            for n in (0, 30, 60)
        ],
    }
    (tmp_path / "rec.reframe.json").write_text(json.dumps(meta), encoding="utf-8")
    doc = _doc(crop={"mode": "reframe", "metaPath": "rec.reframe.json"})
    fg = compile_mod.build_plan(doc, project_dir=tmp_path).filter_complex
    assert "crop=540:960:690:60" in fg
    assert "scale=1080:1920" in fg
    assert "pad=" not in fg


def test_crop_reframe_falls_back_to_center_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda p: "")
    doc = _doc(crop={"mode": "reframe", "metaPath": "absent.reframe.json"})
    fg = compile_mod.build_plan(doc, project_dir=tmp_path).filter_complex
    assert "crop=w='min(iw,ih*1080/1920)'" in fg  # center fallback
    assert "pad=" not in fg
