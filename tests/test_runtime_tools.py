from __future__ import annotations

import os
from pathlib import Path

import pytest

from pipeline import runtime_tools


def test_packaged_tools_and_font_take_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "bundle" / "app"
    (root.parent / "ffmpeg").mkdir(parents=True)
    (root / "assets" / "fonts" / "liberation-sans").mkdir(parents=True)
    ffmpeg = root.parent / "ffmpeg" / "ffmpeg.exe"
    ffprobe = root.parent / "ffmpeg" / "ffprobe.exe"
    font = root / "assets" / "fonts" / "liberation-sans" / "LiberationSans-Bold.ttf"
    for path in (ffmpeg, ffprobe, font):
        path.write_bytes(b"fixture")
    (root.parent / "bundle-manifest.json").write_text("{}")

    assert runtime_tools.ffmpeg_path(source_root=root) == ffmpeg.resolve()
    assert runtime_tools.ffprobe_path(source_root=root) == ffprobe.resolve()
    assert runtime_tools.font_path(source_root=root) == font.resolve()
    report = runtime_tools.capability_report(source_root=root)
    assert report == {"ffmpeg": True, "ffprobe": True, "font": True}
    assert runtime_tools.bundle_root(root) == root.parent
    assert runtime_tools.bundle_root(tmp_path) is None


def test_tool_overrides_do_not_change_bundled_fonts_or_mutate_environment(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "custom-ffmpeg.exe"
    ffprobe = tmp_path / "custom-ffprobe.exe"
    font = tmp_path / "custom.ttf"
    for path in (ffmpeg, ffprobe, font):
        path.write_bytes(b"fixture")
    environ = {
        "CUTGRAPH_FFMPEG": str(ffmpeg),
        "CUTGRAPH_FFPROBE": str(ffprobe),
        "CUTGRAPH_FONTFILE": str(font),
        "PATH": "original-path",
    }
    result = runtime_tools.runtime_environment(environ=environ, source_root=tmp_path / "none")
    assert runtime_tools.font_path(source_root=tmp_path).parent == tmp_path / "assets/fonts/liberation-sans"
    assert result["CUTGRAPH_FONTFILE"] == str(font)
    assert result["PATH"].split(os.pathsep)[0] == str(tmp_path)
    assert environ["PATH"] == "original-path"


def test_tool_resolution_fails_closed_for_unknown_tools(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported Cutgraph runtime tool"):
        runtime_tools.resolve_tool("node", source_root=tmp_path, environ={})


def test_font_style_is_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported Cutgraph font style"):
        runtime_tools.font_path("thin", source_root=tmp_path)


def test_font_filter_path_escapes_bundled_font(tmp_path: Path) -> None:
    font = tmp_path / "assets/fonts/liberation-sans/LiberationSans-Bold.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"fixture")
    value = runtime_tools.font_filter_path(source_root=tmp_path)
    escaped = font.resolve().as_posix().replace(":", "\\:")
    assert value == f"'{escaped}'"


def test_missing_bundled_font_never_falls_back(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Bundled overlay bold font is missing"):
        runtime_tools.font_filter_path(source_root=tmp_path)
