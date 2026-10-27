import { useContext, useLayoutEffect, useRef, useSyncExternalStore, type CSSProperties, type RefObject } from "react";
import { PlaybackContext, MediaContext, WindowContext, useFrame } from "./context";

export const MEDIA_DRIFT_THRESHOLD_SECONDS = 0.15;
export const MEDIA_HARD_SEEK_THRESHOLD_SECONDS = 1;
export const MEDIA_DRIFT_SETTLED_SECONDS = 0.04;
const STILL_MAX_SIDE = 1920;
const MEDIA_TIME_TOLERANCE = 0.000001;

export interface ClipMediaProps {
  src: string;
  sourceStartFrame?: number;
  gain?: number | ((frame: number) => number);
  muted?: boolean;
  style?: CSSProperties;
  /** Defaults to true for ClipVideo and false for ClipAudio. */
  required?: boolean;
}

function useMedia(ref: RefObject<HTMLMediaElement | null>, { src, sourceStartFrame = 0, gain = 1, muted = false, required = true, style }: ClipMediaProps,
  still?: RefObject<HTMLCanvasElement | null>) {
  const playback = useContext(PlaybackContext);
  const media = useContext(MediaContext);
  const window = useContext(WindowContext);
  const frame = useFrame();
  if (!playback || !media) throw new Error("Media requires a FramePlayer");
  const { clock } = media;
  const buffering = useSyncExternalStore(clock.subscribeBuffering, clock.isBuffering, clock.isBuffering);
  const state = useRef({ playback, media, window, sourceStartFrame, muted, required, visibility: style?.visibility });
  state.current = { playback, media, window, sourceStartFrame, muted, required, visibility: style?.visibility };
  const sync = useRef<() => void>(() => {});
  const renderPresentation = useRef<() => void>(() => {});
  const enable = useRef<() => void>(() => {});
  const level = typeof gain === "function" ? gain(frame) : gain;

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (element.getAttribute("src") !== src) element.setAttribute("src", src);
    const owner = {};
    let mounted = true;
    let waiting = false;
    let policyMuted = state.current.media.soundRequired;
    let blockedPlay = false;
    let pending: Promise<void> | null = null;
    let startingPlay = false;
    let correcting = false;
    let seekStarted: number | null = null;
    let seekLatency = 0;
    let lastSeekPosition: number | null = null;
    let seekVersion = -1;
    let sourceOffset = NaN;
    let sourceStart = NaN;
    let sourceEnd = NaN;
    const video = element instanceof HTMLVideoElement && typeof element.requestVideoFrameCallback === "function" ? element : null;
    let videoFrame: number | null = null;
    const presentation = {
      presentedTime: null as number | null,
      stillTime: null as number | null,
      pendingSeek: false,
      hidden: true,
      // A seek discards reuse, but an already displayed still lasts until its replacement arrives.
      stillAvailable: false,
      showStill: false,
    };
    const sourceWindow = () => {
      const { playback, window, sourceStartFrame } = state.current;
      return { start: Math.max(0, (sourceStartFrame + window.start - window.offset) / playback.fps),
        end: (sourceStartFrame + window.end - window.offset) / playback.fps };
    };
    const validStill = () => {
      const { end } = sourceWindow();
      return presentation.stillTime !== null && presentation.stillTime < end - MEDIA_TIME_TOLERANCE;
    };
    renderPresentation.current = () => {
      if (!video) return;
      video.style.visibility = presentation.hidden ? "hidden" : state.current.visibility ?? "";
      if (still?.current) {
        const show = presentation.hidden && presentation.showStill && validStill();
        if (show) {
          still.current.style.left = `${video.offsetLeft}px`;
          still.current.style.top = `${video.offsetTop}px`;
          still.current.style.width = `${video.offsetWidth}px`;
          still.current.style.height = `${video.offsetHeight}px`;
        }
        still.current.style.display = show ? "block" : "none";
      }
    };
    const timelineTime = () => {
      const { playback, window, sourceStartFrame } = state.current;
      const position = window.active ? playback.clock.readPosition() - window.offset : 0;
      return (sourceStartFrame + position) / playback.fps;
    };
    const targetTime = () => {
      const bounds = sourceWindow();
      const target = Math.max(bounds.start, Math.min(bounds.end - MEDIA_TIME_TOLERANCE, timelineTime()));
      return Number.isFinite(element.duration) ? Math.min(element.duration, target) : target;
    };
    const hasRemainingTime = () => !Number.isFinite(element.duration) || timelineTime() < element.duration;
    const shouldPlay = () => mounted && !element.error && state.current.window.active && clock.isRunning() && !clock.isBuffering() && hasRemainingTime();
    const resume = () => {
      if (!shouldPlay() || startingPlay || pending || blockedPlay || (!element.paused && !element.ended)) return;
      startingPlay = true;
      if (element.ended) element.currentTime = targetTime();
      const attempt = element.play();
      pending = attempt;
      startingPlay = false;
      void attempt.then(() => {
        if (pending === attempt) pending = null;
        if (!shouldPlay()) element.pause();
      }, (error: unknown) => {
        if (pending !== attempt) return;
        pending = null;
        if (!mounted) return;
        if ((error as { name?: string } | null)?.name === "NotAllowedError") {
          if (!element.muted) {
            policyMuted = true;
            element.muted = true;
            state.current.media.requireSound();
            resume();
          } else {
            blockedPlay = true;
            updateBlock();
          }
        }
      });
    };
    const updateBlock = () => {
      clock.setBlocked(owner, !element.error && state.current.window.active && state.current.required && hasRemainingTime()
        && (blockedPlay || waiting || element.readyState < 3 || element.seeking));
    };
    const seek = (target: number, correction = false) => {
      if (Math.abs(element.currentTime - target) > 0.000001) {
        presentation.stillAvailable = false;
        presentation.pendingSeek = true;
        renderPresentation.current();
        correcting = correction;
        seekStarted = performance.now();
        element.currentTime = target;
      }
    };
    sync.current = () => {
      const { playback, window, sourceStartFrame, muted } = state.current;
      element.muted = !window.active || muted || policyMuted;
      if (element.error) {
        waiting = false;
        blockedPlay = false;
        updateBlock();
        element.pause();
        return;
      }
      const offset = sourceStartFrame - window.offset;
      const bounds = sourceWindow();
      const sourceChanged = sourceOffset !== offset || sourceStart !== bounds.start || sourceEnd !== bounds.end;
      const explicitSeek = seekVersion !== playback.seekVersion || sourceChanged;
      if (explicitSeek) {
        presentation.stillAvailable = false;
        if (sourceChanged && video) {
          const time = presentation.presentedTime;
          // The start is unguarded: the frame covering it can carry an earlier timestamp. Only a source start that
          // really moved, with a seek about to replace the picture, hides the frame left behind before it.
          const startTrimmed = sourceStart !== bounds.start
            && (Math.abs(element.currentTime - targetTime()) > 0.000001 || presentation.pendingSeek || element.seeking);
          const outside = time === null || time >= bounds.end - MEDIA_TIME_TOLERANCE
            || (startTrimmed && time < bounds.start - MEDIA_TIME_TOLERANCE);
          if (outside) presentation.hidden = true;
          else if (!presentation.pendingSeek && !element.seeking) {
            presentation.hidden = false;
            presentation.showStill = false;
          }
          if (sourceStart !== bounds.start || !validStill()) {
            presentation.stillTime = null;
            presentation.showStill = false;
          }
          renderPresentation.current();
        }
        seekVersion = playback.seekVersion;
        sourceOffset = offset;
        sourceStart = bounds.start;
        sourceEnd = bounds.end;
        waiting = false;
        blockedPlay = false;
        lastSeekPosition = null;
        element.playbackRate = playback.speed;
        seek(targetTime());
      } else if (!window.active || !clock.isRunning()) {
        seek(targetTime());
        element.playbackRate = playback.speed;
      } else if (!element.seeking && !clock.isBuffering() && !blockedPlay) {
        const target = targetTime();
        const drift = element.currentTime - target;
        if (Math.abs(drift) > MEDIA_HARD_SEEK_THRESHOLD_SECONDS
          && (lastSeekPosition === null || element.currentTime > lastSeekPosition + 0.000001)) {
          const ahead = target + (state.current.required ? 0 : seekLatency * playback.speed);
          const bounded = Math.max(bounds.start, Math.min(bounds.end - MEDIA_TIME_TOLERANCE, ahead));
          seek(Number.isFinite(element.duration) ? Math.min(element.duration, bounded) : bounded, true);
          element.playbackRate = playback.speed;
        } else if (Math.abs(drift) > MEDIA_DRIFT_THRESHOLD_SECONDS) {
          element.playbackRate = playback.speed * (drift > 0 ? 0.9 : 1.1);
        } else if (Math.abs(drift) < MEDIA_DRIFT_SETTLED_SECONDS) element.playbackRate = playback.speed;
      }
      updateBlock();
      if (shouldPlay()) resume();
      else element.pause();
    };
    const present = (_now: number, metadata: VideoFrameCallbackMetadata) => {
      videoFrame = null;
      if (!video || !mounted) return;
      const { end } = sourceWindow();
      const canvas = still?.current;
      presentation.presentedTime = metadata.mediaTime;
      if (metadata.mediaTime >= end - MEDIA_TIME_TOLERANCE) {
        // Frame callbacks precede paint: retain the last safe picture if decoding outruns the clock.
        presentation.hidden = true;
        if (!presentation.pendingSeek) presentation.showStill = presentation.stillAvailable;
      } else {
        presentation.pendingSeek = false;
        presentation.hidden = false;
        presentation.showStill = false;
        if (canvas) {
          // The element's own time must still be inside the window, so the copy cannot be a later, excluded frame.
          if (metadata.mediaTime >= end - 0.25 && video.currentTime < end - MEDIA_TIME_TOLERANCE) {
            const scale = Math.min(1, STILL_MAX_SIDE / Math.max(video.videoWidth, video.videoHeight, 1));
            const width = Math.round(video.videoWidth * scale);
            const height = Math.round(video.videoHeight * scale);
            if (canvas.width !== width) canvas.width = width;
            if (canvas.height !== height) canvas.height = height;
            const context = canvas.getContext("2d");
            if (context && canvas.width && canvas.height) {
              context.drawImage(video, 0, 0, width, height);
              presentation.stillTime = metadata.mediaTime;
              presentation.stillAvailable = validStill();
            }
          }
        }
      }
      renderPresentation.current();
      videoFrame = video.requestVideoFrameCallback(present);
    };
    enable.current = () => {
      policyMuted = false;
      blockedPlay = false;
      element.muted = !state.current.window.active || state.current.muted;
      // Invoke play in the click's call stack to preserve user activation.
      sync.current();
    };
    const onWaiting = () => { waiting = true; sync.current(); };
    const onStalled = () => { if (element.readyState < 3) onWaiting(); };
    const onReady = () => { waiting = false; sync.current(); };
    const onTimeUpdate = () => { if (element.readyState >= 3) onReady(); };
    const onSeeking = () => {
      presentation.pendingSeek = true;
      presentation.stillAvailable = false;
      renderPresentation.current();
      seekStarted ??= performance.now();
      updateBlock();
    };
    const onSeeked = () => {
      if (seekStarted !== null) seekLatency = Math.max(0, performance.now() - seekStarted) / 1000;
      seekStarted = null;
      lastSeekPosition = correcting || seekLatency > 0 ? element.currentTime : null;
      correcting = false;
      sync.current();
    };
    const onMetadata = () => { seekVersion = -1; sync.current(); };
    const onStopped = () => resume();
    const onPlay = () => { blockedPlay = false; sync.current(); };
    const onPause = () => sync.current();
    const listeners = { waiting: onWaiting, stalled: onStalled, canplay: onReady, playing: onReady, timeupdate: onTimeUpdate,
      seeking: onSeeking, seeked: onSeeked, loadedmetadata: onMetadata, pause: onStopped, ended: onStopped,
      error: () => sync.current() };
    for (const [name, listener] of Object.entries(listeners)) element.addEventListener(name, listener);
    clock.listen("play", onPlay);
    clock.listen("pause", onPause);
    const unregisterSound = state.current.media.registerSound(() => enable.current());
    if (video) videoFrame = video.requestVideoFrameCallback(present);
    sync.current();
    return () => {
      mounted = false;
      if (video && videoFrame !== null) video.cancelVideoFrameCallback(videoFrame);
      pending = null;
      sync.current = () => {};
      renderPresentation.current = () => {};
      enable.current = () => {};
      unregisterSound();
      clock.unlisten("play", onPlay);
      clock.unlisten("pause", onPause);
      for (const [name, listener] of Object.entries(listeners)) element.removeEventListener(name, listener);
      element.pause();
      element.removeAttribute("src");
      element.load();
      clock.setBlocked(owner, false);
    };
  }, [ref, src, clock]);

  useLayoutEffect(() => { sync.current(); }, [frame, playback.seekVersion, playback.playing, playback.speed,
    playback.fps, window.active, window.offset, window.start, window.end, sourceStartFrame, muted, required, buffering, media.soundRequired]);
  // React can overwrite the imperative guard when the caller changes its visibility style.
  useLayoutEffect(() => { renderPresentation.current(); });
  useLayoutEffect(() => {
    if (ref.current) ref.current.volume = Number.isNaN(level) ? 0 : Math.max(0, Math.min(1, level));
  }, [ref, level]);
}

export function ClipVideo(props: ClipMediaProps) {
  const ref = useRef<HTMLVideoElement>(null);
  const still = useRef<HTMLCanvasElement>(null);
  useMedia(ref, props, still);
  return <><video ref={ref} src={props.src} style={props.style} playsInline preload="auto" />
    <canvas ref={still} aria-hidden
      style={{ top: 0, left: 0, ...props.style, position: "absolute", margin: 0, boxSizing: "border-box",
        display: "none", pointerEvents: "none" }} /></>;
}

export function ClipAudio(props: ClipMediaProps) {
  const ref = useRef<HTMLAudioElement>(null);
  useMedia(ref, { ...props, required: props.required ?? false });
  return <audio ref={ref} src={props.src} style={props.style} preload="auto" />;
}
