import {
  forwardRef, memo, useCallback, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState,
  useSyncExternalStore, type ComponentType, type CSSProperties, type ForwardedRef, type ReactElement, type RefAttributes,
} from "react";
import { FrameClock, type FramePlayerHandle } from "./clock";
import { FrameContext, MediaContext, PlaybackContext } from "./context";

export interface FramePlayerProps<Props extends object> {
  content: ComponentType<Props>;
  contentProps: Props;
  frameCount: number;
  fps: number;
  speed?: number;
  width: number;
  height: number;
  keyboardPlayback?: boolean;
  clickToggles?: boolean;
  fullscreenOnDoubleClick?: boolean;
  style?: CSSProperties;
}

export function containScale(viewportWidth: number, viewportHeight: number, width: number, height: number): number {
  if (width <= 0 || height <= 0) throw new RangeError("Content dimensions must be positive");
  return Math.max(0, Math.min(viewportWidth / width, viewportHeight / height));
}

function FramePlayerInner<Props extends object>({
  content: Content, contentProps, frameCount, fps, speed = 1, width, height,
  keyboardPlayback = false, clickToggles = false, fullscreenOnDoubleClick = false, style,
}: FramePlayerProps<Props>, ref: ForwardedRef<FramePlayerHandle>) {
  const [clock] = useState(() => new FrameClock(fps, frameCount));
  const subscribeSound = useCallback((notify: () => void) => {
    clock.listen("sound", notify);
    return () => clock.unlisten("sound", notify);
  }, [clock]);
  const soundRequired = useSyncExternalStore(subscribeSound, clock.isSoundBlocked, clock.isSoundBlocked);
  const soundCallbacks = useRef(new Set<() => void>());
  const enableSound = useCallback(() => {
    clock.setSoundBlocked(false);
    for (const enable of soundCallbacks.current) enable();
  }, [clock]);
  const media = useMemo(() => ({
    clock, soundRequired,
    requireSound: () => clock.setSoundBlocked(true),
    registerSound: (enable: () => void) => {
      soundCallbacks.current.add(enable);
      return () => { soundCallbacks.current.delete(enable); };
    },
    enableSound,
  }), [clock, soundRequired, enableSound]);
  const container = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const subscribe = useCallback((notify: () => void) => {
    clock.listen("frame", notify);
    return () => clock.unlisten("frame", notify);
  }, [clock]);
  const frame = useSyncExternalStore(subscribe, clock.readFrame, clock.readFrame);
  const subscribeSeek = useCallback((notify: () => void) => {
    clock.listen("seek", notify);
    return () => clock.unlisten("seek", notify);
  }, [clock]);
  const seekVersion = useSyncExternalStore(subscribeSeek, clock.readSeekVersion, clock.readSeekVersion);
  const subscribePlaying = useCallback((notify: () => void) => {
    clock.listen("play", notify);
    clock.listen("pause", notify);
    return () => {
      clock.unlisten("play", notify);
      clock.unlisten("pause", notify);
    };
  }, [clock]);
  const playing = useSyncExternalStore(subscribePlaying, clock.isRunning, clock.isRunning);
  const buffering = useSyncExternalStore(clock.subscribeBuffering, clock.isBuffering, clock.isBuffering);
  const subscribeRate = useCallback((notify: () => void) => {
    clock.listen("rate", notify);
    return () => clock.unlisten("rate", notify);
  }, [clock]);
  const rate = useSyncExternalStore(subscribeRate, clock.readSpeed, clock.readSpeed);
  const playback = useMemo(() => ({ clock, fps, seekVersion, playing, speed: rate }),
    [clock, fps, seekVersion, playing, rate]);
  const content = useMemo(() => <Content {...contentProps} />, [Content, contentProps]);
  useImperativeHandle(ref, (): FramePlayerHandle => ({
    seek: (frame) => clock.seek(frame),
    readFrame: clock.readFrame,
    play: () => clock.play(),
    pause: () => clock.pause(),
    isRunning: clock.isRunning,
    isSoundBlocked: clock.isSoundBlocked,
    enableSound,
    listen: (name, listener) => clock.listen(name, listener),
    unlisten: (name, listener) => clock.unlisten(name, listener),
  }), [clock, enableSound]);
  useLayoutEffect(() => { clock.configure(fps, frameCount); }, [clock, fps, frameCount]);
  useLayoutEffect(() => { clock.setSpeed(speed); }, [clock, speed]);
  useLayoutEffect(() => () => clock.dispose(), [clock]);
  useLayoutEffect(() => {
    const element = container.current;
    if (!element) return;
    const measure = () => {
      setScale(containScale(element.clientWidth, element.clientHeight, width, height));
    };
    measure();
    const observer = new ResizeObserver((entries) => {
      const { width: viewportWidth, height: viewportHeight } = entries[0].contentRect;
      setScale(containScale(viewportWidth, viewportHeight, width, height));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [width, height]);
  const toggle = () => { if (clock.isRunning()) clock.pause(); else clock.play(); };
  return <div
    ref={container}
    tabIndex={keyboardPlayback ? 0 : undefined}
    onKeyDown={(event) => {
      if (keyboardPlayback && event.code === "Space" && !event.repeat) {
        event.preventDefault();
        toggle();
      }
    }}
    onClick={clickToggles ? toggle : undefined}
    onDoubleClick={fullscreenOnDoubleClick ? () => { void container.current?.requestFullscreen(); } : undefined}
    style={{ position: "relative", overflow: "hidden", ...style }}
  >
    <div style={{
      position: "absolute", width, height, left: "50%", top: "50%",
      transform: `translate(-50%, -50%) scale(${scale})`, transformOrigin: "center",
    }}>
      <MediaContext.Provider value={media}>
        <PlaybackContext.Provider value={playback}>
          <FrameContext.Provider value={frame}>{content}</FrameContext.Provider>
        </PlaybackContext.Provider>
      </MediaContext.Provider>
    </div>
    {playing && buffering && <div role="status" style={{
      position: "absolute", bottom: 12, left: 12, padding: "6px 10px", borderRadius: 6,
      background: "rgba(8,10,12,.8)", color: "#f4f6f7", fontSize: 12, pointerEvents: "none",
    }}>Waiting for media</div>}
  </div>;
}

export const FramePlayer = memo(forwardRef(FramePlayerInner)) as <Props extends object>(
  props: FramePlayerProps<Props> & RefAttributes<FramePlayerHandle>,
) => ReactElement | null;
