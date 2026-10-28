// The store's playheadF and the player's clock share feedback-guarded sync.
//   IN  store -> FramePlayer: seek external playhead changes, but consume FramePlayer-originated store echoes.
//   OUT FramePlayer -> store: sample normal playback at 10 Hz, publish seeks/pauses exactly, and feed the
//                        existing previewSec karaoke contract (timeline frame -> source sec).
import { useEffect, useRef, useState, type RefObject } from "react";
import type { FrameListener, FramePlayerHandle } from "../player";
import { useProjectStore, setPlayhead, setPreviewSec, frameToSec } from "../store/useProjectStore";
import { timelineToSrc } from "../transcript/cutmap";
import type { Project } from "../types/project";

const PLAYHEAD_PUBLISH_HZ = 10;

export interface PlayerSync {
  playerRef: RefObject<FramePlayerHandle | null>;
  playing: boolean;
  soundBlocked: boolean;
  enableSound: () => void;
  togglePlay: () => void;
}

export function usePlayerSync(project: Project | null): PlayerSync {
  const playheadF = useProjectStore((s) => s.playheadF);
  const playerRef = useRef<FramePlayerHandle>(null);
  const playerPublishedFrameRef = useRef<number | null>(null);
  const lastPublishedPlaybackFrameRef = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [soundBlocked, setSoundBlocked] = useState(false);

  useEffect(() => {
    const p = playerRef.current;
    setSoundBlocked(p?.isSoundBlocked() ?? false);
    if (!p) return;
    const onSoundBlocked: FrameListener<"sound"> = (event) => setSoundBlocked(event.detail.soundBlocked);
    p.listen("sound", onSoundBlocked);
    return () => p.unlisten("sound", onSoundBlocked);
  }, [project]);

  // IN: store playhead -> FramePlayer. A FramePlayer frame event updates the store asynchronously relative
  // to playback, so readFrame() alone is not a sufficient feedback guard: by the time this
  // effect runs the FramePlayer may already be on the next frame and would be sought backwards. Remember
  // the frame most recently published by the FramePlayer and consume that store echo without seeking.
  useEffect(() => {
    const playerFrame = playerPublishedFrameRef.current;
    playerPublishedFrameRef.current = null;
    if (playerFrame === playheadF) return;
    const p = playerRef.current;
    if (p && p.readFrame() !== playheadF) p.seek(playheadF);
  }, [playheadF]);

  // OUT: FramePlayer frame -> store playhead + karaoke previewSec. Re-registered when `project` changes so
  // the timeline->source mapping (timelineToSrc) always sees the current clip list.
  useEffect(() => {
    const p = playerRef.current;
    if (!p) return;
    const forward = (frame: number, force = false): void => {
      const proj = useProjectStore.getState().project;
      if (!force && p.isRunning() && proj) {
        const intervalF = Math.max(1, Math.floor(proj.fps / PLAYHEAD_PUBLISH_HZ));
        const last = lastPublishedPlaybackFrameRef.current;
        if (last !== null && Math.abs(frame - last) < intervalF) return;
      }
      if (frame !== useProjectStore.getState().playheadF) {
        lastPublishedPlaybackFrameRef.current = frame;
        playerPublishedFrameRef.current = frame;
        setPlayhead(frame);
      }
      if (!proj) return;
      const s = timelineToSrc(proj, frame);
      if (s) setPreviewSec(frameToSec(s.srcF, proj.fps));
    };
    const onFrame: FrameListener<"frame"> = (e) => forward(e.detail.frame);
    const onSeek: FrameListener<"seek"> = (e) => forward(e.detail.frame, true);
    p.listen("frame", onFrame);
    p.listen("seek", onSeek);
    return () => {
      p.unlisten("frame", onFrame);
      p.unlisten("seek", onSeek);
    };
  }, [project]);

  // Keep the transport play/pause glyph in sync with the FramePlayer's own play state, which the store's
  // playhead seeks and any other control path can flip.
  useEffect(() => {
    const p = playerRef.current;
    if (!p) return;
    const onPlay: FrameListener<"play"> = () => setPlaying(true);
    const onPause: FrameListener<"pause"> = () => {
      setPlaying(false);
      const frame = p.readFrame();
      if (frame !== useProjectStore.getState().playheadF) {
        lastPublishedPlaybackFrameRef.current = frame;
        playerPublishedFrameRef.current = frame;
        setPlayhead(frame);
      }
    };
    p.listen("play", onPlay);
    p.listen("pause", onPause);
    return () => {
      p.unlisten("play", onPlay);
      p.unlisten("pause", onPause);
    };
  }, [project]);

  function togglePlay(): void {
    const p = playerRef.current;
    if (!p) return;
    if (p.isRunning()) p.pause();
    else p.play();
  }

  // Must run unconditionally (never after a caller's early return): a caller whose project loads
  // asynchronously would otherwise change hook order the moment it starts rendering its FramePlayer, which
  // makes React unmount the whole subtree. StoryView's Space handler dispatches this event too.
  useEffect(() => {
    window.addEventListener("cutgraph:toggle-preview", togglePlay);
    return () => window.removeEventListener("cutgraph:toggle-preview", togglePlay);
  });

  return { playerRef, playing, soundBlocked, enableSound: () => playerRef.current?.enableSound(), togglePlay };
}
