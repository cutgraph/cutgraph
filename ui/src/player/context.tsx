import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { FrameClock } from "./clock";
import { Layer } from "./elements";

export const PlaybackContext = createContext<{
  clock: FrameClock;
  fps: number;
  seekVersion: number;
  playing: boolean;
  speed: number;
} | null>(null);
export const FrameContext = createContext(0);
export const WindowContext = createContext({ offset: 0, start: 0, end: Infinity, active: true });
export const MediaContext = createContext<{
  clock: FrameClock;
  soundRequired: boolean;
  requireSound: () => void;
  registerSound: (enable: () => void) => () => void;
  enableSound: () => void;
} | null>(null);

/** Outside a FramePlayer, returns an inert policy; external controls use FramePlayerHandle. */
export function usePlaybackPolicy() {
  const media = useContext(MediaContext);
  return {
    soundRequired: media?.soundRequired ?? false,
    enableSound: (event?: { stopPropagation(): void }) => {
      event?.stopPropagation();
      media?.enableSound();
    },
  };
}

export function useFrame(): number {
  const frame = useContext(FrameContext);
  const window = useContext(WindowContext);
  return window.active ? frame - window.offset : Math.max(0, frame - window.offset);
}

export interface FrameWindowProps {
  startFrame: number;
  frameCount: number;
  label?: string;
  children?: ReactNode;
  preloadFrames?: number;
}

export function FrameWindow({ startFrame, frameCount, label, children, preloadFrames }: FrameWindowProps) {
  const frame = useContext(FrameContext);
  const parent = useContext(WindowContext);
  const playback = useContext(PlaybackContext);
  const offset = parent.offset + startFrame;
  const start = Math.max(parent.start, offset);
  const end = Math.min(parent.end, offset + frameCount);
  const lead = preloadFrames ?? Math.ceil((playback?.fps ?? 30) * (playback?.speed ?? 1));
  const active = parent.active && frame >= offset && frame < offset + frameCount;
  const window = useMemo(() => ({ offset, start, end, active }), [offset, start, end, active]);
  if (frame < offset - Math.max(0, lead) || frame >= offset + frameCount) return null;
  return <WindowContext.Provider value={window}>
    <Layer data-clip-label={label} aria-hidden={!active}
      style={{ visibility: active ? "visible" : "hidden", pointerEvents: active ? undefined : "none" }}>{children}</Layer>
  </WindowContext.Provider>;
}
