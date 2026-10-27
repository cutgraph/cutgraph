"""Workspace library media in projects: the `/media/workspace/<id>/<file>` preview route,
compile resolution of `workspaces/<id>/library/<file>` refs, and the animated registry graphics.

Real ffmpeg lavfi fixtures, imported through pipeline.library exactly as the drawer's assets are.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

from pipeline.errors import ValidationError
from PIL import Image

import config
from pipeline import compile as compile_stage
from pipeline import graphics
from pipeline import library
from pipeline import project as project_mod
from scripts import studio_app

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


async def _get(path: str, **kw) -> httpx.Response:
    transport = httpx.ASGITransport(app=studio_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
        return await client.get(path, **kw)


@pytest.fixture()
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(config, "OVERLAYS_DIR", tmp_path / "outputs" / "overlays")
    return tmp_path


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                   check=True, capture_output=True, text=True)


def _import(workspace_id: str, tmp: Path, name: str) -> dict:
    src = tmp / f"src-{name}"
    if name.endswith(".png"):
        _ffmpeg("-f", "lavfi", "-i", "color=c=blue:size=32x32", "-frames:v", "1", str(src.with_suffix(".png")))
    elif name.endswith(".mp4"):
        _ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=1", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(src.with_suffix(".mp4")))
    else:
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src.with_suffix(".wav")))
    asset, _ = library.import_asset(workspace_id, src.with_suffix(Path(name).suffix), display_name=name)
    return asset


def _ref(workspace_id: str, asset: dict) -> str:
    return f"workspaces/{workspace_id}/library/{asset['file']}"


# ── the preview route ────────────────────────────────────────────────────────────

def test_route_serves_library_files_with_type_and_ranges(state: Path, tmp_path: Path) -> None:
    ws = library.create_workspace("garden")["id"]
    image, audio = _import(ws, tmp_path, "plate.png"), _import(ws, tmp_path, "bed.wav")
    payload = (config.WORKSPACES_DIR / ws / "library" / image["file"]).read_bytes()

    whole = _run(_get(f"/media/workspace/{ws}/{image['file']}"))
    assert whole.status_code == 200
    assert whole.content == payload
    assert whole.headers["content-type"] == "image/png"
    ranged = _run(_get(f"/media/workspace/{ws}/{audio['file']}", headers={"Range": "bytes=0-9"}))
    assert ranged.status_code == 206
    assert len(ranged.content) == 10
    assert ranged.headers["content-type"].startswith("audio/")


def test_route_404s_anything_but_a_library_file(state: Path, tmp_path: Path) -> None:
    ws = library.create_workspace("garden")["id"]
    image = _import(ws, tmp_path, "plate.png")
    thumb = f"{image['id']}.jpg"
    assert (config.WORKSPACES_DIR / ws / "library" / "thumbs" / thumb).is_file()
    for url in (
        f"/media/workspace/unknown-ws/{image['file']}",          # well-formed id, no such workspace
        f"/media/workspace/{ws.upper()}/{image['file']}",        # id outside the closed charset
        f"/media/workspace/{ws}/assets.json",                     # the manifest
        f"/media/workspace/{ws}/workspace.json",
        f"/media/workspace/{ws}/thumbs/{thumb}",                  # a sub-path
        f"/media/workspace/{ws}/..%2Fworkspace.json",             # encoded traversal
        f"/media/workspace/{ws}/{image['file'].upper()}",         # not the content-addressed name
        f"/media/workspace/{ws}/{image['id']}.txt",               # unsupported type
        f"/media/workspace/{ws}/{'0' * 16}.png",                  # right shape, not imported
        f"/media/workspace/..%2F..%2Fdata/{image['file']}",
    ):
        assert _run(_get(url)).status_code == 404, url


def test_media_refs_parse_only_library_files() -> None:
    assert library.parse_media_ref("workspaces/garden-1a2b3c/library/0123456789abcdef.png") == (
        "garden-1a2b3c", "0123456789abcdef.png")
    assert library.parse_media_ref("workspaces/garden/library/0123456789abcdef.png") == (
        "garden", "0123456789abcdef.png")
    for bad in ("workspaces/garden/library/../assets.json",
                "workspaces/garden/library/thumbs/0123456789abcdef.jpg", "workspaces/garden/0123456789abcdef.png",
                "data/source/clip.mp4", "workspaces/../library/0123456789abcdef.png"):
        assert library.parse_media_ref(bad) is None, bad


# ── compile ──────────────────────────────────────────────────────────────────────

def _doc(broll: list[dict], audio: list[dict] | None = None, items: list[dict] | None = None) -> dict:
    tracks: list[dict] = [{"id": "video", "kind": "video", "clips": broll}]
    if audio:
        tracks.append({"id": "audio", "kind": "audio", "clips": audio})
    if items:
        tracks.append({"id": "text_1", "kind": "text", "items": items})
    return {"schemaVersion": project_mod.CURRENT_SCHEMA_VERSION, "name": "demo", "fps": 30, "width": 1920,
            "height": 1080, "revision": 1, "tracks": tracks}


def test_compile_resolves_workspace_media_under_state_home(state: Path, tmp_path: Path) -> None:
    ws = library.create_workspace("garden")["id"]
    image, clip, music = (_import(ws, tmp_path, n) for n in ("plate.png", "move.mp4", "bed.wav"))
    doc = _doc(
        [{"id": "plate", "media": {"source": _ref(ws, image), "proxy": None}, "srcInF": 0, "srcOutF": 90, "tlStartF": 0},
         {"id": "move", "media": {"source": _ref(ws, clip), "proxy": None}, "srcInF": 0, "srcOutF": 30, "tlStartF": 90}],
        audio=[{"id": "bed", "media": {"source": _ref(ws, music), "proxy": None}, "srcInF": 0, "srcOutF": 30,
                "tlStartF": 0, "duckDb": -12}],
    )
    plan = compile_stage.build_plan(doc)
    library_dir = config.WORKSPACES_DIR / ws / "library"
    for asset in (image, clip, music):
        assert str(library_dir / asset["file"]) in plan.inputs
    image_at = plan.inputs.index(str(library_dir / image["file"]))
    assert plan.inputs[image_at - 5:image_at - 1] == ["-loop", "1", "-framerate", "30"]  # stills loop


def test_project_rejects_traversal_out_of_a_workspace() -> None:
    doc = _doc([{"id": "x", "media": {"source": "workspaces/garden/library/../../../secret.png", "proxy": None},
                 "srcInF": 0, "srcOutF": 30, "tlStartF": 0}])
    with pytest.raises(ValidationError):
        project_mod.validate(project_mod.from_dict(doc))


# ── animated registry graphics ────────────────────────────────────────────────────

def _graphic(component_id: str, start: int = 30, dur: int = 120) -> dict:
    return {"type": "graphic", "id": f"g-{component_id}", "componentId": component_id, "componentVersion": 1,
            "props": graphics.default_props(component_id), "pos": {"x": 0.5, "y": 0.5}, "scale": 1,
            "tlStartF": start, "durF": dur}


@pytest.mark.parametrize("component_id", [d.id for d in graphics.DEFINITIONS])
def test_graphic_filtergraph_fades_and_slides_with_registry_timing(component_id: str, state: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compile_stage, "_probe_has_audio", lambda _path: False)
    monkeypatch.setattr(compile_stage, "_probe_transfer", lambda _path: "")
    doc = _doc([{"id": "bg", "media": {"source": "data/source/bg.mp4", "proxy": None}, "srcInF": 0, "srcOutF": 300,
                 "tlStartF": 0}], items=[_graphic(component_id)])
    graph = compile_stage.build_plan(doc).filter_complex
    motion = graphics.graphic_motion(120, 1080)
    assert 9 <= motion["inF"] <= 12 and motion["inF"] == motion["outF"]  # ~300–400 ms at 30 fps
    d = f"{motion['inF'] / 30:.6f}"
    layer = next(chain for chain in graph.split(";") if chain.endswith("[gfx0]"))
    assert layer.startswith("color=c=black@0:s=1920x1080:r=30:d=4.000000,format=rgba,")
    assert f"fade=t=in:st=0:d={d}:alpha=1" in layer
    assert f"fade=t=out:st={(120 - motion['outF']) / 30:.6f}:d={d}:alpha=1" in layer
    assert layer.endswith("trim=start_frame=0:end_frame=120,setpts=round(PTS-STARTPTS+1.000000/TB)[gfx0]")
    overlay = next(chain for chain in graph.split(";") if "[gfx0]overlay=" in chain)
    enter = f"pow(1-clip((t-1.000000)/{d},0,1),2)"
    leave = f"pow(clip((t-{(30 + 120 - motion['outF']) / 30:.6f})/{d},0,1),2)"
    assert f"overlay=x=0:y='{motion['slidePx']}*({enter}+{leave})':eof_action=pass" in overlay


def test_windowed_graphic_keeps_the_authored_animation_clock(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compile_stage, "_probe_has_audio", lambda _path: False)
    monkeypatch.setattr(compile_stage, "_probe_transfer", lambda _path: "")
    doc = _doc([{"id": "bg", "media": {"source": "data/source/bg.mp4", "proxy": None}, "srcInF": 0, "srcOutF": 300,
                 "tlStartF": 0}], items=[_graphic("lower-third", start=30, dur=120)])
    graph = compile_stage.build_plan(doc, window=(60, 240)).filter_complex
    layer = next(chain for chain in graph.split(";") if chain.endswith("[gfx0]"))
    # The window starts 30 frames into the graphic: render the authored span, drop its first 30 frames.
    assert "d=4.000000" in layer and "trim=start_frame=30:end_frame=120" in layer
    overlay = next(chain for chain in graph.split(";") if "[gfx0]overlay=" in chain)
    assert re.search(r"clip\(\(t--1\.000000\)/", overlay)  # entrance began 1 s before the window


def test_animated_graphic_renders_through_ffmpeg(state: Path) -> None:
    bg = state / "data" / "source" / "bg.mp4"
    bg.parent.mkdir(parents=True)
    _ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(bg))
    doc = _doc([{"id": "bg", "media": {"source": "data/source/bg.mp4", "proxy": None}, "srcInF": 0, "srcOutF": 60,
                 "tlStartF": 0}], items=[_graphic("callout", start=10, dur=40)])
    doc["width"], doc["height"] = 320, 180
    plan = compile_stage.build_plan(doc)
    out = state / "frames"
    out.mkdir()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *plan.inputs, "-filter_complex",
                    plan.filter_complex, "-map", "[vout]", str(out / "%02d.png"), "-map", "[aout]", "-f", "null", "-"],
                   check=True, capture_output=True, text=True, cwd=state)
    assert len(list(out.glob("*.png"))) == 60
    box = graphics.graphic_geometry("callout", graphics.default_props("callout"), 320, 180, {"x": 0.5, "y": 0.5}, 1)["box"]
    probe = (box["x"] + box["width"] // 2, box["y"] + box["height"] - 6)

    def luma(frame: int) -> int:
        with Image.open(out / f"{frame + 1:02d}.png") as im:
            return im.convert("L").getpixel(probe)  # type: ignore[return-value]

    gray, first, landed, gone = luma(9), luma(10), luma(30), luma(50)
    assert first == gray == gone  # frame 0 of the span is fully transparent; nothing after it
    assert landed < gray - 60     # the dark panel is fully in by mid-span
    assert gray > luma(12) > landed  # and part-way through its fade two frames in
