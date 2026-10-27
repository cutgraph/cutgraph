import { useEffect, useMemo, type CSSProperties } from "react";
import { FramePlayer, type FrameListener } from "../../player";
import { FORMATS } from "motion-core";
import { applyLocal, setPlayhead, useProjectStore } from "../../store/useProjectStore";
import { persistCurrent } from "../../persist";
import { mmss, projectDurationF } from "../../timeline/geometry";
import { ProjectComposition } from "../../preview/ProjectComposition";
import { usePlayerSync } from "../../preview/usePlayerSync";
import { applyFormat, formatOf, type Format } from "./player/format";
import FormatChip from "./player/FormatChip";
import { setPlaying } from "./storyStore";
import { animatedSceneSpecs } from "./shot/specs";
import { useScreenAssets } from "./shot/screenLibrary";
import { useVideoCast } from "../library/castStore";
import { useStoryModel } from "./useStoryModel";
import "./player/player.css";

const PLAYER_STYLE = { width: "100%", height: "100%" } as const;

function toggleCaptions(): void {
  const current = useProjectStore.getState().project;
  if (!current) return;
  const enabled = current.captions?.enabled !== true;
  applyLocal(
    () => ({ project: { ...current, captions: { ...(current.captions ?? {}), enabled } } }),
    { origin: "human", label: enabled ? "Turn on captions" : "Turn off captions" },
  );
  void persistCurrent(current.name);
}

function pickFormat(next: Format): void {
  const current = useProjectStore.getState().project;
  if (!current || formatOf(current) === next) return;
  applyLocal(() => ({ project: applyFormat(current, next) }), { origin: "human", label: `Switch to ${next}` });
  void persistCurrent(current.name);
}

export default function PlayerPanel() {
  const project = useProjectStore((s) => s.project);
  const playheadF = useProjectStore((s) => s.playheadF);
  const { playerRef, playing, soundBlocked, enableSound, togglePlay } = usePlayerSync(project);
  const { words, spans, media } = useStoryModel();
  const { cast } = useVideoCast();
  const screens = useScreenAssets(project);
  const scenes = useMemo(
    () => (project ? animatedSceneSpecs(project, words, spans, media, cast, screens) : []),
    [project, words, spans, media, cast, screens],
  );
  const contentProps = useMemo(
    () => project ? ({
      project,
      captionsEnabled: project.captions?.enabled === true,
      captionWords: project.captions?.enabled === true && words.length ? words : undefined,
      captionMedia: media,
      scenes,
    }) : null,
    [project, words, media, scenes],
  );

  // Mirror play/pause/end into storyStore.playing so other regions (the Script lens' word
  // highlight) can read it directly instead of inferring playback from the playhead advancing.
  useEffect(() => {
    const p = playerRef.current;
    if (!p) return;
    const onPlay: FrameListener<"play"> = () => setPlaying(true);
    const onPause: FrameListener<"pause"> = () => setPlaying(false);
    const onEnded: FrameListener<"end"> = () => setPlaying(false);
    p.listen("play", onPlay);
    p.listen("pause", onPause);
    p.listen("end", onEnded);
    return () => {
      p.unlisten("play", onPlay);
      p.unlisten("pause", onPause);
      p.unlisten("end", onEnded);
    };
  }, [playerRef, project]);

  if (!project || !contentProps) {
    return (
      <div className="player" data-slot="player">
        <p className="muted">No video open</p>
      </div>
    );
  }

  const format = formatOf(project);
  const [fw, fh] = FORMATS[format];
  const durF = projectDurationF(project);
  const captionsEnabled = project.captions?.enabled === true;

  return (
    <div className="player" data-slot="player">
      <div className="frame" style={{ "--ar": `${fw}/${fh}` } as CSSProperties}>
        <FramePlayer
          content={ProjectComposition}
          contentProps={contentProps}
          frameCount={Math.max(1, durF)}
          fps={project.fps}
          width={project.width}
          height={project.height}
          ref={playerRef}
          keyboardPlayback={false}
          clickToggles={false}
          fullscreenOnDoubleClick={false}
          style={PLAYER_STYLE}
        />
      </div>
      <input
        className="scrub"
        type="range"
        aria-label="Seek"
        min={0}
        max={Math.max(1, durF)}
        step={1}
        value={Math.min(playheadF, durF)}
        onChange={(e) => setPlayhead(Number(e.target.value))}
      />
      <div className="transport">
        <button
          type="button"
          className="play"
          onClick={togglePlay}
          aria-label={playing ? "Pause" : "Play"}
          title={playing ? "Pause (Space)" : "Play (Space)"}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <span id="time" className="time">{mmss(playheadF, project.fps)} / {mmss(durF, project.fps)}</span>
        <span className="grow" />
        {soundBlocked && <button type="button" className="chip" onClick={enableSound}>Enable sound</button>}
        <button
          type="button"
          className={`chip${captionsEnabled ? " on" : ""}`}
          aria-pressed={captionsEnabled}
          title="Captions"
          onClick={toggleCaptions}
        >
          CC
        </button>
        <FormatChip format={format} onPick={pickFormat} />
      </div>
    </div>
  );
}
