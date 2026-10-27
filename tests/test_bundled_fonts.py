from pathlib import Path
import re
import struct
import string

from PIL import ImageFont
import pytest

import config
from pipeline import caption_burn, caption_pages, scene_render
from pipeline.runtime_tools import font_path


def _table(path: Path, tag: bytes) -> bytes:
    data = path.read_bytes()
    count = struct.unpack_from(">H", data, 4)[0]
    for offset in range(12, 12 + 16 * count, 16):
        name, _, start, length = struct.unpack_from(">4sIII", data, offset)
        if name == tag:
            return data[start:start + length]
    raise AssertionError(f"Missing font table {tag!r}")


def test_caption_metrics_and_browser_mirror_match_the_bundled_font() -> None:
    path = font_path("bold", role="sans")
    os2 = _table(path, b"OS/2")
    asc, desc = struct.unpack_from(">HH", os2, 74)
    upm = struct.unpack_from(">H", _table(path, b"head"), 18)[0]
    assert (caption_burn._PILL_ASC, caption_burn._PILL_DESC, caption_burn._PILL_UPM) == (asc, desc, upm)
    font = ImageFont.truetype(str(path), upm)
    assert font.getname() == ("Inter", "Bold")
    sample = string.ascii_uppercase + string.ascii_lowercase + string.digits + " "
    assert caption_pages.CHAR_EM == sum(font.getlength(c) for c in sample) / len(sample) / upm
    mirror = (config.CODE_ROOT / "ui/src/preview/captionPages.ts").read_text(encoding="utf-8")
    assert f"CHAR_EM = {caption_pages.CHAR_EM};" in mirror
    pill = (config.CODE_ROOT / "ui/src/preview/PillCaption.tsx").read_text(encoding="utf-8")
    assert f"const ASC = {asc} / {upm};" in pill
    assert f"const DESC = {desc} / {upm};" in pill


def test_browser_faces_use_the_same_bundled_files() -> None:
    css = (config.CODE_ROOT / "ui/fonts.css").read_text(encoding="utf-8")
    faces = re.findall(r'font-family: "([^"]+)";\s+font-style: normal;\s+font-weight: (\d+);\s+src: url\("\.\./assets/fonts/([^"]+)"\)', css)
    expected = {
        ("Inter", "400"): font_path("regular", role="sans"),
        ("Inter", "600"): font_path("semibold", role="sans"),
        ("Inter", "700"): font_path("bold", role="sans"),
        ("Inter", "900"): font_path("black", role="sans"),
        ("Liberation Sans", "400"): font_path("regular", role="overlay"),
        ("Liberation Sans", "700"): font_path("bold", role="overlay"),
        ("Cascadia Code", "400"): font_path("regular", role="mono"),
    }
    assert len(faces) == len(expected)
    for family, weight, relative in faces:
        assert config.CODE_ROOT / "assets/fonts" / relative == expected[family, weight]
        assert expected[family, weight].is_file()


@pytest.mark.parametrize("weight", ["regular", "semibold", "bold", "black"])
def test_each_scene_font_invalidates_the_cache(weight: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fonts = {style: tmp_path / f"{style}.ttf" for style in ("regular", "semibold", "bold", "black")}
    for style, path in fonts.items():
        path.write_bytes(font_path(style, role="sans").read_bytes())
    monkeypatch.setattr(scene_render, "font_path", lambda style, **kwargs: fonts[style])
    before = scene_render.motion_core_version()
    fonts[weight].write_bytes(fonts[weight].read_bytes() + b"changed")
    after = scene_render.motion_core_version()
    assert before != after
    request = {"startF": 0, "firstWordF": 0, "cueWordF": None, "shot": {}, "durF": 90}
    assert scene_render.cache_key(request, before) != scene_render.cache_key(request, after)
