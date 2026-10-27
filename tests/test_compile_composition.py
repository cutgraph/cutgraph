"""Composed multi-track render parity fixtures."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import config
from pipeline import compile as compile_mod


def _doc() -> dict:
    return {
        "schemaVersion": 2,
        "name": "composition",
        "fps": 30,
        "width": 640,
        "height": 360,
        "revision": 1,
        "tracks": [
            {"id": "speech", "kind": "speech", "clips": [{
                "id": "dialogue",
                "media": {"source": "media/base.mp4"},
                "srcInF": 0,
                "srcOutF": 150,
                "tlStartF": 0,
                "gainDb": 0,
            }]},
            {"id": "title", "kind": "text", "items": [{
                "id": "hook",
                "text": "Layered",
                "preset": "hook",
                "pos": {"x": 0.5, "y": 0.2},
                "tlStartF": 15,
                "durF": 30,
            }]},
            {"id": "visual", "kind": "video", "clips": [
                {
                    "id": "still",
                    "media": {"source": "media/card.png"},
                    "srcInF": 0,
                    "srcOutF": 90,
                    "tlStartF": 30,
                    "transform": {"x": 0.75, "y": 0.25, "scale": 0.4, "fit": "contain"},
                },
                {
                    "id": "silent",
                    "media": {"source": "media/silent.mp4"},
                    "srcInF": 0,
                    "srcOutF": 60,
                    "tlStartF": 120,
                    "transform": {"x": 0.25, "y": 0.75, "scale": 0.5, "fit": "cover"},
                },
            ]},
            {"id": "music", "kind": "audio", "clips": [{
                "id": "bed",
                "media": {"source": "media/music.wav"},
                "srcInF": 0,
                "srcOutF": 210,
                "tlStartF": 0,
                "gainDb": -6,
                "duckDb": -12,
            }]},
        ],
    }


def test_composition_plan_matches_preview_clock_layers_and_mix(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda _path: "")
    monkeypatch.setattr(
        compile_mod,
        "_probe_has_audio",
        lambda path: Path(path).name in {"base.mp4", "music.wav"},
    )

    plan = compile_mod.build_plan(_doc())
    fg = plan.filter_complex

    # The trailing audio bed extends the black canvas to seven seconds. Still inputs loop, while
    # ordinary media retain normal -i semantics.
    assert "color=c=black:s=640x360:r=30:d=7.000000" in fg
    image_i = plan.inputs.index(str(config.STATE_HOME / "media" / "card.png"))
    assert plan.inputs[image_i - 5:image_i] == ["-loop", "1", "-framerate", "30", "-i"]

    # Text belongs below the later visual track because project track order is the layer stack.
    assert fg.index("drawtext=") < fg.index("[vclipstill]overlay=")
    assert "scale=256:144:force_original_aspect_ratio=decrease" in fg
    assert "overlay=x='480.000-w/2':y='90.000-h/2'" in fg
    assert "scale=320:180:force_original_aspect_ratio=increase,crop=320:180" in fg

    # Silent imported video contributes no invalid audio pad. The bed keeps its gain and receives
    # its own deterministic duck only across the dialogue's [0,5s) span.
    assert "[2:a]" not in fg
    assert "volume=-6.000dB,volume=-12.000dB:enable='between(t,0.000000,5.000000)'" in fg
    assert "sidechaincompress" not in fg
    assert "amix=inputs=3:duration=first:normalize=0" in fg
    assert "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]" in fg


def test_duck_falls_back_to_base_gain_without_dialogue(monkeypatch) -> None:
    doc = _doc()
    doc["tracks"] = [track for track in doc["tracks"] if track["kind"] != "speech"]
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda _path: "")
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda path: Path(path).suffix == ".wav")

    fg = compile_mod.build_plan(doc).filter_complex
    assert "volume=-6.000dB" in fg
    assert "volume=-12.000dB" not in fg


def test_silent_speech_track_still_defines_preview_duck_clock(monkeypatch) -> None:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda _path: "")
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda path: Path(path).name == "music.wav")

    fg = compile_mod.build_plan(_doc()).filter_complex
    assert "[0:a]" not in fg
    assert "volume=-12.000dB:enable='between(t,0.000000,5.000000)'" in fg


def test_image_input_looping_uses_demuxer_appropriate_options() -> None:
    assert compile_mod._input_args("card.png", 30) == [
        "-loop", "1", "-framerate", "30", "-i", "card.png",
    ]
    assert compile_mod._input_args("animation.gif", 30) == [
        "-stream_loop", "-1", "-i", "animation.gif",
    ]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_real_ffmpeg_composed_image_and_trailing_audio(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    base = media / "base.mp4"
    card = media / "card.png"
    music = media / "music.wav"
    dest = tmp_path / "composed.mp4"

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(base)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=80x80:d=0.1",
         "-frames:v", "1", str(card)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=3",
         str(music)],
        check=True, capture_output=True, text=True,
    )

    doc = {
        "schemaVersion": 2, "name": "realcomp", "fps": 30,
        "width": 320, "height": 240, "revision": 1,
        "tracks": [
            {"id": "speech", "kind": "speech", "clips": [{
                "id": "base", "media": {"source": "media/base.mp4"},
                "srcInF": 0, "srcOutF": 60, "tlStartF": 0,
                "seams": [{"atF": 60, "fadeMs": 30}],
            }]},
            {"id": "image", "kind": "video", "clips": [{
                "id": "card", "media": {"source": "media/card.png"},
                "srcInF": 0, "srcOutF": 30, "tlStartF": 15,
                "transform": {"x": 0.75, "y": 0.25, "scale": 0.4, "fit": "contain"},
            }]},
            {"id": "audio", "kind": "audio", "clips": [{
                "id": "music", "media": {"source": "media/music.wav"},
                "srcInF": 0, "srcOutF": 90, "tlStartF": 30,
                "gainDb": -9, "duckDb": -6,
            }]},
        ],
    }
    plan = compile_mod.build_plan(doc, project_dir=tmp_path)
    cmd = compile_mod.build_encode_cmd(plan, dest, encoder="libx264")
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,codec_name,width,height,sample_rate",
         "-of", "json", str(dest)],
        check=True, capture_output=True, text=True,
    )
    payload = json.loads(probe.stdout)
    assert float(payload["format"]["duration"]) == pytest.approx(4.0, abs=0.08)
    video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in payload["streams"] if stream["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 320, 240)
    assert audio["codec_name"] == "aac"
    assert audio["sample_rate"] == "48000"

    pixel = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "1", "-i", str(dest),
         "-vf", "format=rgb24,crop=1:1:240:60", "-frames:v", "1",
         "-f", "rawvideo", "-"],
        check=True, capture_output=True,
    ).stdout[:3]
    red, green, blue = pixel
    assert red > 180 and red > green * 2 and red > blue * 2


def test_untransformed_footage_is_never_stretched(monkeypatch) -> None:
    """A source whose shape differs from the frame (a rotated phone take in a 16:9 video) keeps its
    aspect: speech footage is contained whole, B-roll video covers the frame, as the preview does."""
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda _path: "")
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda path: Path(path).name == "base.mp4")
    doc = _doc()
    doc["tracks"][2]["clips"] = [{
        "id": "stock",
        "media": {"source": "media/stock.mp4"},
        "srcInF": 0,
        "srcOutF": 60,
        "tlStartF": 30,
    }]
    fg = compile_mod.build_plan(doc).filter_complex
    assert "scale=640:360:force_original_aspect_ratio=decrease,setsar=1" in fg
    assert "scale=640:360:force_original_aspect_ratio=increase,crop=640:360" in fg
    assert "scale=640:360," not in fg
