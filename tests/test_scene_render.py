"""Scene render specs and the scene cache (pipeline/scene_render.py).

`scene_specs` is the shared scene render spec rule (the TS preview computes the same list), so these
cases pin it on a small talk: 12 words, word i spoken at source frame 15i+3, three scenes (Hook
animation at word 0, a footage Beat at word 4, a Wrap cameo at word 8 cueing word 10). The cache
tests mock the Node renderer, except one that runs it for real when Node and ffmpeg are present.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

import config
from pipeline import edits
from pipeline import project as project_mod
from pipeline import scene_render
from pipeline.compile import SceneLayer

MEDIA = "data/source/talk.mp4"
WORDS = [{"word": f"w{i}", "start": i * 0.5 + 0.1, "end": i * 0.5 + 0.4} for i in range(12)]


def _doc(clips: Sequence[tuple[int, int, int]] = ((0, 180, 0),), *, width: int = 1920, height: int = 1080) -> dict:
    return {
        "schemaVersion": 3, "name": "zzscene", "fps": 30, "width": width, "height": height,
        "revision": 1, "transcript": None,
        "tracks": [{"id": "speech", "kind": "speech", "clips": [
            {"id": f"c{i}", "media": {"source": MEDIA, "proxy": None},
             "srcInF": a, "srcOutF": b, "tlStartF": tl, "gainDb": 0.0}
            for i, (a, b, tl) in enumerate(clips)
        ]}],
        "scenes": [
            {"id": "s1", "title": "Intro", "role": "Hook", "startWord": 0,
             "shot": {"mode": "animation", "pose": "cheer", "headline": "Real export"}},
            {"id": "s2", "title": "Middle", "role": "Beat", "startWord": 4},
            {"id": "s3", "title": "Outro", "role": "Wrap", "startWord": 8, "shot": {"mode": "cameo", "cueWord": 10}},
        ],
    }


def _spans(specs: list[dict]) -> list[tuple]:
    return [(s["sceneId"], s["mode"], s["startF"], s["durF"], s["firstWordF"], s["cueWordF"]) for s in specs]


def test_uncut_talk_yields_animated_scenes_in_story_order() -> None:
    specs = scene_render.scene_specs(_doc(), WORDS)
    assert _spans(specs) == [("s1", "animation", 0, 63, 3, None), ("s3", "cameo", 123, 57, 123, 153)]
    assert specs[0] == {
        "sceneId": "s1", "mode": "animation", "shot": {"mode": "animation", "pose": "cheer", "headline": "Real export"},
        "role": "Hook", "title": "Intro", "startF": 0, "durF": 63, "firstWordF": 3, "cueWordF": None,
    }


def test_cuts_move_later_scenes_and_the_first_kept_word() -> None:
    # Words 0-1 (source 0..33) cut: the Hook starts on word 2 and the Wrap slides 33 frames left.
    assert _spans(scene_render.scene_specs(_doc([(33, 180, 0)]), WORDS)) == [
        ("s1", "animation", 0, 30, 0, None), ("s3", "cameo", 90, 57, 90, 120),
    ]


def test_a_cut_cue_word_falls_back_to_none() -> None:
    specs = scene_render.scene_specs(_doc([(0, 153, 0), (168, 180, 153)]), WORDS)
    assert _spans(specs)[1] == ("s3", "cameo", 123, 42, 123, None)


def test_a_cue_word_outside_the_scene_is_ignored() -> None:
    doc = _doc()
    doc["scenes"][2]["shot"]["cueWord"] = 2
    assert scene_render.scene_specs(doc, WORDS)[1]["cueWordF"] is None


def test_reorder_follows_story_order_on_the_resequenced_timeline() -> None:
    frames = edits.resolve_word_frames([(w["start"], w["end"]) for w in WORDS], 30)
    moved, _warnings = edits.reorder_scenes(project_mod.from_dict(_doc()), ["s3", "s2", "s1"], word_frames={MEDIA: frames})
    specs = scene_render.scene_specs(project_mod.to_dict(moved), WORDS)
    assert _spans(specs) == [("s3", "cameo", 0, 57, 0, 30), ("s1", "animation", 117, 63, 120, None)]


def test_a_fully_cut_scene_is_excluded() -> None:
    assert _spans(scene_render.scene_specs(_doc([(63, 180, 0)]), WORDS)) == [("s3", "cameo", 60, 57, 60, 90)]


def test_footage_scenes_and_missing_words_render_nothing() -> None:
    doc = _doc()
    doc["scenes"][0]["shot"] = {"mode": "footage", "pose": "cheer"}
    doc["scenes"][2]["shot"] = None
    assert scene_render.scene_specs(doc, WORDS) == []
    assert scene_render.scene_specs(_doc(), []) == []


def test_an_unaligned_boundary_word_takes_its_timed_neighbour() -> None:
    words = copy.deepcopy(WORDS)
    words[8].update(start=None, end=None)
    assert _spans(scene_render.scene_specs(_doc(), words))[1] == ("s3", "cameo", 108, 72, 108, 153)


# ── cache ───────────────────────────────────────────────────────────────────────────────────────────

def _motion_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private copy of what draws a scene, used in place of motion-core so a test can edit it."""
    motion = tmp_path / "motion"
    shutil.copytree(scene_render.MOTION_CORE / "src", motion / "src")
    shutil.copytree(scene_render.MOTION_CORE / "scripts", motion / "scripts")
    renderer = motion / "bin/render-scene.ts"
    renderer.parent.mkdir()
    shutil.copyfile(scene_render.RENDERER, renderer)
    monkeypatch.setattr(scene_render, "MOTION_CORE", motion)
    monkeypatch.setattr(scene_render, "RENDERER", renderer)
    return motion


@pytest.mark.parametrize("source", ["staging.ts", "ideas.ts"])
def test_camera_and_diagram_source_changes_invalidate_cached_scenes(
    source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    motion = _motion_copy(tmp_path, monkeypatch)
    request = scene_render.render_request(scene_render.scene_specs(_doc(), WORDS)[0], width=1920, height=1080, fps=30)
    before = scene_render.motion_core_version()
    path = motion / "src" / source
    path.write_bytes(path.read_bytes() + b"\n")
    after = scene_render.motion_core_version()
    assert before != after
    assert scene_render.cache_key(request, before) != scene_render.cache_key(request, after)


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    transcripts = tmp_path / "outputs" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "talk.words.json").write_text(json.dumps({"segments": [{"words": WORDS}]}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def renders(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake(request: dict, dest: Path) -> dict:
        calls.append(request)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"scene")
        return {"event": "done"}

    monkeypatch.setattr(scene_render, "render_scene", fake)
    return calls


def test_unchanged_scenes_never_rerender(state: Path, renders: list[dict]) -> None:
    progress: list[tuple[int, int]] = []
    layers, rendered = scene_render.ensure_scenes(_doc(), root=state, on_progress=lambda n, m: progress.append((n, m)))
    assert rendered == 2 and progress == [(1, 2), (2, 2)]
    assert [(r["mode"], r["alpha"], r["width"], r["height"], r["fps"]) for r in renders] == [
        ("animation", False, 1920, 1080, 30), ("cameo", True, 1920, 1080, 30),
    ]
    assert [(Path(x.path).suffix, x.startF, x.durF, x.alpha) for x in layers] == [
        (".mp4", 0, 63, False), (".mov", 123, 57, True),
    ]
    assert all(Path(x.path).parent == state / "outputs" / "scenes" for x in layers)

    again, rendered_again = scene_render.ensure_scenes(_doc(), root=state)
    assert rendered_again == 0 and again == layers and len(renders) == 2


@pytest.mark.parametrize("source", ["src/staging.ts", "src/draw.ts", "scripts/node-env.ts", "bin/render-scene.ts"])
def test_an_edit_to_the_drawing_rerenders_every_cached_scene_once(
    source: str, state: Path, renders: list[dict], monkeypatch: pytest.MonkeyPatch,
) -> None:
    motion = _motion_copy(state, monkeypatch)
    before, rendered = scene_render.ensure_scenes(_doc(), root=state)
    assert rendered == 2 and scene_render.ensure_scenes(_doc(), root=state) == (before, 0)
    path = motion / source
    path.write_bytes(path.read_bytes() + b"\n")
    after, rerendered = scene_render.ensure_scenes(_doc(), root=state)
    assert rerendered == 2 and renders[2:] == renders[:2], "the same scenes render again, unchanged"
    assert [layer.path for layer in after] != [layer.path for layer in before]
    assert [(x.startF, x.durF, x.alpha) for x in after] == [(x.startF, x.durF, x.alpha) for x in before]
    assert all(Path(layer.path).exists() for layer in after)
    assert scene_render.ensure_scenes(_doc(), root=state) == (after, 0), "then they are served from the cache"


def test_the_plan_names_each_scene_and_the_render_it_waits_for(state: Path, renders: list[dict]) -> None:
    plans: list[list[tuple[str, int]]] = []
    scene_render.ensure_scenes(_doc(), root=state, on_plan=plans.append)
    assert plans == [[("s1", 1), ("s3", 2)]]
    doc = _doc()
    doc["scenes"][2]["shot"]["expression"] = "happy"
    scene_render.ensure_scenes(doc, root=state, on_plan=plans.append)
    assert plans[-1] == [("s1", 0), ("s3", 1)], "an unchanged scene is served from the cache"


def test_only_the_changed_scene_rerenders(state: Path, renders: list[dict]) -> None:
    scene_render.ensure_scenes(_doc(), root=state)
    doc = _doc()
    doc["scenes"][0]["shot"]["headline"] = "Changed"
    _layers, rendered = scene_render.ensure_scenes(doc, root=state)
    assert rendered == 1 and renders[-1]["shot"]["headline"] == "Changed"


def test_a_scene_that_only_slides_on_the_timeline_keeps_its_render(state: Path, renders: list[dict]) -> None:
    before, _n = scene_render.ensure_scenes(_doc(), root=state)
    # Cutting inside the Hook re-renders it (shorter) but only shifts the cameo 15 frames left.
    after, rendered = scene_render.ensure_scenes(_doc([(0, 20, 0), (35, 180, 20)]), root=state)
    assert rendered == 1 and renders[-1]["mode"] == "animation"
    assert after[1] == SceneLayer(before[1].path, 108, 57, True)


def test_the_key_covers_format_alpha_and_motion_core(state: Path, renders: list[dict], monkeypatch) -> None:
    scene_render.ensure_scenes(_doc(), root=state)
    _layers, vertical = scene_render.ensure_scenes(_doc(width=1080, height=1920), root=state)
    assert vertical == 2
    request = scene_render.render_request(scene_render.scene_specs(_doc(), WORDS)[0], width=1920, height=1080, fps=30)
    key = scene_render.cache_key(request, "v1")
    assert scene_render.cache_key({**request, "fps": 25}, "v1") != key
    assert scene_render.cache_key({**request, "alpha": True}, "v1") != key
    assert scene_render.cache_key(request, "v2") != key
    monkeypatch.setattr(scene_render, "motion_core_version", lambda: "edited")
    _layers, rebuilt = scene_render.ensure_scenes(_doc(), root=state)
    assert rebuilt == 2


def test_one_scene_can_be_rendered_on_its_own(state: Path, renders: list[dict]) -> None:
    layers, rendered = scene_render.ensure_scenes(_doc(), root=state, scene_id="s3")
    assert rendered == 1 and [x.alpha for x in layers] == [True]
    assert scene_render.ensure_scenes(_doc(), root=state, scene_id="s2") == ([], 0)


def test_motion_core_version_hashes_the_sources_and_the_renderer() -> None:
    assert scene_render.RENDERER.exists()
    assert len(scene_render.motion_core_version()) == 64


@pytest.mark.parametrize("change", ["scene_profile", "scene_order", "portable_order", "defaults"])
def test_scene_encoder_policy_changes_invalidate_cached_scenes(tmp_path, monkeypatch, change) -> None:
    from pipeline import encoder_ladder

    policy = tmp_path / "encoder-ladder.json"
    policy.write_bytes(encoder_ladder.DEFINITION_PATH.read_bytes())
    monkeypatch.setattr(encoder_ladder, "DEFINITION_PATH", policy)
    before = scene_render.motion_core_version()
    definition = json.loads(policy.read_text(encoding="utf-8"))
    if change == "scene_profile":
        definition["profiles"]["scene"]["h264_mf"][-1] = "99"
    elif change == "scene_order":
        definition["orders"]["withLibx264"]["scene"].reverse()
    elif change == "portable_order":
        definition["orders"]["withoutLibx264"].reverse()
    else:
        definition["defaults"]["crf"] = "19"
    policy.write_text(json.dumps(definition), encoding="utf-8")
    assert scene_render.motion_core_version() != before


@pytest.mark.parametrize("change", ["messages", "init_failures", "other_profiles", "other_orders", "whitespace"])
def test_unrelated_encoder_policy_changes_preserve_cached_scenes(tmp_path, monkeypatch, change) -> None:
    from pipeline import encoder_ladder

    policy = tmp_path / "encoder-ladder.json"
    policy.write_bytes(encoder_ladder.DEFINITION_PATH.read_bytes())
    monkeypatch.setattr(encoder_ladder, "DEFINITION_PATH", policy)
    before = scene_render.motion_core_version()
    definition = json.loads(policy.read_text(encoding="utf-8"))
    if change == "messages":
        for key in ("unavailableMessage", "probeTimeoutMessage", "probeFailureMessage"):
            definition[key] = "Updated guidance"
    elif change == "init_failures":
        definition["initFailures"]["h264_nvenc"].append("another initialization failure")
    elif change == "other_profiles":
        for role in ("export", "proxy", "script", "conform"):
            definition["profiles"][role]["h264_mf"][-1] = "90"
    elif change == "other_orders":
        for role in ("export", "proxy", "script", "conform"):
            definition["orders"]["withLibx264"][role] = ["h264_mf"]
    policy.write_text(json.dumps(definition, indent=4) + "\n\n", encoding="utf-8")
    assert scene_render.motion_core_version() == before



# ── cache eviction ───────────────────────────────────────────────────────────────────────────

def _aged(path: Path, mtime: int) -> Path:
    os.utime(path, (mtime, mtime))
    return path


def test_eviction_trims_least_recently_used_renders_to_the_cap(tmp_path: Path) -> None:
    cache = tmp_path / "scenes"
    cache.mkdir()
    for name, mtime in (("a.mp4", 1), ("b.mov", 2), ("c.mp4", 3), ("d.mp4", 4)):
        (cache / name).write_bytes(b"x" * 100)
        _aged(cache / name, mtime)
    removed = scene_render.evict_scenes(cache, keep=[cache / "a.mp4"], cap=250)
    assert [p.name for p in removed] == ["b.mov", "c.mp4"], "oldest first, skipping the one in use"
    assert sorted(p.name for p in cache.iterdir()) == ["a.mp4", "d.mp4"]
    assert scene_render.evict_scenes(cache, keep=[], cap=250) == []
    assert scene_render.evict_scenes(tmp_path / "absent", keep=[], cap=0) == []


def test_a_cache_hit_counts_as_a_use(state: Path, renders: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SCENE_CACHE_MAX_BYTES", 10)  # two 5-byte fake renders
    anim, cameo = (Path(x.path) for x in scene_render.ensure_scenes(_doc(), root=state)[0])
    _aged(anim, 2_000)
    _aged(cameo, 1_000)
    assert scene_render.ensure_scenes(_doc(), root=state, scene_id="s3")[1] == 0
    assert cameo.stat().st_mtime > anim.stat().st_mtime, "the hit was touched"
    doc = _doc()
    doc["scenes"][0]["shot"]["headline"] = "Changed"
    [fresh], rendered = scene_render.ensure_scenes(doc, root=state, scene_id="s1")
    assert rendered == 1
    assert not anim.exists() and cameo.exists() and Path(fresh.path).exists()


def test_the_cache_never_evicts_what_the_request_uses(state: Path, renders: list[dict],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SCENE_CACHE_MAX_BYTES", 0)
    cache = state / "outputs" / "scenes"
    cache.mkdir(parents=True)
    other = cache / "other.mp4"
    other.write_bytes(b"old")
    layers, rendered = scene_render.ensure_scenes(_doc(), root=state)
    assert rendered == 2 and all(Path(x.path).exists() for x in layers)
    assert not other.exists()
    again, rendered_again = scene_render.ensure_scenes(_doc(), root=state)
    assert rendered_again == 0 and again == layers and all(Path(x.path).exists() for x in layers)


@pytest.mark.skipif(not (shutil.which("node") and shutil.which("ffmpeg")), reason="needs Node 24+ and ffmpeg")
def test_the_node_renderer_writes_the_cached_scene_and_logs_ffmpeg(state: Path) -> None:
    doc = _doc([(0, 180, 0)], width=320, height=180)
    doc["scenes"][2]["shot"] = None
    layers, rendered = scene_render.ensure_scenes(doc, root=state)
    assert rendered == 1
    out = Path(layers[0].path)
    assert out.exists() and out.stat().st_size > 0
    assert not list(out.parent.glob("*.partial.*"))
    logged = (state / "run.log").read_text(encoding="utf-8")
    from pipeline.runtime_tools import tool_path

    assert logged.startswith(tool_path("ffmpeg") + " ") and str(out.with_name(out.stem + ".partial.mp4")) in logged
