"""Preview/export caption parity with synthetic project-licensed prose, cuts, reordered scenes and an unaligned word in all three formats. Python pages and events match the shared fixture independently checked by TypeScript. Regenerate with tests/fixtures/generate.py."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import caption_pages
from pipeline import project as project_mod

FIXTURE = Path(__file__).parent / "fixtures" / "caption_pages_parity.json"


def _fx() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_export_caption_pages_match_the_shared_parity_fixture() -> None:
    fx = _fx()
    proj = project_mod.from_dict(fx["project"])
    for case in fx["cases"]:
        w, h = case["width"], case["height"]
        assert list(caption_pages.layout(w, h)) == case["layout"]
        assert caption_pages.line_budget(w, h) == case["budget"]
        pages = caption_pages.caption_pages(proj, fx["words"], w, h)
        assert pages == case["pages"]
        assert caption_pages.caption_events(pages) == case["events"]


def test_parity_fixture_covers_the_edge_cases() -> None:
    fx = _fx()
    words = fx["words"]
    wide, tall = fx["cases"][0], fx["cases"][1]
    captioned = [w["w"] for p in wide["pages"] for w in p["words"]]
    unaligned = next(w["w"] for w in words if w["start"] is None)
    assert unaligned in captioned, "an unaligned word rides its neighbour"
    assert len(captioned) < len(words), "cut words are skipped"
    assert sum(a["off"] == b["on"] for a, b in zip(tall["pages"], tall["pages"][1:])) >= 3, "lines follow on"
    gaps = [b["words"][0]["start"] - a["words"][-1]["end"] for a, b in zip(wide["pages"], wide["pages"][1:])]
    assert max(gaps) > caption_pages.GAP_CS, "a silence break"
    assert any(a["off"] < b["on"] for a, b in zip(wide["pages"], wide["pages"][1:])), "a blank pause"
    first = words[next(s["startWord"] for s in fx["project"]["scenes"] if s["id"] == "scene-2")]["w"]
    k = next(i for i, p in enumerate(wide["pages"]) if p["words"][0]["w"] == first)
    line = " ".join(w["w"] for w in wide["pages"][k - 1]["words"])
    assert len(line) + 1 + len(first) <= wide["budget"], "a scene change breaks a line that would fit"
    assert {s for e in wide["events"] for s in e["states"]} == {"C", "P", "U"}
    assert all(len(" ".join(w["w"] for w in p["words"])) <= tall["budget"] for p in tall["pages"])


def test_layout_matches_motion_core_layout() -> None:
    """motion-core layoutFor: capFs 46/58/44 at 1080 px, baseline 0.935H (0.9H for 9:16)."""
    assert caption_pages.layout(1920, 1080) == (1080 * 0.935, 46.0)
    assert caption_pages.layout(1080, 1920) == (1920 * 0.9, 58.0)
    assert caption_pages.layout(1080, 1080) == (1080 * 0.935, 44.0)
    assert caption_pages.layout(1280, 720) == (720 * 0.935, 46 * 720 / 1080)
    assert caption_pages.line_budget(1920, 1080) == caption_pages.line_budget(1280, 720) == 54
