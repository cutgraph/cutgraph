"""routes: create a video from a script, upload and apply its narration.

Real ffmpeg (tiny encodes); the WhisperX job is replaced by a stand-in that writes the take's
transcript sidecar exactly where the transcription job would.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Awaitable, Sequence
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from pipeline import contracts
from pipeline import jobs as jobs_mod
from pipeline import project as project_mod
from pipeline import script_video as sv
from pipeline import transcribe as transcribe_stage
from scripts import studio_app
from studio import compute, helpers
from studio import state as studio_state

T = TypeVar("T")

SCRIPT = """## Hook: A quick supper
Have you ever cooked pasta with fresh garlic and a little lemon?

## Prepare the ingredients
Boil the water while someone chops garlic beside the kitchen sink.

## Wrap: Serve the pasta
Fresh lemon adds brightness. That is a simple supper to share."""


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


def _call(method: str, url: str, **kw) -> httpx.Response:
    async def go() -> httpx.Response:
        async with _client() as client:
            return await client.request(method, url, **kw)
    return _run(go())


@pytest.fixture()
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data" / "source"
    data.mkdir(parents=True)
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "outputs" / "projects")
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "outputs" / "transcripts")
    monkeypatch.setattr(config, "PROXIES_DIR", tmp_path / "outputs" / "proxies")
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "outputs" / "jobs")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(studio_state, "_PROXY_WAITERS", {})
    monkeypatch.setattr(studio_state, "_JOBS", {})
    monkeypatch.setattr(compute, "_spawn_proxy", lambda *a, **k: None)
    return tmp_path


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, capture_output=True)


def _create(title: str = "A simple pasta supper", script: str = SCRIPT, **extra) -> httpx.Response:
    return _call("POST", "/api/script-video", json={"title": title, "script": script, **extra})


def _fake_transcription(monkeypatch: pytest.MonkeyPatch, words: Sequence[str], *, speed: float = 1.25) -> list[str]:
    """Stand in for the WhisperX job: write `words`, read at `speed` x the estimate, as the take's sidecar."""
    enqueued: list[str] = []

    def enqueue(take: str, _preference: str = "auto", *, project: str | None = None) -> dict:
        enqueued.append(take)
        t, tokens = 0.8, []
        for w in words:
            d = sv.word_dur(w) * speed
            tokens.append(contracts.WordToken(word=w, start=round(t, 3), end=round(t + d, 3), score=0.8))
            t += d + 0.05
        result = contracts.TranscriptResult("en", round(t, 3), [contracts.TranscriptSegment(0.8, round(t, 3), " ".join(words), tokens)])
        transcribe_stage._write_outputs(result, take)  # noqa: SLF001 - where the job writes it
        return {"status": "queued", "jobId": "job-1"}

    monkeypatch.setattr(compute, "_enqueue_transcribe", enqueue)
    return enqueued


def test_create_writes_an_animatic_every_region_can_read(state: Path) -> None:
    created = _create()
    assert created.status_code == 201, created.text
    video = created.json()["video"]
    assert video == "A_simple_pasta_supper"
    assert created.json()["project"]["title"] == "A simple pasta supper"

    doc = json.loads((config.PROJECTS_DIR / f"{video}.json").read_text(encoding="utf-8"))
    proj = project_mod.from_dict(doc)
    project_mod.validate(proj)
    assert proj.title == "A simple pasta supper"
    assert proj.narration == "estimated"
    assert [(s.role, s.title, s.startWord, s.shot) for s in proj.scenes] == [
        ("Hook", "A quick supper", 0, {"mode": "animation"}),
        ("Beat", "Prepare the ingredients", 12, {"mode": "animation"}),
        ("Wrap", "Serve the pasta", 23, {"mode": "animation"}),
    ]
    clip = proj.tracks[0].clips[0]
    assert clip.media.source == f"data/source/{video}_script.mp4"
    source = config.DATA_DIR / f"{video}_script.mp4"
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(source)],
                           check=True, capture_output=True, text=True)
    assert int(probe.stdout.strip()) == clip.srcOutF

    words = _call("GET", f"/api/transcript/{video}").json()["words"]
    assert [w["w"] for w in words] == [w for s in sv.parse_script(SCRIPT) for w in s.words]
    assert words[0]["start"] == pytest.approx(sv.LEAD)
    assert clip.srcOutF == pytest.approx(sv.estimate(sv.parse_script(SCRIPT))[1] * 30, abs=1)
    assert _call("GET", f"/api/project/{video}/transcription").json()["state"] == "ready"
    assert _call("GET", f"/api/script-video/{video}/narration").json() == {"take": None, "state": "none"}
    assert "ffmpeg" in (state / "run.log").read_text(encoding="utf-8")

    again = _create()
    assert again.status_code == 201 and again.json()["video"] == "A_simple_pasta_supper_2"


def test_create_assigns_the_workspace_and_rejects_what_it_cannot_build(state: Path) -> None:
    ws = _call("POST", "/api/workspaces", json={"name": "Explainers"}).json()["id"]
    created = _create(workspaceId=ws)
    assert created.json()["project"]["workspaceId"] == ws
    assert _create(workspaceId="nope-123456").status_code == 404
    assert _create(script="## Only a heading\n\n---").status_code == 400
    assert _create(title="  ").status_code == 400


# ── "From an idea" → "✦ Draft it": the co-pilot draft route, offline-safe and never a bug when
# the model can't answer. `_draft_by_copilot` is the only SDK-touching call, so it's what every test
# here replaces — the route itself, and its use of `agent_host.draft_script`, stay SDK-free.

def _no_copilot(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import agent_host

    async def unavailable(idea: str, format_hint: str | None) -> dict:
        raise RuntimeError("no SDK in tests")

    monkeypatch.setattr(agent_host, "_draft_by_copilot", unavailable)


def test_the_draft_route_shape(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_copilot(monkeypatch)
    offline = _call("POST", "/api/script/draft", json={"idea": "how to cook pasta"})
    assert offline.status_code == 503 and offline.json()["error"]
    assert _call("POST", "/api/script/draft", json={"idea": "   "}).status_code == 400
    assert _call("POST", "/api/script/draft", content=b"{nope", headers={"content-type": "application/json"}).status_code == 400


def test_the_draft_route_returns_a_script_the_create_route_accepts(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import agent_host

    async def fake_copilot(idea: str, format_hint: str | None) -> dict:
        return {
            "title": "A Simple Pasta Supper",
            "scenes": [
                {"role": "Hook", "title": "A quick supper",
                 "body": "Have you ever cooked pasta with fresh garlic and a little lemon?"},
                {"role": "Wrap", "title": "Serve the pasta",
                 "body": "Fresh lemon adds brightness. That is a simple supper to share."},
            ],
        }

    monkeypatch.setattr(agent_host, "_draft_by_copilot", fake_copilot)
    drafted = _call("POST", "/api/script/draft", json={"idea": "how to cook pasta"})
    assert drafted.status_code == 200
    body = drafted.json()
    assert body["source"] == "copilot" and body["title"] == "A Simple Pasta Supper"
    assert [s.role for s in sv.parse_script(body["script"])] == ["Hook", "Wrap"]

    created = _create(title=body["title"], script=body["script"])
    assert created.status_code == 201, created.text


def test_narration_import_aligns_the_take_and_keeps_the_word_space(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _create().json()["video"]
    script_words = [w for s in sv.parse_script(SCRIPT) for w in s.words]
    # The reading skips "a great", says "grief" for "idea" and adds an "um".
    heard = [w for i, w in enumerate(script_words) if i not in (2, 3)]
    heard[2] = "grief"
    heard.insert(10, "um")
    enqueued = _fake_transcription(monkeypatch, heard)
    take_audio = state / "reading.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=14", str(take_audio))

    upload = _call("PUT", f"/api/script-video/{video}/narration/reading.wav", content=take_audio.read_bytes())
    assert upload.status_code == 201, upload.text
    take = upload.json()["take"]
    assert take == f"{video}_take1" and enqueued == [take]
    assert (config.DATA_DIR / f"{take}.mp4").exists()  # audio became a playable MP4 over black
    assert not list(config.DATA_DIR.glob("*.part"))
    status = _call("GET", f"/api/script-video/{video}/narration").json()
    assert status == {"take": take, "state": "ready"}

    before = _call("GET", f"/api/project/{video}").json()
    stale = _call("POST", f"/api/script-video/{video}/narration/{take}/apply", json={"baseRevision": 0})
    assert stale.status_code == 409
    applied = _call("POST", f"/api/script-video/{video}/narration/{take}/apply", json={"baseRevision": before["revision"]})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["alignment"] == {"matched": len(script_words) - 3, "total": len(script_words)}
    assert body["revision"] == before["revision"] + 1
    proj = body["project"]
    assert proj["narration"] == "recorded"
    assert proj["scenes"] == before["project"]["scenes"]
    [clip] = proj["tracks"][0]["clips"]
    assert clip["media"]["source"] == f"data/source/{take}.mp4"
    assert clip["srcOutF"] == helpers._source_duration_frames(config.DATA_DIR / f"{take}.mp4")

    words = _call("GET", f"/api/transcript/{video}").json()["words"]
    assert [w["w"] for w in words] == script_words
    assert words[0]["start"] == pytest.approx(0.8)
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)
    raw = json.loads((config.TRANSCRIPTS_DIR / f"{take}.whisperx.json").read_text(encoding="utf-8"))
    assert [w["word"] for seg in raw["segments"] for w in seg["words"]] == heard
    assert (config.TRANSCRIPTS_DIR / f"{video}_script.words.json").exists()  # undo can go back to the estimate

    assert _call("GET", f"/api/script-video/{video}/narration").json() == {"take": take, "state": "applied"}
    again = _call("POST", f"/api/script-video/{video}/narration/{take}/apply", json={"baseRevision": body["revision"]})
    assert again.status_code == 200 and again.json()["alignment"] is None


def test_a_video_take_keeps_its_file_and_a_silent_one_is_refused(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _create().json()["video"]
    _fake_transcription(monkeypatch, ["unrelated"])
    clip = state / "take.mov"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=1", "-f", "lavfi", "-i", "sine=duration=1",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip))
    upload = _call("PUT", f"/api/script-video/{video}/narration/take.mov", content=clip.read_bytes())
    assert upload.status_code == 201, upload.text
    stored = config.DATA_DIR / f"{upload.json()['take']}.mov"
    assert stored.read_bytes() == clip.read_bytes()

    silent = state / "silent.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=1", "-c:v", "libx264", str(silent))
    refused = _call("PUT", f"/api/script-video/{video}/narration/silent.mp4", content=silent.read_bytes())
    assert refused.status_code == 400 and "no sound" in refused.json()["error"]
    assert _call("PUT", f"/api/script-video/{video}/narration/notes.txt", content=b"hi").status_code == 400


def test_the_wrong_take_is_refused_and_changes_nothing(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _create().json()["video"]
    _fake_transcription(monkeypatch, "completely unrelated audio about cooking pasta tonight".split())
    take_audio = state / "pasta.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=duration=4", str(take_audio))
    take = _call("PUT", f"/api/script-video/{video}/narration/pasta.wav", content=take_audio.read_bytes()).json()["take"]
    before = _call("GET", f"/api/project/{video}").json()
    refused = _call("POST", f"/api/script-video/{video}/narration/{take}/apply", json={"baseRevision": before["revision"]})
    assert refused.status_code == 422 and "script words were heard" in refused.json()["error"]
    assert _call("GET", f"/api/project/{video}").json() == before
    assert (config.TRANSCRIPTS_DIR / f"{take}.words.json").exists()


def test_narration_routes_are_only_for_script_videos(state: Path) -> None:
    (config.PROJECTS_DIR).mkdir(parents=True)
    footage = project_mod.Project(name="talk", tracks=[])
    project_mod.write_project(config.PROJECTS_DIR / "talk.json", footage)
    assert _call("GET", "/api/script-video/talk/narration").status_code == 404
    assert _call("PUT", "/api/script-video/talk/narration/a.wav", content=b"x").status_code == 404
    assert _call("POST", "/api/script-video/talk/narration/talk_take1/apply", json={"baseRevision": 1}).status_code == 404
    assert jobs_mod.all_persisted() == {}
