"""Script-origin videos: a written script becomes an ordinary project.

The script's words ARE the video's transcript: the same `<stem>.words.json/.txt/.srt` sidecars a
transcription writes, timed by motion-core's `wordDur` estimate, over a generated black and silent
source of that length. Every Story region (Script, Board, captions, compile, export) then works
unchanged. Importing a narration take transcribes it for real and aligns the script words to what
was said: the words sidecar keeps the SCRIPT word list, the flat wordIdx space that scene startWords,
shot cueWords and cue anchors point into, and only the timings change.
"""
from __future__ import annotations

import bisect
import difflib
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import config
from pipeline import contracts
from pipeline import project as project_mod
from pipeline.encoder_ladder import encoder_args, run_h264, uses_portable_profiles
from pipeline.timebase import sec_to_frame
from pipeline.runtime_tools import tool_path

# motion-core/src/timing.ts, ported. tests/fixtures/script_video_golden.json pins both to the same numbers.
LEAD = 0.45
TAIL = 0.55
# Below this share of script words heard verbatim, a take is more likely the wrong recording than a
# loose reading, and its timings would scramble the story rather than fix it.
MIN_MATCH = 0.5
TITLE_WORDS = 5

_LINE = re.compile(r"\r\n|\r|\n")
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*)$")
_ROLE_PREFIX = re.compile(r"^([A-Za-z]+)\s*:\s*(.*)$")
# Markdown emphasis/code marks, plus the ASS metacharacters a caption word is burned in with verbatim.
_STRIP = re.compile(r"[*_`{}\\]")
_SENTENCE_END = re.compile(r"[.?!][\"'”’)\]]*$")
_ROLES = {role.lower(): role for role in project_mod.SCENE_ROLES}


class NarrationError(ValueError):
    """A take that can't narrate this script: too few of its words heard, or nothing left to play."""


@dataclass
class ScriptScene:
    title: str
    role: str
    words: list[str]


def bare(w: str) -> str:
    return re.sub(r"[^a-z']", "", w.lower())


def word_dur(w: str) -> float:
    """Estimated spoken duration of one word, used until real narration timings exist."""
    n = bare(w)
    if n in ("um", "uh"):
        return 0.42
    d = 0.17 + 0.047 * len(n)
    if re.search(r"[,;:]$", w):
        d += 0.22
    if re.search(r"[.?!]$", w):
        d += 0.38
    return d


# ── script -> scenes ─────────────────────────────────────────────────────────────

def _tokens(line: str) -> list[str]:
    return [w for w in (_STRIP.sub("", raw) for raw in line.split()) if any(ch.isalnum() for ch in w)]


def _clean(text: str) -> str:
    return " ".join(_STRIP.sub("", text).split())


def _title_from(words: list[str]) -> str:
    head = " ".join(words[:TITLE_WORDS]).rstrip(",;:.!?")
    return head + "…" if len(words) > TITLE_WORDS else head


def parse_script(text: str) -> list[ScriptScene]:
    """`## Title` headings start scenes (paragraphs do when there are none). A heading may name its role
    (`## Problem: An empty pantry`); otherwise the first scene is the Hook, the last the Wrap, the rest
    Beats. Text before the first heading is a scene of its own; a heading with no words is dropped."""
    lines = _LINE.split(text.replace("\ufeff", ""))
    has_heading = any(_HEADING.match(line) for line in lines)
    sections: list[tuple[str | None, list[str]]] = []
    current: tuple[str | None, list[str]] | None = None
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            current = (heading.group(1), [])
            sections.append(current)
        elif not line.strip():
            if not has_heading:
                current = None
        else:
            if current is None:
                current = (None, [])
                sections.append(current)
            current[1].extend(_tokens(line))
    kept = [(heading, words) for heading, words in sections if words]
    scenes: list[ScriptScene] = []
    for i, (heading, words) in enumerate(kept):
        title, role = _clean(heading or ""), None
        prefix = _ROLE_PREFIX.match(title)
        if prefix and prefix.group(1).lower() in _ROLES:
            role, title = _ROLES[prefix.group(1).lower()], prefix.group(2).strip()
        if role is None:
            role = "Hook" if i == 0 else "Wrap" if i == len(kept) - 1 else "Beat"
        scenes.append(ScriptScene((title or _title_from(words))[: project_mod.SCENE_TITLE_MAX], role, words))
    return scenes


def estimate(scenes: list[ScriptScene]) -> tuple[list[list[tuple[float, float]]], float]:
    """motion-core `timeline` for an uncut script: each word runs `word_dur`, with LEAD before and TAIL
    after every scene. Returns per-scene (start, end) word spans and the total length in seconds."""
    t = 0.0
    spans: list[list[tuple[float, float]]] = []
    for scene in scenes:
        t += LEAD
        scene_spans: list[tuple[float, float]] = []
        for w in scene.words:
            end = t + word_dur(w)
            scene_spans.append((t, end))
            t = end
        t += TAIL
        spans.append(scene_spans)
    return spans, t


def _segment(words: list[contracts.WordToken]) -> contracts.TranscriptSegment:
    return contracts.TranscriptSegment(
        start=words[0].start, end=words[-1].end, text=" ".join(w.word for w in words), words=words,
    )


def script_transcript(scenes: list[ScriptScene]) -> contracts.TranscriptResult:
    """The script as a transcript with estimated timings: one segment per sentence, never across scenes."""
    spans, duration = estimate(scenes)
    segments: list[contracts.TranscriptSegment] = []
    for scene, scene_spans in zip(scenes, spans):
        sentence: list[contracts.WordToken] = []
        for word, (start, end) in zip(scene.words, scene_spans):
            sentence.append(contracts.WordToken(word=word, start=round(start, 3), end=round(end, 3), score=0.0))
            if _SENTENCE_END.search(word):
                segments.append(_segment(sentence))
                sentence = []
        if sentence:
            segments.append(_segment(sentence))
    return contracts.TranscriptResult(
        language=config.WHISPER_LANGUAGE, duration=round(duration, 3), segments=segments,
    )


def build_project(
    name: str, scenes: list[ScriptScene], source: str, duration_f: int, workspace_id: str | None,
    title: str | None = None,
) -> project_mod.Project:
    """An animatic: one speech clip over the silent source, every scene in Animation mode (its look comes
    from its role, its headline from its title) and captions on, since there is no voice yet."""
    starts, n = [], 0
    for scene in scenes:
        starts.append(n)
        n += len(scene.words)
    return project_mod.Project(
        name=name,
        title=title,
        fps=config.DEFAULT_FPS,
        revision=1,
        captions={"enabled": True},
        workspaceId=workspace_id,
        narration="estimated",
        tracks=[project_mod.Track(id="speech", kind="speech", clips=[project_mod.Clip(
            id="source", media=project_mod.Media(source=source), srcInF=0, srcOutF=duration_f, tlStartF=0,
        )])],
        scenes=[
            project_mod.Scene(id=f"scene-{i + 1}", title=scene.title, role=scene.role, startWord=start,
                              shot={"mode": "animation"})
            for i, (scene, start) in enumerate(zip(scenes, starts))
        ],
    )


# ── narration take -> aligned script words ─────────────────────────────────────

def load_transcript(path: Path) -> contracts.TranscriptResult:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return contracts.TranscriptResult(
        language=str(raw.get("language") or config.WHISPER_LANGUAGE),
        duration=float(raw.get("duration") or 0.0),
        segments=[
            contracts.TranscriptSegment(
                start=float(seg["start"]), end=float(seg["end"]), text=str(seg.get("text", "")),
                words=[
                    contracts.WordToken(word=str(w["word"]).strip(), start=float(w["start"]), end=float(w["end"]),
                                     score=float(w.get("score", 0.0)))
                    for w in seg.get("words", [])
                ],
            )
            for seg in raw.get("segments", [])
        ],
    )


def _norm(word: str) -> str:
    return "".join(ch for ch in word.casefold() if ch.isalnum())


@dataclass
class Alignment:
    words: list[contracts.WordToken]  # one per script word: the script's text with the take's timing
    matched: int                   # script words heard verbatim


def align(script: Sequence[str], take: Sequence[contracts.WordToken], duration: float) -> Alignment:
    """Time every script word from a transcribed take. A sequence alignment of the normalized words gives
    each word heard verbatim, or swapped one-for-one (a misheard or reworded word), the take's timing;
    every other run of script words is spread between its timed neighbours by estimated duration. Take
    words the script doesn't have (ad-libs, repeats) are skipped."""
    heard: list[contracts.WordToken | None] = [None] * len(script)
    matched = 0
    matcher = difflib.SequenceMatcher(None, [_norm(w) for w in script], [_norm(t.word) for t in take],
                                      autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            heard[i1:i2] = take[j1:j2]
            if op == "equal":
                matched += i2 - i1
    out: list[contracts.WordToken] = []
    i = 0
    while i < len(script):
        token = heard[i]
        if token is not None:
            out.append(contracts.WordToken(word=script[i], start=token.start, end=token.end, score=token.score))
            i += 1
            continue
        j = i
        while j < len(script) and heard[j] is None:
            j += 1
        after = heard[j] if j < len(script) else None
        lo = out[-1].end if out else 0.0
        hi = max(after.start if after else duration, lo)
        weights = [word_dur(w) for w in script[i:j]]
        total = sum(weights)
        at = lo
        for w, weight in zip(script[i:j], weights):
            end = at + (hi - lo) * weight / total
            out.append(contracts.WordToken(word=w, start=round(at, 3), end=round(end, 3), score=0.0))
            at = end
        i = j
    return Alignment(out, matched)


def _warp(old: list[contracts.WordToken], new: list[contracts.WordToken], old_end: float,
          new_end: float) -> Callable[[float], float]:
    """Piecewise-linear map from the estimated clock to the take's, through every word's start and end:
    a cut or reorder boundary on a word lands on that same word in the take."""
    xs, ys = [0.0], [0.0]
    for a, b in zip(old, new):
        for x, y in ((a.start, b.start), (a.end, b.end)):
            if x >= xs[-1]:
                xs.append(x)
                ys.append(max(y, ys[-1]))
    xs.append(max(old_end, xs[-1]))
    ys.append(max(new_end, ys[-1]))

    def warp(x: float) -> float:
        if x >= xs[-1]:
            return ys[-1]
        k = max(bisect.bisect_right(xs, x), 1)
        x0, x1, y0, y1 = xs[k - 1], xs[k], ys[k - 1], ys[k]
        return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    return warp


def _span(element: project_mod.Clip | project_mod.TextItem | project_mod.GraphicItem) -> int:
    return element.srcOutF - element.srcInF if isinstance(element, project_mod.Clip) else element.durF


def _overlaps(elements: list) -> bool:
    ordered = sorted(elements, key=lambda e: e.tlStartF)
    return any(cur.tlStartF < prev.tlStartF + _span(prev) for prev, cur in zip(ordered, ordered[1:]))


def apply_narration(
    proj: project_mod.Project,
    script: contracts.TranscriptResult,
    take: contracts.TranscriptResult,
    take_source: str,
    take_frames: int,
    proxy: str | None,
) -> tuple[project_mod.Project, contracts.TranscriptResult, dict]:
    """The project and words sidecar once `take` narrates the script.

    The sidecar keeps the script's words and segments with the take's timings. Speech clips move onto
    the take through the estimated->take clock warp, so cuts and Board reorders survive; word-anchored
    cues follow their words (a track whose cues would then collide keeps its old positions). Raises
    NarrationError when too few script words were heard for the take to be this script's narration."""
    fps = proj.fps
    duration = take_frames / fps
    old_words = [w for seg in script.segments for w in seg.words]
    aligned = align([w.word for w in old_words], [w for seg in take.segments for w in seg.words], duration)
    if not old_words or aligned.matched < MIN_MATCH * len(old_words):
        raise NarrationError(
            f"Only {aligned.matched} of {len(old_words)} script words were heard in this take. "
            "Is it the right recording?"
        )
    words = iter(aligned.words)
    retimed = contracts.TranscriptResult(
        language=script.language, duration=round(duration, 3),
        segments=[_segment([next(words) for _ in seg.words]) for seg in script.segments],
    )

    out = project_mod.from_dict(project_mod.to_dict(proj))
    speech = next((t for t in out.tracks if t.kind == "speech" and t.clips), None)
    if speech is None:
        raise NarrationError("This video has no speech track to narrate.")
    old_source = speech.clips[0].media.source
    moving = sorted((c for c in speech.clips if c.media.source == old_source), key=lambda c: c.tlStartF)
    others = [c for c in speech.clips if c.media.source != old_source]
    # The silent source ends on a frame boundary, which may precede the estimated duration.
    old_end = max([sec_to_frame(script.duration, fps) / fps] + [c.srcOutF / fps for c in moving])
    warp = _warp(old_words, aligned.words, old_end, duration)

    def frame(old_f: int) -> int:
        return min(max(sec_to_frame(warp(old_f / fps), fps), 0), take_frames)

    kept: list[project_mod.Clip] = []
    old_prev_end = new_prev_end = moving[0].tlStartF
    for clip in moving:
        gap = clip.tlStartF - old_prev_end
        old_prev_end = clip.tlStartF + clip.srcOutF - clip.srcInF
        new_in, new_out = frame(clip.srcInF), frame(clip.srcOutF)
        if new_out <= new_in:
            continue  # it held only words the take skipped
        seams = []
        for seam in clip.seams or []:
            at = frame(clip.srcInF + seam["atF"]) - new_in
            if 0 <= at <= new_out - new_in:
                seams.append({"atF": at, "fadeMs": seam["fadeMs"]})
        clip.media = project_mod.Media(source=take_source, proxy=proxy)
        clip.srcInF, clip.srcOutF, clip.seams = new_in, new_out, seams or None
        clip.tlStartF = new_prev_end + gap
        new_prev_end = clip.tlStartF + new_out - new_in
        kept.append(clip)
    if not kept:
        raise NarrationError("The take doesn't cover any of the words kept in this video.")
    speech.clips = others + kept

    word_frames = [sec_to_frame(w.start, fps) for w in aligned.words]
    for track in out.tracks:
        if track is speech:
            continue
        elements = [*track.clips, *track.items]
        before = {id(e): e.tlStartF for e in elements}
        for element in elements:
            anchor = element.anchor
            if anchor is None or anchor.mode != "word" or not anchor.ref or anchor.ref.get("media") != old_source:
                continue
            anchor.ref = {**anchor.ref, "media": take_source}
            idx = anchor.ref.get("wordIdx")
            if not isinstance(idx, int) or not 0 <= idx < len(word_frames):
                continue
            wf = word_frames[idx]
            covering = next((c for c in kept if c.srcInF <= wf < c.srcOutF), None)
            if covering is not None:
                element.tlStartF = max(0, covering.tlStartF + wf - covering.srcInF + anchor.offsetF)
        if _overlaps(elements):
            for element in elements:
                element.tlStartF = before[id(element)]

    out.narration = "recorded"
    project_mod.validate(out)
    return out, retimed, {"matched": aligned.matched, "total": len(old_words)}


# ── media ────────────────────────────────────────────────────────────────────────

def _run_ffmpeg(inputs: list[str], audio: list[str], dest: Path) -> None:
    ffmpeg = tool_path("ffmpeg")
    portable = uses_portable_profiles(ffmpeg)
    run_h264(ffmpeg, "script", lambda encoder: [
        ffmpeg, *inputs, *encoder_args("script", encoder), "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-c:a", "aac", *(["-ar", "48000"] if portable else []), *audio, str(dest),
    ])


def make_silent_source(dest: Path, frames: int, fps: int, width: int, height: int) -> None:
    """A black, silent MP4 of exactly `frames` frames: the speech media an unrecorded script plays."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg([
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-frames:v", str(frames), "-t", f"{frames / fps:.6f}",
    ], [], dest)


def take_has_picture(path: Path) -> bool:
    """Whether an uploaded take has a real picture (cover art doesn't count). Raises ValueError when it has
    no sound to transcribe."""
    probe = subprocess.run(
        [tool_path("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    streams = json.loads(probe.stdout).get("streams") or []
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise NarrationError("This recording has no sound.")
    return any(s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
               for s in streams)


def make_take_source(upload: Path, dest: Path, fps: int, width: int, height: int) -> None:
    """A take with no picture (an audio file, a microphone recording) as an MP4 of its sound over black,
    so it plays, proxies, transcribes and compiles like any footage. Trimmed to the sound's own length
    when the container knows it: `-shortest` against an endless lavfi source overshoots by a varying
    amount, so it is only the fallback."""
    probe = subprocess.run(
        [tool_path("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(upload)],
        check=True, capture_output=True, text=True,
    )
    try:
        trim = ["-t", f"{float(probe.stdout.strip()):.3f}"]
    except ValueError:
        trim = ["-shortest"]
    _run_ffmpeg([
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}", "-i", str(upload),
        "-map", "0:v", "-map", "1:a:0", *trim,
    ], ["-b:a", "192k"], dest)
