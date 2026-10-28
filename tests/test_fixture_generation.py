"""Regeneration preserves current files and reports drift without writing in check mode."""
from pathlib import Path
import json
import runpy
import shutil
import sys

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
regenerate = runpy.run_path(str(FIXTURES / "generate.py"))["regenerate"]


@pytest.fixture
def fixtures(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES / "inputs", tmp_path / "inputs")
    assert regenerate(tmp_path, check=False) == 0
    return tmp_path


def test_second_run_preserves_every_byte_and_mtime(fixtures: Path) -> None:
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in fixtures.glob("*.json")}
    assert regenerate(fixtures, check=False) == 0
    assert regenerate(fixtures, check=True) == 0
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in fixtures.glob("*.json")}
    assert {p.name for p in FIXTURES.glob("*parity*.json")} <= before.keys()


def test_check_reports_drift_without_writing_and_generation_repairs_it(fixtures: Path) -> None:
    path = fixtures / "scene_specs_parity.json"
    expected = path.read_bytes()
    path.write_bytes(b"{}\n")
    assert regenerate(fixtures, check=True) == 1
    assert path.read_bytes() == b"{}\n"
    assert regenerate(fixtures, check=False) == 0
    assert path.read_bytes() == expected
    path.unlink()
    assert regenerate(fixtures, check=True) == 1
    assert not path.exists()


def test_a_parity_fixture_without_a_generator_fails_loudly(fixtures: Path) -> None:
    (fixtures / "new_parity.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Parity fixtures without inputs"):
        regenerate(fixtures, check=True)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_synthetic_rig_registration_restores_the_registry(
    fixtures: Path, monkeypatch: pytest.MonkeyPatch, existing: bool, fails: bool,
) -> None:
    from pipeline import cast

    previous = {"owner": "client"}
    if existing:
        monkeypatch.setitem(cast.RIGS, "sample-rig", previous)
    else:
        monkeypatch.delitem(cast.RIGS, "sample-rig", raising=False)
    if fails:
        def reject(shot: dict | None, video_cast: dict) -> tuple[dict, dict | None]:
            assert cast.RIGS["sample-rig"]["palettes"] == ["fog", "fern", "clay"]
            raise ValueError("Cannot resolve sample shot")

        monkeypatch.setattr(cast, "resolve_shot", reject)
        with pytest.raises(ValueError, match="Cannot resolve sample shot"):
            regenerate(fixtures, check=True)
    else:
        assert regenerate(fixtures, check=True) == 0
    if existing:
        assert cast.RIGS["sample-rig"] is previous
    else:
        assert "sample-rig" not in cast.RIGS


def _edit_json(path: Path, edit) -> None:
    source = json.loads(path.read_text(encoding="utf-8"))
    edit(source)
    path.write_text(json.dumps(source), encoding="utf-8")


def _generated_words(fixtures: Path) -> list[list[dict]]:
    return [json.loads((fixtures / name).read_text(encoding="utf-8"))["words"]
            for name in ("scene_specs_parity.json", "caption_pages_parity.json")]


def test_stored_timings_are_rejected(fixtures: Path) -> None:
    _edit_json(fixtures / "inputs" / "words.json", lambda source: source.update(timings=[]))
    with pytest.raises(ValueError, match="generator owns the timestamps"):
        regenerate(fixtures, check=True)


def test_the_timing_recipe_alone_drives_every_word_timestamp(fixtures: Path) -> None:
    before = _generated_words(fixtures)[0]
    recipe = json.loads((fixtures / "inputs" / "words.json").read_text(encoding="utf-8"))["timingRecipe"]
    pause, unaligned = min(recipe["pausesAfter"], key=int), recipe["unalignedWord"]

    def lengthen_pause_and_move_unaligned_word(source: dict) -> None:
        source["timingRecipe"]["pausesAfter"][pause] += 0.5
        source["timingRecipe"]["unalignedWord"] = unaligned + 1

    _edit_json(fixtures / "inputs" / "words.json", lengthen_pause_and_move_unaligned_word)
    assert regenerate(fixtures, check=False) == 0
    for after in _generated_words(fixtures):
        assert [w["w"] for w in after] == [w["w"] for w in before]
        cut = int(pause) + 1
        assert after[:cut] == before[:cut]
        assert after[cut]["start"] == pytest.approx(before[cut]["start"] + 0.5)
        assert after[unaligned]["start"] == pytest.approx(before[unaligned - 1]["end"] + recipe["gap"] + 0.5)
        assert after[unaligned + 1]["start"] is None and after[unaligned + 1]["end"] is None


def _link_dir(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        # A junction needs no symlink privilege on Windows and resolves like a link.
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("fixture_name, key", [
    ("scene_specs_parity_acting.json", "castInput"),
    ("cast_shot_parity.json", "rigInput"),
])
@pytest.mark.parametrize("escape", ["traversal", "absolute", "link"])
def test_named_inputs_must_stay_inside_the_inputs_folder(
    fixtures: Path, monkeypatch: pytest.MonkeyPatch, fixture_name: str, key: str, escape: str,
) -> None:
    from pipeline import cast

    inputs = fixtures / "inputs"
    source = json.loads((inputs / fixture_name).read_text(encoding="utf-8"))
    outside = fixtures / "outside"
    outside.mkdir()
    shutil.copyfile(inputs / source[key], outside / "escape.json")
    if escape == "link":
        _link_dir(inputs / "linked", outside)
    name = {"traversal": "../outside/escape.json", "absolute": str((outside / "escape.json").resolve()),
            "link": "linked/escape.json"}[escape]
    _edit_json(inputs / fixture_name, lambda fixture: fixture.update({key: name}))
    previous = {"owner": "client"}
    monkeypatch.setitem(cast.RIGS, "sample-rig", previous)
    with pytest.raises(ValueError, match="must stay inside inputs/"):
        regenerate(fixtures, check=True)
    assert cast.RIGS["sample-rig"] is previous
