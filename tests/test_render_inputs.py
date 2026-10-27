"""Render identity stays read-only and follows the compiler's dependencies."""
from __future__ import annotations

from pathlib import Path

import pytest

import config
from pipeline import jobs, render_inputs
from studio import helpers
from studio.routes import jobs as job_routes


def _doc() -> dict:
    return {
        "schemaVersion": 1,
        "name": "demo",
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "revision": 1,
        "tracks": [{
            "id": "speech", "kind": "speech", "clips": [{
                "id": "clip01", "media": {"source": "data/source.mp4", "proxy": "data/proxy.mp4"},
                "srcInF": 0, "srcOutF": 30, "tlStartF": 0, "gainDb": 0.0,
            }],
        }],
        "crop": {"mode": "reframe"},
        "captions": {"enabled": True},
    }


def _dependencies(root: Path) -> None:
    for ref, payload in {
        "data/source.mp4": b"source",
        "data/proxy.mp4": b"proxy",
        "outputs/reframe/source.reframe.json": b"{}",
        "outputs/transcripts/source.words.json": b"{}",
    }.items():
        path = root / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def test_digest_is_read_only_canonical_and_uses_original_media(tmp_path: Path) -> None:
    _dependencies(tmp_path)
    doc = _doc()
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    digest = render_inputs.inputs_digest(doc, tmp_path, {"kind": "final"})
    assert render_inputs.inputs_digest(dict(reversed(list(doc.items()))), tmp_path, {"kind": "final"}) == digest
    assert {item.ref for item in render_inputs.discover_inputs(doc, tmp_path)} == {
        "data/source.mp4", "outputs/reframe/source.reframe.json", "outputs/transcripts/source.words.json",
    }
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    (tmp_path / "data/proxy.mp4").write_bytes(b"changed")
    assert render_inputs.inputs_digest(doc, tmp_path, {"kind": "final"}) == digest


@pytest.mark.parametrize("ref", [
    "data/source.mp4", "outputs/reframe/source.reframe.json", "outputs/transcripts/source.words.json",
])
def test_digest_changes_with_dependency_bytes(tmp_path: Path, ref: str) -> None:
    _dependencies(tmp_path)
    doc = _doc()
    before = render_inputs.inputs_digest(doc, tmp_path, {"kind": "final"})
    (tmp_path / ref).write_bytes(b"different")
    assert render_inputs.inputs_digest(doc, tmp_path, {"kind": "final"}) != before


def test_digest_tracks_revision_and_render_spec(tmp_path: Path) -> None:
    _dependencies(tmp_path)
    doc = _doc()
    before = render_inputs.inputs_digest(doc, tmp_path, {"kind": "final"})
    assert render_inputs.inputs_digest(doc, tmp_path, {"kind": "preview", "startF": 0, "endF": 10}) != before
    doc["revision"] += 1
    assert render_inputs.inputs_digest(doc, tmp_path, {"kind": "final"}) != before


def test_disabled_captions_do_not_bind_transcript(tmp_path: Path) -> None:
    _dependencies(tmp_path)
    doc = _doc()
    doc["captions"]["enabled"] = False
    assert "outputs/transcripts/source.words.json" not in {
        item.ref for item in render_inputs.discover_inputs(doc, tmp_path)
    }


def test_explicit_missing_reframe_metadata_is_rejected(tmp_path: Path) -> None:
    _dependencies(tmp_path)
    doc = _doc()
    doc["crop"]["metaPath"] = "outputs/reframe/missing.reframe.json"
    with pytest.raises(jobs.JobError) as error:
        render_inputs.inputs_digest(doc, tmp_path, {"kind": "final"})
    assert error.value.code == "MISSING_ARTIFACT"


def test_dependency_outside_state_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    _dependencies(state)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(render_inputs.project_mod, "resolve_media", lambda *args, **kwargs: outside)
    with pytest.raises(jobs.JobError) as error:
        render_inputs.inputs_digest(_doc(), state, {"kind": "final"})
    assert error.value.code == "PATH_ESCAPE"


def test_reparse_dependency_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _dependencies(tmp_path)
    actual_lstat = Path.lstat

    def lstat(path: Path):
        if path == tmp_path / "data/source.mp4":
            return type("FileInfo", (), {"st_mode": 0, "st_file_attributes": 0x400})()
        return actual_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(jobs.JobError) as error:
        render_inputs.inputs_digest(_doc(), tmp_path, {"kind": "final"})
    assert error.value.code == "PATH_ESCAPE"


def test_final_key_tracks_media_revision_params_and_resolved_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dependencies(tmp_path)
    doc = _doc()
    doc["scenes"] = [{"id": "s1", "title": "Open", "role": "Hook", "startWord": 0,
                      "shot": {"mode": "animation", "character": "speaker", "pose": "idle"}}]
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(helpers, "_read_project_doc", lambda _project: (doc, doc["revision"]))
    monkeypatch.setattr(job_routes.cast_mod, "video_cast", lambda _workspace: [])
    look = {"palette": "blue"}
    monkeypatch.setattr(job_routes.cast_mod, "resolve_shot", lambda _shot, _cast: look)
    before_paths = set(tmp_path.rglob("*"))
    initial = job_routes._render_final_dedupe_key("demo", {})
    assert job_routes._render_final_dedupe_key("demo", {}) == initial
    assert set(tmp_path.rglob("*")) == before_paths
    assert job_routes._render_final_dedupe_key("demo", {"quality": "high"}) != initial
    assert job_routes._render_final_dedupe_key("other", {}) != initial
    look["palette"] = "green"
    assert job_routes._render_final_dedupe_key("demo", {}) != initial
    look["palette"] = "blue"
    doc["revision"] += 1
    assert job_routes._render_final_dedupe_key("demo", {}) != initial
    doc["revision"] -= 1
    (tmp_path / "data/source.mp4").write_bytes(b"changed")
    assert job_routes._render_final_dedupe_key("demo", {}) != initial
    assert set(tmp_path.rglob("*")) == before_paths
