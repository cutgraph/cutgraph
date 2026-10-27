"""Animated scenes in the export: compile compositing and the render_final wiring.

No GPU: ffprobe is stubbed for the filtergraph, and the Node renderer and the compiler are mocked for
the job flow, which still runs the real studio job path (`_run_detached_job` → `_local_render`).
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import config
from pipeline import compile as compile_mod
from pipeline import contracts, scene_render
from pipeline import jobs as jobs_mod
from pipeline.compile import SceneLayer
from scripts import studio_app
from studio import compute
from studio import state as studio_state

WORDS = [{"word": f"w{i}", "start": i * 0.5 + 0.1, "end": i * 0.5 + 0.4} for i in range(12)]
ANIM = SceneLayer("scenes/anim.mp4", 0, 63, False)
CAMEO = SceneLayer("scenes/cameo.mov", 123, 57, True)


def _doc(name: str = "zzscenex") -> dict:
    return {
        "schemaVersion": 3, "name": name, "fps": 30, "width": 1920, "height": 1080, "revision": 1,
        "transcript": None, "captions": {"enabled": True},
        "tracks": [
            {"id": "speech", "kind": "speech", "clips": [
                {"id": "c0", "media": {"source": "data/source/talk.mp4", "proxy": None},
                 "srcInF": 0, "srcOutF": 180, "tlStartF": 0, "gainDb": 0.0}]},
            {"id": "broll", "kind": "video", "clips": [
                {"id": "b0", "media": {"source": "data/source/broll.mp4", "proxy": None},
                 "srcInF": 0, "srcOutF": 30, "tlStartF": 70, "gainDb": 0.0}]},
            {"id": "text", "kind": "text", "items": [
                {"id": "t0", "text": "Hello", "preset": "hook", "pos": {"x": 0.5, "y": 0.2}, "tlStartF": 0, "durF": 30}]},
        ],
        "scenes": [
            {"id": "s1", "title": "Intro", "role": "Hook", "startWord": 0,
             "shot": {"mode": "animation", "pose": "cheer", "headline": "Real export"}},
            {"id": "s2", "title": "Middle", "role": "Beat", "startWord": 4},
            {"id": "s3", "title": "Outro", "role": "Wrap", "startWord": 8, "shot": {"mode": "cameo"}},
        ],
    }


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(compile_mod, "_probe_transfer", lambda _p: "")
    monkeypatch.setattr(compile_mod, "_probe_has_audio", lambda p: Path(p).stem == "talk")
    for name, sub in [("STATE_HOME", ""), ("PROJECTS_DIR", "outputs/projects"), ("TRANSCRIPTS_DIR", "outputs/transcripts"),
                      ("FINAL_DIR", "outputs/final"), ("JOBS_DIR", "outputs/jobs"), ("RUN_LOG", "run.log")]:
        monkeypatch.setattr(config, name, tmp_path / sub if sub else tmp_path)
    (tmp_path / "outputs" / "transcripts").mkdir(parents=True)
    (tmp_path / "outputs" / "transcripts" / "talk.words.json").write_text(
        json.dumps({"segments": [{"words": WORDS}]}), encoding="utf-8")
    jobs_mod._registry.clear()
    yield tmp_path
    jobs_mod._registry.clear()


# ── compile filtergraph ─────────────────────────────────────────────────────────────────────────────

def test_rendered_scenes_are_extra_inputs_overlaid_at_their_spans(state: Path) -> None:
    plan = compile_mod.build_plan(_doc(), project_dir=state, scenes=[ANIM, CAMEO])
    assert plan.inputs[-4:] == ["-i", "scenes/anim.mp4", "-i", "scenes/cameo.mov"]
    fg = plan.filter_complex
    # Inputs 0/1 are the talk and the B-roll, so the scenes are inputs 2 and 3.
    assert "[2:v]format=yuv420p,setpts=round(PTS-STARTPTS+0.000000/TB)[scene0]" in fg
    assert "[3:v]format=yuva420p,setpts=round(PTS-STARTPTS+4.100000/TB)[scene1]" in fg
    assert "[scene0]overlay=x=0:y=0:enable='between(t,-0.016667,2.083333)':eof_action=pass:repeatlast=0" in fg
    assert "[scene1]overlay=x=0:y=0:enable='between(t,4.083333,5.983333)':eof_action=pass:repeatlast=0" in fg
    assert "[2:a]" not in fg and "[3:a]" not in fg, "footage audio is unchanged"


def test_scenes_sit_above_footage_and_broll_and_below_text_and_captions(state: Path) -> None:
    fg = compile_mod.build_plan(_doc(), project_dir=state, scenes=[ANIM, CAMEO]).filter_complex
    order = [fg.index(s) for s in ("[vclipc0]overlay", "[vclipb0]overlay", "[scene0]overlay", "[scene1]overlay",
                                   "drawtext=", "ass=")]
    assert order == sorted(order)


def test_a_4k_doc_composites_the_1080_design_at_twice_the_size(state: Path) -> None:
    """A 4K export is its own 3840x2160 graph, drawn at twice the 1080 design: the canvas and
    footage box, the text and its outline, and the caption pill all double."""
    def build(width: int, height: int) -> tuple[str, str]:
        fg = compile_mod.build_plan({**_doc(), "width": width, "height": height}, project_dir=state,
                                    scenes=[ANIM, CAMEO]).filter_complex
        return fg, (state / fg.split("ass=")[1].split(":fontsdir=")[0]).read_text(encoding="utf-8")

    def found(pattern: str, s: str) -> list[float]:
        match = re.search(pattern, s)
        assert match, pattern
        return [float(n) for n in match.groups()]

    (hd, hd_ass), (uhd, uhd_ass) = build(1920, 1080), build(3840, 2160)
    assert "color=c=black:s=3840x2160" in uhd and "scale=3840:2160:force_original_aspect_ratio=decrease" in uhd
    text =r"fontsize=(\d+):fontcolor=white:borderw=(\d+):bordercolor=black:box=1:boxcolor=black@0.45:boxborderw=(\d+)"
    assert [2 * n for n in found(text, hd)] == found(text, uhd)
    pill = r"Style: Pill,Inter,([\d.]+),"
    assert found(pill, uhd_ass)[0] == pytest.approx(2 * found(pill, hd_ass)[0], abs=0.02)
    assert "PlayResX: 3840" in uhd_ass and "PlayResY: 2160" in uhd_ass
    assert [line for line in uhd.split(";") if "[scene" in line] == [line for line in hd.split(";") if "[scene" in line]


def test_without_scenes_the_graph_is_unchanged(state: Path) -> None:
    assert compile_mod.build_plan(_doc(), project_dir=state) == compile_mod.build_plan(_doc(), project_dir=state, scenes=())
    assert "[scene" not in compile_mod.build_plan(_doc(), project_dir=state).filter_complex


# ── render_final renders the missing scenes, then compiles ────────────────────────────────────────

def _write(doc: dict) -> None:
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.PROJECTS_DIR / f"{doc['name']}.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def pipeline(state: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    seen: dict = {"renders": [], "compiles": [], "events": []}

    def fake_render(request: dict, dest: Path) -> dict:
        seen["renders"].append(request)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"scene")
        return {"event": "done"}

    def fake_compile(doc, dest, *, project_dir=None, output_root=None, **kwargs):  # noqa: ANN001, ANN202
        seen["compiles"].append(kwargs.get("scenes"))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00\x00\x00\x18ftypisom")
        return contracts.StageResult(status="ok", stage="compile", clip_id=doc["name"], output_path=str(dest))

    monkeypatch.setattr(scene_render, "render_scene", fake_render)
    monkeypatch.setattr(compute.compile_stage, "compile_project", fake_compile)
    monkeypatch.setattr(compute, "_emit_job_threadsafe", lambda _p, event: seen["events"].append(event))
    return seen


def _render_final(name: str) -> dict:
    jid = jobs_mod.new_job("render_final", name, {})
    compute._run_detached_job(jid, jobs_mod.get(jid) or {})
    return jobs_mod.get(jid) or {}


def test_render_final_renders_missing_scenes_then_compiles_them_in(pipeline: dict) -> None:
    _write(_doc())
    job = _render_final("zzscenex")
    assert job["status"] == "done", job
    assert [r["mode"] for r in pipeline["renders"]] == ["animation", "cameo"]
    [layers] = pipeline["compiles"]
    assert [(x.startF, x.durF, x.alpha) for x in layers] == [(0, 63, False), (123, 57, True)]
    stages = [(e["status"], e["progressStage"], e["progress"]) for e in pipeline["events"]]
    assert stages == [
        ("running", "Computing", 0.35),
        ("running", "Computing", 0.35),
        ("running", "Rendering animation 1/2", 0.05),
        ("running", "Rendering animation 2/2", 0.2),
        ("running", "Computing", 0.35),
        ("publishing", "Returning result", 0.9),
        ("done", "Complete", 1.0),
    ]
    scenes = [[(x["id"], x["state"]) for x in e["scenes"]] for e in pipeline["events"] if "scenes" in e]
    assert scenes == [
        [("s1", "waiting"), ("s3", "waiting")],
        [("s1", "rendering"), ("s3", "waiting")],
        [("s1", "rendered"), ("s3", "rendering")],
        [("s1", "rendered"), ("s3", "rendered")],
        [("s1", "rendered"), ("s3", "rendered")],
        [("s1", "rendered"), ("s3", "rendered")],
    ]

    pipeline["events"].clear()
    assert _render_final("zzscenex")["status"] == "done"
    assert len(pipeline["renders"]) == 2, "cached scenes are not rendered again"
    assert pipeline["compiles"][-1] == layers
    assert [e["progressStage"] for e in pipeline["events"]] == ["Computing", "Computing", "Returning result", "Complete"]
    assert pipeline["events"][-1]["scenes"] == [{"id": "s1", "state": "cached"}, {"id": "s3", "state": "cached"}]


def test_a_footage_only_project_compiles_exactly_as_before(pipeline: dict) -> None:
    doc = _doc()
    for scene in doc["scenes"]:
        scene.pop("shot", None)
    _write(doc)
    assert _render_final("zzscenex")["status"] == "done"
    assert pipeline["renders"] == [] and pipeline["compiles"] == [None]


def test_a_failed_scene_render_fails_the_export(pipeline: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_request: dict, _dest: Path) -> dict:
        raise scene_render.SceneRenderError("scene render failed: ffmpeg exited 1")

    monkeypatch.setattr(scene_render, "render_scene", boom)
    _write(_doc())
    job = _render_final("zzscenex")
    assert job["status"] == "error" and "scene render failed" in job["error"]
    assert pipeline["compiles"] == []


def test_render_scene_job_prerenders_one_scene(pipeline: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(_doc())
    # A private queue: a worker thread left running by another test must not claim this job.
    monkeypatch.setattr(compute, "_ensure_worker", lambda: None)
    monkeypatch.setattr(studio_state, "_JOB_Q", queue.Queue())

    async def create() -> tuple[int, dict]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://t") as client:
            r = await client.post("/api/jobs", json={"kind": "render_scene", "project": "zzscenex",
                                                      "params": {"sceneId": "s3"}})
            return r.status_code, r.json()

    status, body = asyncio.run(create())
    assert status == 200 and body["status"] == "queued"
    assert studio_state._JOB_Q.get_nowait() == body["jobId"], "dispatched to the single-slot worker"
    job = jobs_mod.get(body["jobId"]) or {}
    assert job["kind"] == "render_scene" and job["meta"] == {"sceneId": "s3"}
    compute._run_detached_job(body["jobId"], job)
    done = jobs_mod.get(body["jobId"]) or {}
    assert done["status"] == "done" and done["rendered"] == 1
    assert [r["mode"] for r in pipeline["renders"]] == ["cameo"]
    assert pipeline["events"][-1]["kind"] == "render_scene"


def _footage_only(doc: dict) -> dict:
    for scene in doc["scenes"]:
        scene.pop("shot", None)
    return doc
