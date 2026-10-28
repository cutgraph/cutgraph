"""Generate synthetic media and a prepared public-domain nursery verse in a fresh state home."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
# Jane Taylor, "The Star", Rhymes for the Nursery (1806), first stanza; public domain.
# Source: https://en.wikisource.org/wiki/Rhymes_for_the_Nursery/The_Star
VERSE = (
    "Twinkle, twinkle, little star, How I wonder what you are! "
    "Up above the world so high, Like a diamond in the sky."
)
DURATION = 4


def build(state: Path) -> None:
    state = state.resolve()
    state.mkdir(parents=True, exist_ok=False)
    os.environ["CUTGRAPH_STATE_HOME"] = str(state)
    sys.path.insert(0, str(ROOT))

    # Import only after selecting the isolated state home: config captures it at import time.
    import config
    from pipeline import contracts
    from pipeline import library, project, script_video

    if config.STATE_HOME != state:
        raise RuntimeError("Fixture generation requires a fresh process with its own state home")
    config.DATA_DIR.mkdir(parents=True)
    config.TRANSCRIPTS_DIR.mkdir(parents=True)
    workspace = library.create_workspace("Synthetic workspace")
    source = config.DATA_DIR / "synthetic-footage.mp4"
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(DURATION), "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(source),
    ]
    # A preview far wider than any viewport, served to the stock lightbox as a data URL.
    stock_preview = state / "synthetic-stock-preview.mp4"
    preview_command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=2560x320:rate=10", "-t", "1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(stock_preview),
    ]
    config.RUN_LOG.write_text(
        "".join(subprocess.list2cmdline(c) + "\n" for c in (command, preview_command)), encoding="utf-8",
    )
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    subprocess.run(preview_command, check=True, capture_output=True, timeout=60)
    tokens = VERSE.split()
    words = [
        contracts.WordToken(word, round(0.5 + i * (DURATION - 1) / len(tokens), 3),
                            round(0.5 + (i + 1) * (DURATION - 1) / len(tokens), 3), 1.0)
        for i, word in enumerate(tokens)
    ]
    transcript = contracts.TranscriptResult("en", DURATION, [
        contracts.TranscriptSegment(words[0].start, words[-1].end, VERSE, words),
    ])
    transcript.validate()
    (config.TRANSCRIPTS_DIR / f"{source.stem}.words.json").write_text(
        json.dumps(asdict(transcript), indent=2) + "\n", encoding="utf-8",
    )
    footage = project.Project(
        name="synthetic-footage", title="Synthetic footage", width=1280, height=720,
        workspaceId=workspace["id"], captions={"enabled": True},
        tracks=[project.Track(id="speech", kind="speech", clips=[project.Clip(
            id="source", media=project.Media(source=source.relative_to(state).as_posix()),
            srcInF=0, srcOutF=DURATION * config.DEFAULT_FPS, tlStartF=0,
        )])],
        scenes=[project.Scene(id="scene-1", title="A nursery verse", role="Hook", startWord=0)],
    )
    scenes = script_video.parse_script(VERSE)
    script_transcript = script_video.script_transcript(scenes)
    script_transcript.validate()
    frames = round(script_transcript.duration * config.DEFAULT_FPS)
    silent = config.DATA_DIR / "synthetic-script.mp4"
    script = script_video.build_project(
        "synthetic-script", scenes, silent.relative_to(state).as_posix(), frames,
        workspace["id"], title="Synthetic script",
    )
    script.width, script.height = 1280, 720
    script_video.make_silent_source(silent, frames, script.fps, script.width, script.height)
    (config.TRANSCRIPTS_DIR / f"{silent.stem}.words.json").write_text(
        json.dumps(asdict(script_transcript), indent=2) + "\n", encoding="utf-8",
    )
    project_schema = json.loads((ROOT / "schema/project.schema.json").read_text(encoding="utf-8"))
    for doc in (footage, script):
        raw = project.to_dict(doc)
        jsonschema.validate(instance=raw, schema=project_schema)
        project.validate(project.from_dict(raw))
        project.write_project(config.PROJECTS_DIR / f"{doc.name}.json", doc)
    (state / "fixture.json").write_text(json.dumps({
        "workspace": workspace["id"], "footage": footage.name, "script": script.name,
        "stockPreview": stock_preview.name,
    }) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
