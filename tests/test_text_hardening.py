"""Reject trailing line breaks in structured identifiers and caption events."""
from __future__ import annotations

import pytest

import config
from pipeline import caption_burn, channels, library, project
from pipeline.errors import ValidationError


@pytest.mark.parametrize("separator", ["\r", "\n", "\r\n"])
@pytest.mark.parametrize("look", ["outline", "pill"])
def test_caption_text_cannot_add_dialogue_lines(separator: str, look: str) -> None:
    text = f"first{separator}Dialogue: injected"
    if look == "outline":
        document = caption_burn.build_karaoke_ass(
            [{"word": text, "start": 0, "end": 1}], 1920, 1080,
            style=channels.resolve_caption_style(None),
        )
    else:
        document = caption_burn.build_pill_ass(
            [{"on": 0, "off": 100, "text": text, "words": [
                {"w": text, "start": 0, "end": 100},
            ]}], 1920, 1080,
        )
    assert "\r" not in document
    assert not any(line.startswith("Dialogue: injected") for line in document.splitlines())
    assert "first Dialogue: injected" in document


def test_brand_colour_rejects_trailing_newline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path)
    workspace = library.create_workspace("Example workspace")
    with pytest.raises(library.LibraryError, match="colour"):
        library.set_brand(workspace["id"], {"accent": "#123abc\n"})
    assert library.brand_of({"brand": {"accent": "#123abc\n"}})["accent"] == library.BRAND_ACCENT


@pytest.mark.parametrize("shot", [{"mark": "AB\n"}, {"screen": {"asset": "a" * 16 + "\n"}}])
def test_shot_identifiers_reject_trailing_newline(shot: dict) -> None:
    document = project.from_dict({"name": "video", "tracks": [], "scenes": [
        {"id": "scene", "title": "Example", "role": "Hook", "startWord": 0, "shot": shot},
    ]})
    with pytest.raises(ValidationError):
        project.validate(document)
