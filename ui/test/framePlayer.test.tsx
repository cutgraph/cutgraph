// @vitest-environment jsdom
import { createRef, StrictMode, useContext, type ContextType } from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  Layer, ClipAudio, FrameImage, FramePlayer, FrameWindow, ClipVideo, useFrame, type FrameListener, type FramePlayerHandle,
} from "../src/player";
import { containScale } from "../src/player/FramePlayer";
import { PlaybackContext } from "../src/player/context";
import { MEDIA_DRIFT_THRESHOLD_SECONDS, MEDIA_HARD_SEEK_THRESHOLD_SECONDS } from "../src/player/media";

let time: number;
let nextHandle: number;
let callbacks: Map<number, FrameRequestCallback>;
let resize: ResizeObserverCallback;
const disconnect = vi.fn();
let mediaElements: WeakMap<HTMLMediaElement, FakeMedia>;
let activeMedia: Set<FakeMedia>;

class FakeMedia {
  constructor(private element: HTMLMediaElement) {}
  currentTime = 0;
  paused = true;
  ended = false;
  duration = Infinity;
  readyState = 4;
  playbackRate = 1;
  seeking = false;
  seekLatency = 0;
  playLatency = 0;
  private seekRemaining = 0;
  private pendingPlay: { remaining: number; resolve: () => void; reject: (error: DOMException) => void } | null = null;
  seek = vi.fn((target: number) => {
    this.currentTime = Math.min(this.duration, Math.round(Math.max(0, target) * 1000000) / 1000000);
    if (this.currentTime < this.duration) this.ended = false;
    this.seeking = true;
    this.seekRemaining = this.seekLatency;
    this.element.dispatchEvent(new Event("seeking"));
    if (!this.seekRemaining) {
      this.seeking = false;
      this.element.dispatchEvent(new Event("seeked"));
    }
  });
  play = vi.fn((): Promise<void> => {
    if (this.ended) this.seek(0);
    this.paused = false;
    this.ended = false;
    if (!this.playLatency) return Promise.resolve();
    return new Promise((resolve, reject) => {
      this.pendingPlay = { remaining: this.playLatency, resolve, reject };
    });
  });
  pause = vi.fn(() => {
    const wasPaused = this.paused;
    this.paused = true;
    this.pendingPlay?.reject(new DOMException("Playback interrupted", "AbortError"));
    this.pendingPlay = null;
    if (!wasPaused) this.element.dispatchEvent(new Event("pause"));
  });
  advance(ms: number) {
    if (!this.element.isConnected) return;
    let playingMs = ms;
    if (this.pendingPlay) {
      playingMs = Math.max(0, playingMs - this.pendingPlay.remaining);
      this.pendingPlay.remaining -= ms;
      if (this.pendingPlay.remaining <= 0) {
        this.pendingPlay.resolve();
        this.pendingPlay = null;
      }
    }
    if (this.seeking) {
      playingMs = Math.min(playingMs, Math.max(0, ms - this.seekRemaining));
      this.seekRemaining -= ms;
      if (this.seekRemaining <= 0) {
        this.seeking = false;
        this.element.dispatchEvent(new Event("seeked"));
      }
    }
    if (!this.paused && !this.seeking) {
      this.currentTime = Math.min(this.duration, this.currentTime + playingMs * this.playbackRate / 1000);
      if (this.currentTime >= this.duration && !this.ended) {
        this.ended = true;
        this.pause();
        this.element.dispatchEvent(new Event("ended"));
      }
    }
  }
}

function mediaFor(element: HTMLMediaElement): FakeMedia {
  let media = mediaElements.get(element);
  if (!media) {
    media = new FakeMedia(element);
    mediaElements.set(element, media);
    activeMedia.add(media);
  }
  return media;
}

beforeEach(() => {
  time = 0;
  nextHandle = 0;
  callbacks = new Map();
  mediaElements = new WeakMap();
  activeMedia = new Set();
  vi.spyOn(HTMLMediaElement.prototype, "currentTime", "get").mockImplementation(function (this: HTMLMediaElement) {
    return mediaFor(this).currentTime;
  });
  vi.spyOn(HTMLMediaElement.prototype, "currentTime", "set").mockImplementation(function (this: HTMLMediaElement, value) {
    mediaFor(this).seek(value);
  });
  vi.spyOn(HTMLMediaElement.prototype, "paused", "get").mockImplementation(function (this: HTMLMediaElement) {
    return mediaFor(this).paused;
  });
  vi.spyOn(HTMLMediaElement.prototype, "ended", "get").mockImplementation(function (this: HTMLMediaElement) {
    return mediaFor(this).ended;
  });
  vi.spyOn(HTMLMediaElement.prototype, "seeking", "get").mockImplementation(function (this: HTMLMediaElement) {
    return mediaFor(this).seeking;
  });
  vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockImplementation(function (this: HTMLMediaElement) {
    return mediaFor(this).duration;
  });
  vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockImplementation(function (this: HTMLMediaElement) {
    return mediaFor(this).readyState;
  });
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "playbackRate", "get").mockImplementation(function (this: HTMLMediaElement) {
    return mediaFor(this).playbackRate;
  });
  vi.spyOn(HTMLMediaElement.prototype, "playbackRate", "set").mockImplementation(function (this: HTMLMediaElement, value) {
    mediaFor(this).playbackRate = value;
  });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function (this: HTMLMediaElement) {
    return mediaFor(this).play();
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(function (this: HTMLMediaElement) {
    mediaFor(this).pause();
  });
  disconnect.mockClear();
  vi.spyOn(performance, "now").mockImplementation(() => time);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callbacks.set(++nextHandle, callback);
    return nextHandle;
  });
  vi.stubGlobal("cancelAnimationFrame", (handle: number) => callbacks.delete(handle));
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: ResizeObserverCallback) { resize = callback; }
    observe() {}
    disconnect = disconnect;
  });
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(300);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function advance(ms: number) {
  act(() => {
    time += ms;
    for (const media of activeMedia) media.advance(ms);
    const pending = [...callbacks.values()];
    callbacks.clear();
    for (const callback of pending) callback(time);
  });
}

function Frame({ label }: { label: string }) {
  return <span data-testid={label}>{useFrame()}</span>;
}

const playerProps = {
  frameCount: 100, fps: 10, width: 800, height: 400,
  keyboardPlayback: false, clickToggles: false, fullscreenOnDoubleClick: false,
  style: { width: "100%", height: "100%" },
} as const;

describe("player composition", () => {
  it("passes content props and exposes a stable handle with frame and transport events", () => {
    const ref = createRef<FramePlayerHandle>();
    const view = render(<FramePlayer {...playerProps} ref={ref} content={Frame} contentProps={{ label: "frame" }} />);
    const original = ref.current!;
    expect(Object.keys(original).sort()).toEqual([
      "enableSound", "isRunning", "isSoundBlocked", "listen", "pause", "play", "readFrame", "seek", "unlisten",
    ]);
    const events: string[] = [];
    const onFrame: FrameListener<"frame"> = (event) => events.push(`frame:${event.detail.frame}`);
    const onSeek: FrameListener<"seek"> = (event) => events.push(`seek:${event.detail.frame}`);
    original.listen("frame", onFrame);
    original.listen("seek", onSeek);
    original.listen("play", () => events.push("play"));
    original.listen("pause", () => events.push("pause"));
    original.listen("end", () => events.push("end"));
    act(() => original.play());
    advance(200);
    expect(view.getByTestId("frame").textContent).toBe("2");
    act(() => original.pause());
    advance(1000);
    expect(view.getByTestId("frame").textContent).toBe("2");
    act(() => original.seek(8));
    expect(view.getByTestId("frame").textContent).toBe("8");
    expect(events).toEqual(["play", "frame:2", "pause", "seek:8", "frame:8"]);
    original.unlisten("frame", onFrame);
    act(() => { original.seek(98); original.play(); });
    advance(100);
    expect(view.getByTestId("frame").textContent).toBe("99");
    expect(original.isRunning()).toBe(true);
    advance(100);
    expect(events.slice(-2)).toEqual(["pause", "end"]);
    view.rerender(<FramePlayer {...playerProps} fps={20} frameCount={200} ref={ref} content={Frame} contentProps={{ label: "frame" }} />);
    expect(ref.current).toBe(original);
    act(() => original.play());
    advance(100);
    expect(view.getByTestId("frame").textContent).toBe("101");
  });

  it("renders frame windows with half-open boundaries and additive nested offsets", () => {
    function Composition() {
      return <>
        <Frame label="absolute" />
        <FrameWindow startFrame={10} frameCount={10} label="outer">
          <Frame label="outer" />
          <FrameWindow startFrame={3} frameCount={4}><Frame label="inner" /></FrameWindow>
        </FrameWindow>
        <FrameWindow startFrame={-2} frameCount={4}><Frame label="negative" /></FrameWindow>
      </>;
    }
    const ref = createRef<FramePlayerHandle>();
    const view = render(<FramePlayer {...playerProps} ref={ref} content={Composition} contentProps={{}} />);
    expect(view.getByTestId("absolute").textContent).toBe("0");
    expect(view.getByTestId("negative").textContent).toBe("2");
    for (const frame of [9, 10, 12, 13, 16, 17, 19, 20]) {
      act(() => ref.current!.seek(frame));
      expect(view.getByTestId("absolute").textContent).toBe(String(frame));
      const mountedOuter = view.queryByTestId("outer");
      const mountedInner = view.queryByTestId("inner");
      const outer = mountedOuter?.closest('[aria-hidden="true"]') ? null : mountedOuter;
      const inner = mountedInner?.closest('[aria-hidden="true"]') ? null : mountedInner;
      if (frame === 9) expect(mountedOuter?.closest('[aria-hidden="true"]')).not.toBeNull();
      expect(outer?.textContent ?? null).toBe(frame >= 10 && frame < 20 ? String(frame - 10) : null);
      expect(inner?.textContent ?? null).toBe(frame >= 13 && frame < 17 ? String(frame - 13) : null);
    }
    act(() => ref.current!.seek(13));
    const layer = view.container.querySelector<HTMLElement>('[data-clip-label="outer"]')!;
    expect(layer.style.position).toBe("absolute");
    expect(layer.style.width).toBe("100%");
  });

  it("uniformly contains and centers the composition, and observes resizing", () => {
    expect(containScale(400, 300, 800, 400)).toBe(0.5);
    expect(containScale(300, 400, 400, 800)).toBe(0.5);
    expect(containScale(1600, 800, 800, 400)).toBe(2);
    expect(containScale(0, 0, 800, 400)).toBe(0);
    const view = render(<FramePlayer {...playerProps} content={Frame} contentProps={{ label: "frame" }} />);
    const layer = view.getByTestId("frame").parentElement!;
    expect(layer.style.width).toBe("800px");
    expect(layer.style.height).toBe("400px");
    expect(layer.style.left).toBe("50%");
    expect(layer.style.top).toBe("50%");
    expect(layer.style.transform).toBe("translate(-50%, -50%) scale(0.5)");
    act(() => resize([{ contentRect: { width: 200, height: 300 } } as ResizeObserverEntry], {} as ResizeObserver));
    expect(layer.style.transform).toBe("translate(-50%, -50%) scale(0.25)");
    view.unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it("survives strict effects and cancels animation work on unmount", () => {
    const ref = createRef<FramePlayerHandle>();
    const view = render(<StrictMode><FramePlayer {...playerProps} ref={ref} content={Frame} contentProps={{ label: "frame" }} /></StrictMode>);
    const clock = ref.current!;
    const paused = vi.fn();
    clock.listen("pause", paused);
    act(() => clock.play());
    advance(200);
    expect(view.getByTestId("frame").textContent).toBe("2");
    view.unmount();
    expect(callbacks.size).toBe(0);
    expect(clock.isRunning()).toBe(false);
    expect(paused).toHaveBeenCalledOnce();
    expect(ref.current).toBeNull();
  });

  it("updates frame consumers without rerendering the composition tree", () => {
    const compositionRenders = vi.fn();
    const staticRenders = vi.fn();
    const playbackRenders = vi.fn();
    function StaticLayer() { staticRenders(); return <div>Static layer</div>; }
    function PlaybackLayer() { useContext(PlaybackContext); playbackRenders(); return null; }
    function Composition({ label }: { label: string }) {
      compositionRenders();
      return <><StaticLayer /><PlaybackLayer /><Frame label={label} /></>;
    }
    const ref = createRef<FramePlayerHandle>();
    const contentProps = { label: "frame" };
    const view = render(<FramePlayer {...playerProps} ref={ref} content={Composition} contentProps={contentProps} />);
    expect(compositionRenders).toHaveBeenCalledOnce();
    expect(staticRenders).toHaveBeenCalledOnce();
    act(() => ref.current!.play());
    expect(playbackRenders).toHaveBeenCalledTimes(2);
    advance(100);
    advance(100);
    expect(view.getByTestId("frame").textContent).toBe("2");
    expect(compositionRenders).toHaveBeenCalledOnce();
    expect(staticRenders).toHaveBeenCalledOnce();
    expect(playbackRenders).toHaveBeenCalledTimes(2);
    view.rerender(<FramePlayer {...playerProps} ref={ref} content={Composition} contentProps={{ label: "updated" }} />);
    expect(view.getByTestId("updated").textContent).toBe("2");
    expect(compositionRenders).toHaveBeenCalledTimes(2);
  });

  it("keeps player clocks independent and leaves disabled click controls inert", () => {
    const left = createRef<FramePlayerHandle>();
    const right = createRef<FramePlayerHandle>();
    const view = render(<>
      <FramePlayer {...playerProps} ref={left} content={Frame} contentProps={{ label: "left" }} />
      <FramePlayer {...playerProps} ref={right} content={Frame} contentProps={{ label: "right" }} />
    </>);
    fireEvent.click(view.getByTestId("right"));
    expect(right.current!.isRunning()).toBe(false);
    act(() => left.current!.play());
    advance(500);
    expect(view.getByTestId("left").textContent).toBe("5");
    expect(view.getByTestId("right").textContent).toBe("0");
  });

  it("provides full-size layers, images, and source-offset media that follows playback", () => {
    function Composition() {
      return <Layer style={{ backgroundColor: "black" }}>
        <FrameImage src="still.png" style={{ objectFit: "contain" }} />
        <FrameWindow startFrame={5} frameCount={20}>
          <ClipVideo src="clip.mp4" sourceStartFrame={20} gain={0.25} style={{ width: "100%" }} />
          <ClipAudio src="sound.wav" sourceStartFrame={30} gain={0.5} />
        </FrameWindow>
      </Layer>;
    }
    const ref = createRef<FramePlayerHandle>();
    const view = render(<FramePlayer {...playerProps} ref={ref} content={Composition} contentProps={{}} />);
    expect(view.container.querySelector("img")!.getAttribute("src")).toBe("still.png");
    expect(view.container.querySelector("img")!.parentElement!.style.flexDirection).toBe("column");
    act(() => ref.current!.seek(7));
    const video = view.container.querySelector("video")!;
    const audio = view.container.querySelector("audio")!;
    expect(video.currentTime).toBe(2.2);
    expect(audio.currentTime).toBe(3.2);
    expect(video.volume).toBe(0.25);
    expect(audio.volume).toBe(0.5);
    act(() => ref.current!.seek(9));
    expect(video.currentTime).toBe(2.4);
    expect(audio.currentTime).toBe(3.4);
    video.currentTime = 0;
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(2.4);
    act(() => ref.current!.play());
    advance(200);
    expect(video.currentTime).toBe(2.6);
    expect(audio.currentTime).toBe(3.6);
    expect(video.paused).toBe(false);
    expect(audio.paused).toBe(false);
  });

  it("evaluates function gains against clip-local frames during seeks and playback", () => {
    const videoVolume = vi.fn((frame: number) => frame / 10);
    const audioVolume = vi.fn((frame: number) => 1 - frame / 10);
    function Composition() {
      return <FrameWindow startFrame={5} frameCount={20}>
        <ClipVideo src="clip.mp4" gain={videoVolume} />
        <ClipAudio src="sound.wav" gain={audioVolume} />
      </FrameWindow>;
    }
    const ref = createRef<FramePlayerHandle>();
    const view = render(<FramePlayer {...playerProps} ref={ref} content={Composition} contentProps={{}} />);
    act(() => ref.current!.seek(7));
    const video = view.container.querySelector("video")!;
    const audio = view.container.querySelector("audio")!;
    expect(video.volume).toBe(0.2);
    expect(audio.volume).toBe(0.8);
    expect(videoVolume).toHaveBeenLastCalledWith(2);
    expect(audioVolume).toHaveBeenLastCalledWith(2);
    act(() => ref.current!.seek(9));
    expect(video.volume).toBe(0.4);
    expect(audio.volume).toBe(0.6);
    act(() => ref.current!.play());
    advance(100);
    expect(video.volume).toBe(0.5);
    expect(audio.volume).toBe(0.5);
  });

  it.each([
    { level: -1, expected: 0 }, { level: 2, expected: 1 }, { level: NaN, expected: 0 },
    { level: Infinity, expected: 1 }, { level: -Infinity, expected: 0 },
  ])("clamps numeric and function gains safely for $level", ({ level, expected }) => {
    function Composition() {
      return <><ClipVideo src="clip.mp4" gain={level} /><ClipAudio src="sound.wav" gain={() => level} /></>;
    }
    const view = render(<FramePlayer {...playerProps} content={Composition} contentProps={{}} />);
    expect(view.container.querySelector("video")!.volume).toBe(expected);
    expect(view.container.querySelector("audio")!.volume).toBe(expected);
  });
});

describe.each([
  { tag: "video", Media: ClipVideo }, { tag: "audio", Media: ClipAudio },
])("clock-driven $tag", ({ tag, Media }) => {
  function setupMedia(speed = 1, frameCount = 20) {
    let playback: ContextType<typeof PlaybackContext> = null;
    function Composition({ muted = false, src = "clip.mp4", sourceStartFrame = 20 }: {
      muted?: boolean; src?: string; sourceStartFrame?: number;
    }) {
      playback = useContext(PlaybackContext);
      return <FrameWindow startFrame={5} frameCount={frameCount}>
        <Media src={src} sourceStartFrame={sourceStartFrame} muted={muted} required />
      </FrameWindow>;
    }
    const ref = createRef<FramePlayerHandle>();
    const view = render(<FramePlayer {...playerProps} speed={speed} ref={ref} content={Composition} contentProps={{}} />);
    act(() => ref.current!.seek(7));
    const element = view.container.querySelector<HTMLMediaElement>(tag)!;
    advance(0);
    return { ref, view, element, media: mediaFor(element), Composition, playback: () => playback! };
  }

  it("starts media without a frame change, then pauses and snaps even between paints", () => {
    const { ref, element, media, playback } = setupMedia();
    expect(element.paused).toBe(true);
    expect(element.currentTime).toBe(2.2);
    act(() => ref.current!.play());
    expect(element.paused).toBe(false);
    expect(media.play).toHaveBeenCalledOnce();
    expect(playback().playing).toBe(true);
    media.currentTime = 2.21;
    act(() => ref.current!.pause());
    expect(element.paused).toBe(true);
    expect(element.currentTime).toBe(2.2);
    expect(playback().playing).toBe(false);
    act(() => ref.current!.play());
    time += 175;
    media.currentTime = 99;
    act(() => ref.current!.pause());
    expect(ref.current!.readFrame()).toBe(8);
    expect(element.currentTime).toBe(2.375);
    expect(element.paused).toBe(true);
    advance(1000);
    expect(element.currentTime).toBe(2.375);
  });

  it.each([
    { drift: MEDIA_DRIFT_THRESHOLD_SECONDS - 0.01, correct: false },
    { drift: -MEDIA_DRIFT_THRESHOLD_SECONDS + 0.01, correct: false },
    { drift: MEDIA_DRIFT_THRESHOLD_SECONDS + 0.01, correct: true },
    { drift: -MEDIA_DRIFT_THRESHOLD_SECONDS - 0.01, correct: true },
  ])("nudges drift $drift only beyond the threshold", ({ drift, correct }) => {
    const { ref, element, media } = setupMedia();
    act(() => ref.current!.play());
    media.currentTime = 2.2 + drift;
    media.seek.mockClear();
    advance(100);
    expect(element.currentTime).toBeCloseTo(2.3 + drift);
    expect(element.playbackRate).toBe(correct ? (drift > 0 ? 0.9 : 1.1) : 1);
    expect(media.seek).not.toHaveBeenCalled();
    expect(media.play).toHaveBeenCalledOnce();
  });

  it.each([-1, 1])("hard-seeks large drift in direction %s", (direction) => {
    const { ref, element, media } = setupMedia();
    act(() => ref.current!.play());
    media.currentTime += direction * (MEDIA_HARD_SEEK_THRESHOLD_SECONDS + 0.01);
    media.seek.mockClear();
    advance(100);
    expect(element.currentTime).toBeCloseTo(2.3);
    expect(media.seek).toHaveBeenCalledOnce();
    expect(element.playbackRate).toBe(1);
  });

  it("compares drift and pause positions against the clock's fractional position", () => {
    const { ref, element, media } = setupMedia();
    act(() => ref.current!.play());
    media.currentTime += 0.14;
    media.seek.mockClear();
    advance(175);
    expect(ref.current!.readFrame()).toBe(8);
    expect(element.currentTime).toBeCloseTo(2.515);
    expect(element.playbackRate).toBe(1);
    expect(media.seek).not.toHaveBeenCalled();
    act(() => ref.current!.pause());
    expect(element.currentTime).toBeCloseTo(2.375);
  });

  it("holds the clock for a slow required seek and resumes without another seek", async () => {
    const { ref, element, media } = setupMedia(1, 90);
    media.seekLatency = 450;
    const seeked = vi.fn();
    element.addEventListener("seeked", seeked);
    act(() => ref.current!.seek(8));
    await act(async () => ref.current!.play());
    media.seek.mockClear();
    for (let step = 0; step < 4; step++) advance(100);
    expect(ref.current!.readFrame()).toBe(8);
    expect(element.seeking).toBe(true);
    expect(seeked).not.toHaveBeenCalled();
    expect(media.seek).not.toHaveBeenCalled();
    advance(100);
    expect(element.seeking).toBe(false);
    expect(seeked).toHaveBeenCalledOnce();
    expect(element.playbackRate).toBe(1);
    for (let step = 0; step < 50; step++) advance(100);
    expect(element.paused).toBe(false);
    expect(element.currentTime).toBeCloseTo(7.3, 1);
    expect(element.playbackRate).toBe(1);
    expect(media.seek).not.toHaveBeenCalled();
  });

  it.each([
    { drift: -0.3, rate: 0.5 }, { drift: 0.3, rate: 0.5 },
    { drift: -0.3, rate: 1 }, { drift: 0.3, rate: 1 },
    { drift: -0.3, rate: 2 }, { drift: 0.3, rate: 2 },
  ])("converges drift $drift at rate $rate without seeking", ({ drift, rate }) => {
    const { ref, element, media, playback } = setupMedia(rate, 90);
    act(() => ref.current!.play());
    media.currentTime += drift;
    media.seek.mockClear();
    advance(100 / rate);
    expect(element.playbackRate).toBeCloseTo(rate * (drift > 0 ? 0.9 : 1.1));
    for (let step = 0; step < 40; step++) advance(100 / rate);
    const expected = (20 + playback().clock.readPosition() - 5) / 10;
    expect(Math.abs(element.currentTime - expected)).toBeLessThan(0.04);
    expect(element.playbackRate).toBe(rate);
    expect(media.seek).not.toHaveBeenCalled();
  });

  it("seeks only once per paused scrub and does not seek unchanged targets", () => {
    const { ref, media } = setupMedia();
    media.seek.mockClear();
    for (const frame of [8, 9, 10, 10, 11]) act(() => ref.current!.seek(frame));
    expect(media.seek.mock.calls.map(([target]) => target)).toEqual([2.3, 2.4, 2.5, 2.6]);
  });

  it("does not duplicate scrub seeks when media time has microsecond precision", () => {
    function Composition() { return <Media src="clip.mp4" />; }
    const ref = createRef<FramePlayerHandle>();
    const view = render(<FramePlayer {...playerProps} fps={30} ref={ref} content={Composition} contentProps={{}} />);
    const element = view.container.querySelector<HTMLMediaElement>(tag)!;
    const media = mediaFor(element);
    media.seek.mockClear();
    for (let frame = 1; frame <= 10; frame++) act(() => ref.current!.seek(frame));
    act(() => ref.current!.seek(10));
    expect(media.seek).toHaveBeenCalledTimes(10);
    expect(element.currentTime).toBeCloseTo(10 / 30, 6);
  });

  it("bounds seeks and play calls when the window extends beyond the media", async () => {
    const { ref, element, media } = setupMedia(1, 90);
    media.duration = 2.4;
    await act(async () => ref.current!.play());
    advance(200);
    expect(element.ended).toBe(true);
    media.seek.mockClear();
    for (let step = 0; step < 20; step++) advance(100);
    expect(element.currentTime).toBe(2.4);
    expect(element.paused).toBe(true);
    expect(media.seek).not.toHaveBeenCalled();
    expect(media.play).toHaveBeenCalledOnce();
    act(() => ref.current!.pause());
    act(() => ref.current!.seek(90));
    expect(element.currentTime).toBe(2.4);
    expect(media.seek).not.toHaveBeenCalled();
  });

  it("snaps on seeks to new and unchanged frames while playing and on metadata load", () => {
    const { ref, element, media } = setupMedia();
    act(() => ref.current!.play());
    media.currentTime = 2.29;
    act(() => ref.current!.seek(8));
    expect(element.currentTime).toBe(2.3);
    media.currentTime = 2.31;
    act(() => ref.current!.seek(8));
    expect(element.currentTime).toBe(2.3);
    media.currentTime = 0;
    fireEvent.loadedMetadata(element);
    expect(element.currentTime).toBe(2.3);
    expect(element.paused).toBe(false);
    expect(media.play).toHaveBeenCalledOnce();
  });

  it("resumes after EOF when seeking backward inside the mounted FrameWindow", async () => {
    const { ref, view, element, media } = setupMedia();
    media.duration = 2.4;
    await act(async () => ref.current!.play());
    advance(200);
    media.currentTime = media.duration;
    media.paused = true;
    media.ended = true;
    fireEvent.pause(element);
    fireEvent.ended(element);
    advance(100);
    expect(ref.current!.isRunning()).toBe(true);
    expect(element.paused).toBe(true);
    expect(media.play).toHaveBeenCalledOnce();
    act(() => ref.current!.seek(7));
    expect(view.container.querySelector(tag)).toBe(element);
    expect(element.currentTime).toBe(2.2);
    expect(element.paused).toBe(false);
    expect(media.play).toHaveBeenCalledTimes(2);
    act(() => ref.current!.pause());
    fireEvent.pause(element);
    fireEvent.ended(element);
    expect(element.paused).toBe(true);
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  it.each(["pause", "ended"])("recovers from an element's own %s event", async (event) => {
    const { ref, element, media } = setupMedia();
    await act(async () => ref.current!.play());
    media.paused = true;
    media.ended = event === "ended";
    fireEvent(element, new Event(event));
    expect(element.paused).toBe(false);
    expect(element.currentTime).toBe(2.2);
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  it.each([0, 0.5])("resumes on a frame update with drift %s", async (drift) => {
    const { ref, element, media } = setupMedia();
    await act(async () => ref.current!.play());
    media.paused = true;
    media.currentTime = 2.3 + drift;
    media.seek.mockClear();
    advance(100);
    expect(element.paused).toBe(false);
    expect(media.seek).not.toHaveBeenCalled();
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  it("keeps only one recovery play pending across events, seeks and frame updates", async () => {
    const { ref, element, media } = setupMedia();
    await act(async () => ref.current!.play());
    let resolvePlay!: () => void;
    const pending = new Promise<void>((resolve) => { resolvePlay = resolve; });
    media.play.mockImplementation(() => pending.then(() => { media.paused = false; }));
    media.paused = true;
    fireEvent.pause(element);
    fireEvent.pause(element);
    fireEvent.ended(element);
    advance(100);
    act(() => ref.current!.seek(7));
    fireEvent.loadedMetadata(element);
    expect(media.play).toHaveBeenCalledTimes(2);
    act(() => ref.current!.pause());
    await act(async () => { resolvePlay(); await pending; });
    expect(element.paused).toBe(true);
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  it("follows initial, prop, and clock rate changes without restarting media", () => {
    const { ref, view, element, media, Composition, playback } = setupMedia(2);
    expect(element.playbackRate).toBe(2);
    expect(playback().clock.readSpeed()).toBe(2);
    media.playbackRate = 1;
    fireEvent.loadedMetadata(element);
    expect(element.playbackRate).toBe(2);
    act(() => ref.current!.play());
    advance(100);
    expect(ref.current!.readFrame()).toBe(9);
    act(() => playback().clock.setSpeed(0.5));
    expect(element.playbackRate).toBe(0.5);
    expect(playback().speed).toBe(0.5);
    advance(200);
    expect(ref.current!.readFrame()).toBe(10);
    view.rerender(<FramePlayer {...playerProps} speed={1.5} ref={ref} content={Composition} contentProps={{}} />);
    expect(element.playbackRate).toBe(1.5);
    expect(playback().clock.readSpeed()).toBe(1.5);
    expect(media.play).toHaveBeenCalledOnce();
  });

  it("pauses when playback or a seek leaves the FrameWindow window", () => {
    const { ref, view, element, media } = setupMedia();
    act(() => { ref.current!.seek(24); ref.current!.play(); });
    expect(element.paused).toBe(false);
    advance(100);
    expect(view.container.querySelector(tag)).toBeNull();
    expect(element.paused).toBe(true);
    const seeks = media.seek.mock.calls.length;
    fireEvent.loadedMetadata(element);
    expect(media.seek.mock.calls).toHaveLength(seeks);
    act(() => ref.current!.seek(7));
    const replacement = view.container.querySelector<HTMLMediaElement>(tag)!;
    expect(replacement).not.toBe(element);
    expect(replacement.paused).toBe(false);
    expect(replacement.currentTime).toBe(2.2);
    act(() => ref.current!.seek(4));
    expect(replacement.paused).toBe(true);
    expect(view.container.querySelector(tag)).toBe(replacement);
    expect(replacement.muted).toBe(true);
    expect(replacement.parentElement!.style.visibility).toBe("hidden");
  });

  it("pauses on FramePlayer unmount and cancels the clock", () => {
    const { ref, view, element } = setupMedia();
    const clock = ref.current!;
    act(() => clock.play());
    expect(element.paused).toBe(false);
    view.unmount();
    expect(element.paused).toBe(true);
    expect(clock.isRunning()).toBe(false);
    expect(callbacks.size).toBe(0);
  });

  it("consumes a rejected play promise without throwing or retrying each frame", async () => {
    const { ref, element, media } = setupMedia();
    let rejectPlay!: (reason: Error) => void;
    const pending = new Promise<void>((_resolve, reject) => { rejectPlay = reject; });
    media.play.mockImplementation(() => pending);
    expect(() => act(() => ref.current!.play())).not.toThrow();
    await act(async () => { rejectPlay(new DOMException("Playback blocked", "NotAllowedError")); await Promise.resolve(); });
    expect(ref.current!.isRunning()).toBe(true);
    expect(element.paused).toBe(true);
    media.seek.mockClear();
    advance(200);
    advance(200);
    fireEvent.pause(element);
    fireEvent.ended(element);
    expect(media.play).toHaveBeenCalledTimes(2);
    expect(media.seek).not.toHaveBeenCalled();
    expect(() => act(() => ref.current!.pause())).not.toThrow();
  });

  it.each(["transport", "seek"])("retries policy-blocked playback after %s", async (retry) => {
    const { ref, element, media } = setupMedia();
    media.play.mockRejectedValueOnce(new DOMException("Playback blocked", "NotAllowedError"))
      .mockRejectedValueOnce(new DOMException("Playback blocked", "NotAllowedError"));
    await act(async () => ref.current!.play());
    advance(200);
    expect(media.play).toHaveBeenCalledTimes(2);
    if (retry === "transport") {
      act(() => ref.current!.pause());
      await act(async () => ref.current!.play());
    } else {
      await act(async () => ref.current!.seek(8));
    }
    expect(element.paused).toBe(false);
    expect(media.play).toHaveBeenCalledTimes(3);
  });

  it("recovers on the next frame after pause aborts a pending play", async () => {
    const { ref, element, media } = setupMedia();
    media.playLatency = 300;
    act(() => ref.current!.play());
    act(() => ref.current!.pause());
    act(() => ref.current!.play());
    expect(media.play).toHaveBeenCalledOnce();
    await act(async () => { await Promise.resolve(); });
    advance(100);
    expect(media.play).toHaveBeenCalledTimes(2);
    await act(async () => advance(400));
    expect(element.paused).toBe(false);
    expect(element.currentTime).toBeGreaterThan(2.2);
  });

  it("retries non-policy play failures on the next frame", async () => {
    const { ref, element, media } = setupMedia();
    media.play.mockRejectedValueOnce(new DOMException("Playback interrupted", "AbortError"));
    await act(async () => ref.current!.play());
    advance(100);
    expect(media.play).toHaveBeenCalledTimes(2);
    expect(element.paused).toBe(false);
  });

  it("aborts pending playback on unmount without resuming from later events", async () => {
    const { ref, view, element, media } = setupMedia();
    media.playLatency = 300;
    act(() => ref.current!.play());
    await act(async () => view.unmount());
    fireEvent.pause(element);
    fireEvent.ended(element);
    advance(500);
    expect(element.paused).toBe(true);
    expect(media.play).toHaveBeenCalledOnce();
    expect(callbacks.size).toBe(0);
  });

  it("pauses a pending play that resolves after unmount", async () => {
    const { ref, view, element, media } = setupMedia();
    let resolvePlay!: () => void;
    const pending = new Promise<void>((resolve) => { resolvePlay = resolve; });
    media.play.mockImplementation(() => pending.then(() => { media.paused = false; }));
    act(() => ref.current!.play());
    view.unmount();
    await act(async () => { resolvePlay(); await pending; });
    expect(element.paused).toBe(true);
    expect(media.play).toHaveBeenCalledOnce();
  });

  it("handles a pending play promise resolving after playback pauses", async () => {
    const { ref, element, media } = setupMedia();
    let resolvePlay!: () => void;
    const pending = new Promise<void>((resolve) => { resolvePlay = resolve; });
    media.play.mockImplementation(() => { media.paused = false; return pending; });
    act(() => ref.current!.play());
    expect(element.paused).toBe(false);
    act(() => ref.current!.pause());
    await act(async () => { resolvePlay(); await pending; });
    expect(element.paused).toBe(true);
    expect(element.currentTime).toBe(2.2);
  });

  it("updates mute independently of volume and synchronizes replacement sources", () => {
    const { ref, view, element, media, Composition } = setupMedia(2);
    act(() => ref.current!.play());
    view.rerender(<FramePlayer {...playerProps} speed={2} ref={ref} content={Composition} contentProps={{ muted: true }} />);
    expect(element.muted).toBe(true);
    expect(element.volume).toBe(1);
    expect(media.play).toHaveBeenCalledOnce();
    media.currentTime = 99;
    media.playbackRate = 1;
    view.rerender(<FramePlayer {...playerProps} speed={2} ref={ref} content={Composition} contentProps={{ src: "other.mp4", sourceStartFrame: 40 }} />);
    expect(element.muted).toBe(false);
    expect(element.currentTime).toBe(4.2);
    expect(element.paused).toBe(false);
    expect(element.playbackRate).toBe(2);
    expect(media.play).toHaveBeenCalledTimes(2);
  });
});
