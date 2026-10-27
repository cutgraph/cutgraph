"""Regeneration preserves current files and reports drift without writing in check mode."""
from pathlib import Path
import json
import runpy
import shutil

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


def test_recorded_timings_are_rejected(fixtures: Path) -> None:
    path = fixtures / "inputs" / "words.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    source["timings"][0][1] += 0.02
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic timing recipe"):
        regenerate(fixtures, check=True)
