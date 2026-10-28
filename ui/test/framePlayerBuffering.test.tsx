// @vitest-environment jsdom
import { createRef, StrictMode } from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ClipAudio, FramePlayer, FrameWindow, ClipVideo, useFrame, usePlaybackPolicy, type FramePlayerHandle } from "../src/player";
import { FrameClock } from "../src/player/clock";

let time = 0;
let handle = 0;
let callbacks: Map<number, FrameRequestCallback>;
let elements: Map<HTMLMediaElement, FakeMedia>;
let initialDuration = 100;
let initialReadyState = 4;

class FakeMedia {
  currentTime = 0;
  duration = initialDuration;
  readyState = initialReadyState;
  error: MediaError | null = null;
  paused = true;
  seeking = false;
  ended = false;
  playbackRate = 1;
  seekLatency = 0;
  remaining = 0;
  frameCallbacks = new Map<number, VideoFrameRequestCallback>();
  frameHandle = 0;
  displayed: number[] = [];
  sourceFps: number | null = null;
  decodedTime: number | null = null;
  composedAt = 0;
  frameNeeded = true;
  decode() {
    if (!this.element.isConnected || this.sourceFps === null || this.seeking) return;
    const mediaTime = Math.floor((this.currentTime + 1e-9) * this.sourceFps) / this.sourceFps;
    if (!this.frameNeeded && mediaTime === this.decodedTime) return;
    this.frameNeeded = false;
    this.decodedTime = mediaTime;
    this.composedAt = time;
    this.deliver();
    if (getComputedStyle(this.element).visibility === "visible") this.displayed.push(mediaTime);
  }
  // Runs the callbacks for the picture already composited, as a browser does when they were queued before a seek.
  deliver() {
    const pending = [...this.frameCallbacks.values()];
    this.frameCallbacks.clear();
    const metadata = { mediaTime: this.decodedTime!, presentationTime: this.composedAt } as VideoFrameCallbackMetadata;
    for (const callback of pending) callback(time, metadata);
  }
  constructor(readonly element: HTMLMediaElement) {
    Object.defineProperty(element, "error", { configurable: true, get: () => this.error });
  }
  seek = vi.fn((target: number) => {
    this.currentTime = Math.min(this.duration, target);
    this.ended = false;
    this.seeking = true;
    this.frameNeeded = true;
    this.remaining = this.seekLatency;
    this.element.dispatchEvent(new Event("seeking"));
    if (!this.remaining) this.completeSeek();
  });
  play = vi.fn((): Promise<void> => {
    this.paused = false;
    return Promise.resolve();
  });
  pause = vi.fn(() => {
    const playing = !this.paused;
    this.paused = true;
    if (playing) this.element.dispatchEvent(new Event("pause"));
  });
  load = vi.fn();
  completeSeek() {
    this.seeking = false;
    this.element.dispatchEvent(new Event("seeked"));
  }
  advance(ms: number) {
    if (!this.element.isConnected) return;
    let elapsed = ms;
    if (this.seeking) {
      elapsed = Math.max(0, ms - this.remaining);
      this.remaining -= ms;
      if (this.remaining <= 0) this.completeSeek();
    }
    if (!this.paused && !this.seeking && this.readyState >= 3) {
      this.currentTime = Math.min(this.duration, this.currentTime + elapsed * this.playbackRate / 1000);
      if (this.currentTime >= this.duration) {
        this.ended = true;
        this.pause();
        this.element.dispatchEvent(new Event("ended"));
      }
    }
  }
}

function fake(element: HTMLMediaElement): FakeMedia {
  let state = elements.get(element);
  if (!state) { state = new FakeMedia(element); elements.set(element, state); }
  return state;
}

beforeEach(() => {
  time = 0;
  handle = 0;
  callbacks = new Map();
  elements = new Map();
  initialDuration = 100;
  initialReadyState = 4;
  for (const property of ["currentTime", "duration", "readyState", "paused", "seeking", "ended", "playbackRate"] as const) {
    vi.spyOn(HTMLMediaElement.prototype, property, "get").mockImplementation(function (this: HTMLMediaElement) { return fake(this)[property]; });
  }
  vi.spyOn(HTMLMediaElement.prototype, "currentTime", "set").mockImplementation(function (this: HTMLMediaElement, target) { fake(this).seek(target); });
  vi.spyOn(HTMLMediaElement.prototype, "playbackRate", "set").mockImplementation(function (this: HTMLMediaElement, rate) { fake(this).playbackRate = rate; });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function (this: HTMLMediaElement) { return fake(this).play(); });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(function (this: HTMLMediaElement) { fake(this).pause(); });
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(function (this: HTMLMediaElement) { fake(this).load(); });
  vi.spyOn(performance, "now").mockImplementation(() => time);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callbacks.set(++handle, callback); return handle; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => callbacks.delete(id));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(640);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(360);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function advance(ms: number, tickClock = true, decodeFrames = true) {
  await act(async () => {
    time += ms;
    for (const state of elements.values()) state.advance(ms);
    if (decodeFrames) for (const state of elements.values()) state.decode();
    if (!tickClock) return;
    const pending = [...callbacks.values()];
    callbacks.clear();
    for (const callback of pending) callback(time);
  });
}

function SoundButton() {
  const { soundRequired, enableSound } = usePlaybackPolicy();
  return soundRequired ? <button onClick={enableSound}>Enable sound</button> : null;
}

const props = { fps: 10, frameCount: 100, width: 640, height: 360 };

describe.each([{ tag: "video", Media: ClipVideo }, { tag: "audio", Media: ClipAudio }])("buffered $tag", ({ tag, Media }) => {
  function setup(required = true, rate = 1, clickToggles = false) {
    const ref = createRef<FramePlayerHandle>();
    function Composition({ src = "clip.mp4", muted = false }: { src?: string; muted?: boolean }) {
      return <><Media src={src} required={required} muted={muted} /><SoundButton /></>;
    }
    const view = render(<FramePlayer {...props} ref={ref} content={Composition} contentProps={{}} speed={rate} clickToggles={clickToggles} />);
    const element = view.container.querySelector<HTMLMediaElement>(tag)!;
    return { ref, view, element, state: fake(element), Composition };
  }

  it("freezes fractional clock position and all media until every required element is ready", async () => {
    const ref = createRef<FramePlayerHandle>();
    function Composition() { return <><Media src="clip.mp4" required /><Media src="other.mp4" required /></>; }
    const view = render(<FramePlayer {...props} ref={ref} content={Composition} contentProps={{}} speed={2} />);
    const [first, second] = [...view.container.querySelectorAll<HTMLMediaElement>(tag)];
    await act(async () => ref.current!.play());
    await advance(125);
    expect(ref.current!.readFrame()).toBe(2);
    act(() => { fireEvent.waiting(first); fake(second).readyState = 2; fireEvent.stalled(second); });
    expect(view.getByRole("status").textContent).toBe("Waiting for media");
    expect(first.paused).toBe(true);
    expect(second.paused).toBe(true);
    expect(ref.current!.isRunning()).toBe(true);
    await advance(3000);
    expect(ref.current!.readFrame()).toBe(2);
    act(() => fireEvent.canPlay(first));
    expect(view.getByRole("status").textContent).toBe("Waiting for media");
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(2);
    act(() => { fake(second).readyState = 3; fireEvent.canPlay(second); });
    expect(view.queryByRole("status")).toBeNull();
    await advance(75);
    expect(ref.current!.readFrame()).toBe(4);
    expect(first.currentTime).toBeCloseTo(0.4);
    expect(second.currentTime).toBeCloseTo(0.4);
  });

  it("honors pause and play requests during buffering without transport event chatter", async () => {
    const { ref, element, view } = setup();
    const play = vi.fn(), pause = vi.fn();
    ref.current!.listen("play", play);
    ref.current!.listen("pause", pause);
    await act(async () => ref.current!.play());
    act(() => fireEvent.waiting(element));
    expect(view.getByRole("status").textContent).toBe("Waiting for media");
    expect(pause).not.toHaveBeenCalled();
    act(() => ref.current!.pause());
    expect(view.queryByRole("status")).toBeNull();
    act(() => fireEvent.canPlay(element));
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(0);
    expect(element.paused).toBe(true);
    expect(pause).toHaveBeenCalledOnce();
    act(() => fireEvent.waiting(element));
    await act(async () => ref.current!.play());
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(0);
    act(() => fireEvent.canPlay(element));
    await advance(100);
    expect(ref.current!.readFrame()).toBe(1);
    expect(play).toHaveBeenCalledTimes(2);
  });

  it("does not hold the timeline for optional media", async () => {
    const { ref, element, view } = setup(false);
    await act(async () => ref.current!.play());
    act(() => { fake(element).readyState = 2; fireEvent.waiting(element); });
    expect(view.queryByRole("status")).toBeNull();
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(10);
  });

  it.each([2, 3, 4])("releases a failed clip at readyState %s and preserves pause and play", async (readyState) => {
    const { ref, element, state } = setup();
    await act(async () => ref.current!.play());
    await advance(1000);
    act(() => { state.readyState = 2; fireEvent.waiting(element); });
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(10);
    act(() => {
      state.readyState = readyState;
      state.error = { code: 3, message: "Decode failed" } as MediaError;
      fireEvent.error(element);
    });
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(20);
    act(() => ref.current!.pause());
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(20);
    await act(async () => ref.current!.play());
    act(() => { fireEvent.waiting(element); fireEvent.stalled(element); });
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(30);
    expect(ref.current!.isRunning()).toBe(true);
  });

  it("holds the opening before metadata arrives", async () => {
    initialDuration = NaN;
    initialReadyState = 0;
    const { ref, element, state } = setup();
    await act(async () => ref.current!.play());
    await advance(2000);
    expect(ref.current!.readFrame()).toBe(0);
    expect(state.play).not.toHaveBeenCalled();
    act(() => { state.duration = 100; state.readyState = 1; fireEvent.loadedMetadata(element); });
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(0);
    act(() => { state.readyState = 3; fireEvent.canPlay(element); });
    await advance(100);
    expect(ref.current!.readFrame()).toBe(1);
    expect(element.currentTime).toBeCloseTo(0.1);
  });

  it("ignores stalled events while there is enough data", async () => {
    const { ref, element } = setup();
    await act(async () => ref.current!.play());
    act(() => fireEvent.stalled(element));
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(10);
    expect(element.paused).toBe(false);
  });

  it.each([false, true])("does not seek or replay at natural EOF before the clock renders the window end (required=%s)", async (required) => {
    initialDuration = 1;
    const ref = createRef<FramePlayerHandle>();
    function Composition() { return <FrameWindow startFrame={0} frameCount={10}><Media src="clip.mp4" required={required} /></FrameWindow>; }
    const view = render(<FramePlayer {...props} ref={ref} content={Composition} contentProps={{}} />);
    const element = view.container.querySelector<HTMLMediaElement>(tag)!;
    const state = fake(element);
    await act(async () => ref.current!.play());
    await advance(1000, false);
    expect(ref.current!.readFrame()).toBe(0);
    expect(state.currentTime).toBe(1);
    expect(state.ended).toBe(true);
    expect(state.paused).toBe(true);
    expect(state.seek).not.toHaveBeenCalled();
    expect(state.play).toHaveBeenCalledOnce();
    await advance(0);
    expect(view.container.querySelector(tag)).toBeNull();
    expect(state.seek).not.toHaveBeenCalled();
    expect(state.play).toHaveBeenCalledOnce();
  });

  it.each(["canplay", "playing", "timeupdate"])("releases a data-starved stall on %s progress", async (event) => {
    const { ref, element, state } = setup();
    await act(async () => ref.current!.play());
    act(() => { state.readyState = 2; fireEvent.stalled(element); });
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(0);
    act(() => { state.readyState = 3; element.dispatchEvent(new Event(event)); });
    await advance(100);
    expect(ref.current!.readFrame()).toBe(1);
    expect(element.paused).toBe(false);
  });

  it("corrects required media without adding latency while the clock waits", async () => {
    const { ref, element, state } = setup();
    state.seekLatency = 250;
    await act(async () => ref.current!.play());
    act(() => ref.current!.seek(20));
    await advance(250);
    await advance(1000);
    act(() => { element.currentTime -= 2.5; });
    await advance(250);
    await advance(100);
    const heldFrame = ref.current!.readFrame();
    expect(state.seek).toHaveBeenLastCalledWith(heldFrame / props.fps);
    await advance(250);
    expect(ref.current!.readFrame()).toBe(heldFrame);
    expect(Math.abs(element.currentTime - heldFrame / props.fps)).toBeLessThanOrEqual(0.04);
    await advance(100);
    expect(Math.abs(element.currentTime - ref.current!.readFrame() / props.fps)).toBeLessThanOrEqual(0.04);
  });

  it("holds a newly visible unready clip and resumes on canplay", async () => {
    const { ref, element, state } = setup();
    act(() => { state.readyState = 2; fireEvent.waiting(element); });
    await act(async () => ref.current!.play());
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(0);
    act(() => { state.readyState = 3; fireEvent.canPlay(element); });
    await advance(100);
    expect(ref.current!.readFrame()).toBe(1);
  });

  it("retries denied autoplay muted and offers sound through a click hook", async () => {
    const { ref, view, element, state } = setup(true, 1, true);
    state.play.mockRejectedValueOnce(new DOMException("Playback denied", "NotAllowedError"));
    await act(async () => ref.current!.play());
    expect(state.play).toHaveBeenCalledTimes(2);
    expect(element.muted).toBe(true);
    expect(element.paused).toBe(false);
    const button = view.getByRole("button", { name: "Enable sound" });
    await act(async () => fireEvent.click(button));
    expect(element.muted).toBe(false);
    expect(ref.current!.isRunning()).toBe(true);
    expect(view.queryByRole("button")).toBeNull();
    await advance(100);
    expect(element.muted).toBe(false);
  });

  it("bounds retries when muted autoplay is also denied", async () => {
    const { ref, element, state } = setup();
    state.play.mockRejectedValue(new DOMException("Playback denied", "NotAllowedError"));
    await act(async () => ref.current!.play());
    await advance(100);
    await advance(100);
    act(() => { fireEvent.pause(element); fireEvent.ended(element); });
    expect(state.play).toHaveBeenCalledTimes(2);
    expect(ref.current!.readFrame()).toBe(0);
  });

  it.each(["sound", "transport"])("retries fully blocked playback on a %s gesture", async (gesture) => {
    const { ref, view, element, state } = setup();
    state.play.mockRejectedValueOnce(new DOMException("Playback denied", "NotAllowedError"))
      .mockRejectedValueOnce(new DOMException("Playback denied", "NotAllowedError"));
    await act(async () => ref.current!.play());
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(0);
    if (gesture === "sound") await act(async () => fireEvent.click(view.getByRole("button")));
    else {
      act(() => ref.current!.pause());
      await act(async () => ref.current!.play());
    }
    expect(element.paused).toBe(false);
    expect(state.play).toHaveBeenCalledTimes(3);
    await advance(100);
    expect(ref.current!.readFrame()).toBe(1);
  });

  it("keeps explicit mute after sound is enabled and resets replacement sources", async () => {
    const { ref, view, element, state, Composition } = setup();
    state.play.mockRejectedValueOnce(new DOMException("Playback denied", "NotAllowedError"));
    await act(async () => ref.current!.play());
    view.rerender(<FramePlayer {...props} ref={ref} content={Composition} contentProps={{ muted: true }} />);
    await act(async () => fireEvent.click(view.getByRole("button")));
    expect(element.muted).toBe(true);
    view.rerender(<FramePlayer {...props} ref={ref} content={Composition} contentProps={{ src: "replacement.mp4" }} />);
    expect(element.getAttribute("src")).toBe("replacement.mp4");
    expect(state.load).toHaveBeenCalledOnce();
    expect(element.muted).toBe(false);
  });

  it("pauses a late play resolution after unmount and releases its source", async () => {
    const { ref, view, element, state } = setup();
    let resolve!: () => void;
    const pending = new Promise<void>((done) => { resolve = done; });
    state.play.mockImplementation(() => pending.then(() => { state.paused = false; }));
    act(() => ref.current!.play());
    view.unmount();
    await act(async () => { resolve(); await pending; });
    expect(element.paused).toBe(true);
    expect(element.getAttribute("src")).toBeNull();
    expect(state.load).toHaveBeenCalledOnce();
    expect(callbacks.size).toBe(0);
  });

  it("lets media advance after a slow seek before another hard correction", async () => {
    const { ref, element, state } = setup(false, 2);
    state.seekLatency = 1500;
    await act(async () => ref.current!.play());
    act(() => ref.current!.seek(20));
    await advance(1500);
    expect(state.seek).toHaveBeenCalledOnce();
    expect(element.playbackRate).toBeCloseTo(2.2);
    await advance(100);
    expect(state.seek).toHaveBeenCalledTimes(2);
    expect(state.seek.mock.calls[1][0]).toBeCloseTo(8.2);
  });
});

describe("audio defaults", () => {
  function setup() {
    const ref = createRef<FramePlayerHandle>();
    function Composition() { return <><ClipVideo src="clip.mp4" /><ClipAudio src="music.mp3" /></>; }
    const view = render(<FramePlayer {...props} ref={ref} content={Composition} contentProps={{}} />);
    return { ref, audio: view.container.querySelector("audio")!, video: view.container.querySelector("video")! };
  }

  it("keeps video and the clock running when background audio stalls", async () => {
    const { ref, audio, video } = setup();
    await act(async () => ref.current!.play());
    act(() => { fake(audio).readyState = 2; fireEvent.waiting(audio); });
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(10);
    expect(video.paused).toBe(false);
    expect(video.currentTime).toBeCloseTo(1);
    act(() => { fake(video).readyState = 2; fireEvent.waiting(video); });
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(10);
  });

  it("does not freeze at zero when even muted audio autoplay is denied", async () => {
    const { ref, audio, video } = setup();
    fake(audio).play.mockRejectedValue(new DOMException("Playback denied", "NotAllowedError"));
    await act(async () => ref.current!.play());
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(10);
    expect(video.paused).toBe(false);
    expect(ref.current!.isSoundBlocked()).toBe(true);
    expect(fake(audio).play).toHaveBeenCalledTimes(2);
  });
});

describe("external playback policy", () => {
  it("exposes policy state, change events and gesture retries to sibling controls", async () => {
    const ref = createRef<FramePlayerHandle>();
    function Composition() { return <ClipVideo src="clip.mp4" />; }
    const view = render(<>
      <button onClick={() => ref.current!.enableSound()}>Enable sound</button>
      <FramePlayer {...props} ref={ref} content={Composition} contentProps={{}} />
    </>);
    const element = view.container.querySelector("video")!;
    const changed = vi.fn();
    ref.current!.listen("sound", changed);
    expect(ref.current!.isSoundBlocked()).toBe(false);
    fake(element).play.mockRejectedValueOnce(new DOMException("Playback denied", "NotAllowedError"))
      .mockRejectedValueOnce(new DOMException("Playback denied", "NotAllowedError"));
    await act(async () => ref.current!.play());
    expect(ref.current!.isSoundBlocked()).toBe(true);
    expect(changed).toHaveBeenCalledOnce();
    expect(changed).toHaveBeenCalledWith({ type: "sound", detail: { soundBlocked: true } });
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(0);
    await act(async () => fireEvent.click(view.getByRole("button")));
    expect(ref.current!.isSoundBlocked()).toBe(false);
    expect(changed).toHaveBeenLastCalledWith({ type: "sound", detail: { soundBlocked: false } });
    expect(element.muted).toBe(false);
    await advance(100);
    expect(ref.current!.readFrame()).toBe(1);
    ref.current!.unlisten("sound", changed);
    act(() => ref.current!.enableSound());
    expect(changed).toHaveBeenCalledTimes(2);
  });

  it("returns an inert policy outside a FramePlayer", () => {
    const enabled = vi.fn();
    function Outside() {
      const policy = usePlaybackPolicy();
      return <button onClick={() => { policy.enableSound(); enabled(policy.soundRequired); }}>Enable sound</button>;
    }
    const view = render(<Outside />);
    fireEvent.click(view.getByRole("button"));
    expect(enabled).toHaveBeenCalledWith(false);
  });
});

describe("video presentation windows", () => {
  let pictures: Map<HTMLCanvasElement, number>;
  beforeEach(() => {
    pictures = new Map();
    Object.defineProperty(HTMLVideoElement.prototype, "requestVideoFrameCallback", { configurable: true,
      value(this: HTMLVideoElement, callback: VideoFrameRequestCallback) {
        const state = fake(this);
        state.frameCallbacks.set(++state.frameHandle, callback);
        return state.frameHandle;
      } });
    Object.defineProperty(HTMLVideoElement.prototype, "cancelVideoFrameCallback", { configurable: true,
      value(this: HTMLVideoElement, id: number) { fake(this).frameCallbacks.delete(id); } });
    vi.spyOn(HTMLVideoElement.prototype, "videoWidth", "get").mockReturnValue(640);
    vi.spyOn(HTMLVideoElement.prototype, "videoHeight", "get").mockReturnValue(360);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
      return { drawImage: (video: HTMLVideoElement) => pictures.set(this, fake(video).decodedTime!) } as unknown as CanvasRenderingContext2D;
    });
  });
  afterEach(() => {
    cleanup();
    Reflect.deleteProperty(HTMLVideoElement.prototype, "requestVideoFrameCallback");
    Reflect.deleteProperty(HTMLVideoElement.prototype, "cancelVideoFrameCallback");
  });

  function setup(rate = 1, sourceFps = 30, duration = 20) {
    const ref = createRef<FramePlayerHandle>();
    function Composition({ end = duration, sourceStartFrame = 41, visibility, shift = 0 }:
      { end?: number; sourceStartFrame?: number; visibility?: "visible" | "hidden"; shift?: number }) {
      return <>
        <FrameWindow startFrame={shift} frameCount={end} label="outgoing"><ClipVideo src="clip.mp4" sourceStartFrame={sourceStartFrame} style={{ visibility }} /></FrameWindow>
        <FrameWindow startFrame={shift + end} frameCount={30} label="incoming"><ClipVideo src="clip.mp4" sourceStartFrame={90} /></FrameWindow>
      </>;
    }
    const playerProps = { ...props, fps: 30, ref, content: Composition, speed: rate };
    const view = render(<FramePlayer {...playerProps} contentProps={{}} />);
    const outgoing = view.container.querySelector<HTMLVideoElement>('[data-clip-label="outgoing"] video')!;
    fake(outgoing).sourceFps = sourceFps;
    return { ref, view, outgoing, playerProps };
  }

  it.each([24, 25, 29.97])("presents the covering first frame at %s fps without skipping or reseeking", async (sourceFps) => {
    const { outgoing, ref } = setup(1, sourceFps);
    const state = fake(outgoing);
    expect(state.seek).toHaveBeenCalledOnce();
    expect(state.seek).toHaveBeenLastCalledWith(41 / 30);
    await advance(0);
    const first = Math.floor(41 / 30 * sourceFps) / sourceFps;
    expect(state.displayed).toEqual([first]);
    expect(first).toBeLessThan(41 / 30);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    await act(async () => ref.current!.play());
    for (let step = 0; step < 10; step++) await advance(5);
    expect(state.seek).toHaveBeenCalledOnce();
    expect(outgoing.paused).toBe(false);
  });

  it.each([24, 25, 29.97].flatMap((sourceFps) => [0.5, 1, 2].map((rate) => ({ sourceFps, rate }))))(
    "visually guards the end at $sourceFps fps and rate $rate without pausing transport", async ({ sourceFps, rate }) => {
      const { ref, view, outgoing } = setup(rate, sourceFps);
      act(() => ref.current!.seek(18));
      const incoming = view.container.querySelector<HTMLVideoElement>('[data-clip-label="incoming"] video')!;
      fake(incoming).sourceFps = sourceFps;
      await advance(0);
      const state = fake(outgoing);
      await act(async () => ref.current!.play());
      state.pause.mockClear();
      for (let step = 0; step < 30; step++) await advance(5 / rate, false);
      expect(ref.current!.readFrame()).toBe(18);
      expect(state.decodedTime).toBeGreaterThanOrEqual(61 / 30);
      expect(state.displayed.length).toBeGreaterThan(0);
      expect(state.displayed.every((value) => value < 61 / 30 - 0.000001)).toBe(true);
      expect(outgoing.paused).toBe(false);
      expect(state.pause).not.toHaveBeenCalled();
      const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
      expect(getComputedStyle(outgoing).visibility).toBe("hidden");
      expect(getComputedStyle(canvas).display).toBe("block");
      expect(pictures.get(canvas)).toBeLessThan(61 / 30);
      expect(pictures.get(canvas)).toBeGreaterThanOrEqual(61 / 30 - 0.25);
      const seeks = fake(incoming).seek.mock.calls.length;
      await advance(0);
      expect(view.container.querySelector('[data-clip-label="outgoing"]')).toBeNull();
      expect(view.container.querySelector('[data-clip-label="incoming"] video')).toBe(incoming);
      expect(getComputedStyle(incoming).visibility).toBe("visible");
      expect(incoming.paused).toBe(false);
      expect(fake(incoming).seek).toHaveBeenCalledTimes(seeks);
    });

  it("shows the preloaded picture on the exact boundary frame", async () => {
    const { ref, view } = setup();
    act(() => ref.current!.seek(18));
    const incoming = view.container.querySelector<HTMLVideoElement>('[data-clip-label="incoming"] video')!;
    fake(incoming).sourceFps = 24;
    await advance(0);
    expect(fake(incoming).decodedTime).toBe(3);
    expect(getComputedStyle(incoming).visibility).toBe("hidden");
    await act(async () => ref.current!.play());
    await advance(1000 * 2 / 30 + 0.001);
    expect(ref.current!.readFrame()).toBe(20);
    expect(getComputedStyle(incoming).visibility).toBe("visible");
    expect(fake(incoming).decodedTime).toBe(3);
    expect(fake(incoming).seek).toHaveBeenCalledOnce();
  });

  it("retains a safe still while buffering and restores video after seeking back", async () => {
    const { ref, outgoing } = setup(1, 25);
    act(() => ref.current!.seek(18));
    await advance(0);
    await act(async () => ref.current!.play());
    fake(outgoing).playbackRate = 2;
    for (let step = 0; step < 12; step++) await advance(5, false);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    act(() => fireEvent.waiting(outgoing));
    const frame = ref.current!.readFrame();
    const picture = pictures.get(canvas);
    await advance(2000);
    expect(ref.current!.readFrame()).toBe(frame);
    expect(getComputedStyle(outgoing).visibility).toBe("hidden");
    expect(getComputedStyle(canvas).display).toBe("block");
    expect(pictures.get(canvas)).toBe(picture);
    act(() => ref.current!.seek(5));
    // The still stays up until the seek presents its replacement, so the picture never goes blank.
    expect(canvas.style.display).toBe("block");
    await advance(0);
    expect(canvas.style.display).toBe("none");
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    expect(outgoing.paused).toBe(false);
    expect(fake(outgoing).seek).toHaveBeenLastCalledWith(46 / 30);
  });

  it("keeps a valid paused picture when its end is trimmed", async () => {
    const { ref, view, outgoing, playerProps } = setup(1, 24);
    act(() => ref.current!.seek(5));
    await advance(0);
    const state = fake(outgoing);
    const seeks = state.seek.mock.calls.length;
    const picture = state.decodedTime;
    view.rerender(<FramePlayer {...playerProps} contentProps={{ end: 12 }} />);
    expect(view.container.querySelector('[data-clip-label="outgoing"] video')).toBe(outgoing);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    expect(state.decodedTime).toBe(picture);
    expect(state.seek).toHaveBeenCalledTimes(seeks);
    expect(outgoing.paused).toBe(true);
  });

  it("keeps a paused first frame visible when the clip's end is trimmed", async () => {
    const { ref, view, outgoing, playerProps } = setup(1, 30);
    act(() => ref.current!.seek(0));
    await advance(0);
    const state = fake(outgoing);
    // Browsers report microsecond-quantized times, so the first frame can read just below the window start.
    state.currentTime = Math.floor(41 / 30 * 1e6) / 1e6;
    const seeks = state.seek.mock.calls.length;
    view.rerender(<FramePlayer {...playerProps} contentProps={{ end: 12 }} />);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    expect(state.seek).toHaveBeenCalledTimes(seeks);
  });

  it("keeps the still on screen when pausing until the seek presents its replacement", async () => {
    const { ref, outgoing } = setup(1, 25);
    act(() => ref.current!.seek(18));
    await advance(0);
    await act(async () => ref.current!.play());
    fake(outgoing).playbackRate = 2;
    for (let step = 0; step < 12; step++) await advance(5, false);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    expect(getComputedStyle(canvas).display).toBe("block");
    act(() => ref.current!.pause());
    expect(canvas.style.display).toBe("block");
    await advance(0);
    expect(canvas.style.display).toBe("none");
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
  });

  it("captures a 4K still at 1080p width and keeps the caller's geometry", async () => {
    vi.spyOn(HTMLVideoElement.prototype, "videoWidth", "get").mockReturnValue(3840);
    vi.spyOn(HTMLVideoElement.prototype, "videoHeight", "get").mockReturnValue(2160);
    const { ref, outgoing } = setup(1, 30);
    act(() => ref.current!.seek(18));
    await advance(0);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    expect([canvas.width, canvas.height]).toEqual([1920, 1080]);
    function Placed() {
      return <FrameWindow startFrame={0} frameCount={20}>
        <ClipVideo src="clip.mp4" style={{ position: "absolute", top: 80, left: 120, width: 320 }} /></FrameWindow>;
    }
    const view = render(<FramePlayer {...props} fps={30} content={Placed} contentProps={{}} />);
    const placed = view.container.querySelector("video")!.nextElementSibling as HTMLCanvasElement;
    expect([placed.style.top, placed.style.left, placed.style.width]).toEqual(["80px", "120px", "320px"]);
  });

  it("hides a picture left before a start trimmed while its seek is still pending", async () => {
    const { ref, view, outgoing, playerProps } = setup(1, 25);
    act(() => ref.current!.seek(0));
    await advance(0);
    const state = fake(outgoing);
    expect(state.decodedTime).toBeLessThan(46 / 30);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    act(() => ref.current!.seek(5));
    view.rerender(<FramePlayer {...playerProps} contentProps={{ sourceStartFrame: 46, shift: 5 }} />);
    expect(getComputedStyle(outgoing).visibility).toBe("hidden");
    await advance(0);
    expect(state.decodedTime).toBeGreaterThanOrEqual(Math.floor(46 / 30 * 25) / 25);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
  });

  it("keeps a start-trim guard through a stale callback queued before the seek", async () => {
    const { ref, view, outgoing, playerProps } = setup(1, 25);
    act(() => ref.current!.seek(0));
    await advance(0);
    const state = fake(outgoing);
    const left = state.decodedTime;
    expect(left).toBeLessThan(46 / 30);
    await advance(10);
    state.seekLatency = 250;
    act(() => ref.current!.seek(5));
    view.rerender(<FramePlayer {...playerProps} contentProps={{ sourceStartFrame: 46, shift: 5 }} />);
    expect(state.seeking).toBe(true);
    expect(outgoing.style.visibility).toBe("hidden");
    act(() => state.deliver());
    expect(state.decodedTime).toBe(left);
    expect(outgoing.style.visibility).toBe("hidden");
    await advance(250);
    expect(state.decodedTime).toBeGreaterThanOrEqual(Math.floor(46 / 30 * 25) / 25);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
  });

  it.each([24, 25, 29.97])("keeps the covering first frame at %s fps when its clip only moves on the timeline",
    async (sourceFps) => {
      const { ref, view, outgoing, playerProps } = setup(1, sourceFps);
      act(() => ref.current!.seek(0));
      await advance(0);
      const state = fake(outgoing);
      expect(state.decodedTime).toBeLessThan(41 / 30);
      const seeks = state.seek.mock.calls.length;
      view.rerender(<FramePlayer {...playerProps} contentProps={{ shift: 2 }} />);
      act(() => ref.current!.seek(2));
      await advance(200);
      expect(view.container.querySelector('[data-clip-label="outgoing"] video')).toBe(outgoing);
      expect(getComputedStyle(outgoing).visibility).toBe("visible");
      expect(state.seek).toHaveBeenCalledTimes(seeks);
    });

  it("discards a still after an explicit seek or window change and only captures near the end", async () => {
    const { ref, view, outgoing, playerProps } = setup(1, 25);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    await advance(0);
    expect(pictures.has(canvas)).toBe(false);
    act(() => ref.current!.seek(18));
    await advance(0);
    expect(pictures.has(canvas)).toBe(true);
    act(() => ref.current!.seek(18));
    await act(async () => ref.current!.play());
    await advance(150, false);
    expect(canvas.style.display).toBe("none");
    act(() => ref.current!.seek(5));
    await advance(0);
    view.rerender(<FramePlayer {...playerProps} contentProps={{ sourceStartFrame: 70 }} />);
    expect(outgoing.style.visibility).toBe("hidden");
    expect(canvas.style.display).toBe("none");
    await advance(0);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
  });

  it("plays a one-frame window without latching a pause", async () => {
    const { ref, outgoing } = setup(2, 24, 1);
    await advance(0);
    const state = fake(outgoing);
    expect(state.displayed).toEqual([Math.floor(41 / 30 * 24) / 24]);
    await act(async () => ref.current!.play());
    state.pause.mockClear();
    await advance(50, false);
    expect(state.displayed.every((value) => value < 42 / 30 - 0.000001)).toBe(true);
    expect(state.pause).not.toHaveBeenCalled();
    expect(outgoing.paused).toBe(false);
    act(() => ref.current!.seek(0));
    await advance(0);
    expect(outgoing.style.visibility).toBe("");
    expect(outgoing.paused).toBe(false);
  });

  it("retains the covering first frame as a still in a one-frame window", async () => {
    const { ref, outgoing } = setup(1, 24, 1);
    await advance(0);
    const state = fake(outgoing);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    expect(pictures.get(canvas)).toBe(32 / 24);
    expect(pictures.get(canvas)).toBeLessThan(41 / 30);
    await act(async () => ref.current!.play());
    await advance(50, false);
    expect(state.decodedTime).toBeGreaterThanOrEqual(42 / 30);
    expect(getComputedStyle(outgoing).visibility).toBe("hidden");
    expect(getComputedStyle(canvas).display).toBe("block");
    expect(getComputedStyle(canvas).visibility).toBe("visible");
    expect(pictures.get(canvas)).toBe(32 / 24);
    expect(state.seek).toHaveBeenCalledOnce();
    expect(outgoing.paused).toBe(false);
  });

  it("discards a displayed covering still when the source start changes", async () => {
    const { ref, view, outgoing, playerProps } = setup(1, 24, 1);
    const state = fake(outgoing);
    await advance(0);
    await act(async () => ref.current!.play());
    await advance(50, false);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    expect(canvas.style.display).toBe("block");
    state.seekLatency = 250;
    view.rerender(<FramePlayer {...playerProps} contentProps={{ sourceStartFrame: 40, end: 2 }} />);
    expect(state.seeking).toBe(true);
    expect(outgoing.style.visibility).toBe("hidden");
    expect(canvas.style.display).toBe("none");
    await advance(250, false);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
  });

  it.each(["static", "relative", "absolute"] as const)("overlays the rendered video box for a %s caller", async (position) => {
    const ref = createRef<FramePlayerHandle>();
    function Composition() {
      return <FrameWindow startFrame={0} frameCount={20}>
        <ClipVideo src="clip.mp4" sourceStartFrame={41} style={{ position, top: 80, left: 120, width: 320, height: 180,
          objectFit: "contain", margin: 12, transform: "scale(0.9)" }} />
      </FrameWindow>;
    }
    const view = render(<FramePlayer {...props} fps={30} ref={ref} content={Composition} contentProps={{}} />);
    const video = view.container.querySelector("video")!;
    const canvas = video.nextElementSibling as HTMLCanvasElement;
    const state = fake(video);
    state.sourceFps = 30;
    for (const [property, value] of Object.entries({ offsetLeft: 132, offsetTop: 92, offsetWidth: 320, offsetHeight: 180 })) {
      Object.defineProperty(video, property, { configurable: true, value });
    }
    expect(canvas.style.position).toBe("absolute");
    act(() => ref.current!.seek(18));
    await advance(0);
    await act(async () => ref.current!.play());
    for (let step = 0; step < 30; step++) await advance(5, false);
    expect(video.style.visibility).toBe("hidden");
    expect(canvas.style.display).toBe("block");
    expect([canvas.style.left, canvas.style.top, canvas.style.width, canvas.style.height]).toEqual(["132px", "92px", "320px", "180px"]);
    expect(canvas.style.margin).toBe("0px");
    expect(canvas.style.objectFit).toBe("contain");
    expect(canvas.style.transform).toBe("scale(0.9)");
    expect(canvas.style.pointerEvents).toBe("none");
    expect(canvas.getAttribute("aria-hidden")).toBe("true");
  });

  it("hides a captured still immediately when an end trim excludes it during a seek", async () => {
    const { ref, view, outgoing, playerProps } = setup();
    const state = fake(outgoing);
    act(() => ref.current!.seek(18));
    await advance(0);
    await act(async () => ref.current!.play());
    for (let step = 0; step < 30; step++) await advance(5, false);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    expect(pictures.get(canvas)).toBe(2);
    expect(canvas.style.display).toBe("block");
    state.seekLatency = 250;
    act(() => ref.current!.seek(5));
    view.rerender(<FramePlayer {...playerProps} contentProps={{ end: 19 }} />);
    expect(state.seeking).toBe(true);
    expect(outgoing.style.visibility).toBe("hidden");
    expect(canvas.style.display).toBe("none");
    await advance(100, false);
    expect(canvas.style.display).toBe("none");
    await advance(150, false);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    expect(state.decodedTime).toBeLessThan(2 - 0.000001);
  });

  it("retains the valid still when seeking back and trimming before the replacement frame arrives", async () => {
    const { ref, view, outgoing, playerProps } = setup(1, 25);
    const state = fake(outgoing);
    act(() => ref.current!.seek(18));
    await advance(0);
    await act(async () => ref.current!.play());
    await advance(150, false);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    const excluded = state.decodedTime;
    expect(excluded).toBeGreaterThanOrEqual(61 / 30);
    expect(pictures.get(canvas)).toBe(1.96);
    state.seekLatency = 250;
    act(() => ref.current!.seek(5));
    expect(state.currentTime).toBeCloseTo(46 / 30);
    view.rerender(<FramePlayer {...playerProps} contentProps={{ end: 19 }} />);
    expect(state.seeking).toBe(true);
    expect(state.decodedTime).toBe(excluded);
    expect(outgoing.style.visibility).toBe("hidden");
    expect(canvas.style.display).toBe("block");
    await advance(100, false);
    expect(outgoing.style.visibility).toBe("hidden");
    expect(canvas.style.display).toBe("block");
    await advance(150, false, false);
    expect(state.seeking).toBe(false);
    view.rerender(<FramePlayer {...playerProps} contentProps={{ end: 24 }} />);
    expect(excluded).toBeLessThan(65 / 30);
    expect(outgoing.style.visibility).toBe("hidden");
    expect(canvas.style.display).toBe("block");
    await advance(0, false);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    expect(canvas.style.display).toBe("none");
    expect(state.decodedTime).toBe(1.52);
  });

  it("keeps an excluded frame hidden through caller visibility updates", async () => {
    const { ref, view, outgoing, playerProps } = setup();
    const state = fake(outgoing);
    act(() => ref.current!.seek(18));
    await advance(0);
    await act(async () => ref.current!.play());
    for (let step = 0; step < 30; step++) await advance(5, false);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    expect(state.decodedTime).toBeGreaterThanOrEqual(61 / 30);
    view.rerender(<FramePlayer {...playerProps} contentProps={{ visibility: "visible" }} />);
    expect(getComputedStyle(outgoing).visibility).toBe("hidden");
    expect(canvas.style.display).toBe("block");
    view.rerender(<FramePlayer {...playerProps} contentProps={{ visibility: "hidden" }} />);
    expect(getComputedStyle(canvas).visibility).toBe("hidden");
    view.rerender(<FramePlayer {...playerProps} contentProps={{ visibility: "visible" }} />);
    expect(getComputedStyle(outgoing).visibility).toBe("hidden");
    act(() => ref.current!.seek(5));
    await advance(0);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    expect(canvas.style.display).toBe("none");
    view.rerender(<FramePlayer {...playerProps} contentProps={{ visibility: "hidden" }} />);
    expect(getComputedStyle(outgoing).visibility).toBe("hidden");
  });

  it("caps a portrait still by its longest side", async () => {
    vi.spyOn(HTMLVideoElement.prototype, "videoWidth", "get").mockReturnValue(2160);
    vi.spyOn(HTMLVideoElement.prototype, "videoHeight", "get").mockReturnValue(3840);
    const { ref, outgoing } = setup();
    act(() => ref.current!.seek(18));
    await advance(0);
    const canvas = outgoing.nextElementSibling as HTMLCanvasElement;
    expect([canvas.width, canvas.height]).toEqual([1080, 1920]);
    expect(pictures.has(canvas)).toBe(true);
  });

  it.each([24, 25, 29.97])("preserves the paused covering first frame through an end trim at %s fps", async (sourceFps) => {
    const { view, outgoing, playerProps } = setup(1, sourceFps);
    const state = fake(outgoing);
    await advance(0);
    const first = state.decodedTime;
    expect(first).toBeLessThan(41 / 30);
    view.rerender(<FramePlayer {...playerProps} contentProps={{ end: 12 }} />);
    expect(getComputedStyle(outgoing).visibility).toBe("visible");
    expect(state.decodedTime).toBe(first);
    expect(state.seek).toHaveBeenCalledOnce();
  });

  it("guards a nested window at its parent's end", async () => {
    const ref = createRef<FramePlayerHandle>();
    function Composition() {
      return <FrameWindow startFrame={0} frameCount={20}>
        <FrameWindow startFrame={10} frameCount={30}><ClipVideo src="clip.mp4" sourceStartFrame={30} /></FrameWindow>
      </FrameWindow>;
    }
    const view = render(<FramePlayer {...props} ref={ref} content={Composition} contentProps={{}} />);
    act(() => ref.current!.seek(18));
    const video = view.container.querySelector("video")!;
    const state = fake(video);
    state.sourceFps = 25;
    await advance(0);
    await act(async () => ref.current!.play());
    state.pause.mockClear();
    for (let step = 0; step < 50; step++) await advance(5, false);
    expect(state.decodedTime).toBeGreaterThanOrEqual(4);
    expect(state.displayed.every((value) => value < 4 - 0.000001)).toBe(true);
    expect(video.style.visibility).toBe("hidden");
    expect(state.pause).not.toHaveBeenCalled();
  });
});
describe("preload windows", () => {
  function Frame() { return <span>{useFrame()}</span>; }
  function Composition() {
    return <>
      <FrameWindow startFrame={0} frameCount={20} label="first"><ClipVideo src="first.mp4" /><Frame /></FrameWindow>
      <FrameWindow startFrame={20} frameCount={20} label="next"><ClipVideo src="next.mp4" sourceStartFrame={50} /><Frame /></FrameWindow>
      <FrameWindow startFrame={40} frameCount={20} label="last"><ClipVideo src="last.mp4" /></FrameWindow>
    </>;
  }
  function setup() {
    const ref = createRef<FramePlayerHandle>();
    const view = render(<FramePlayer {...props} ref={ref} content={Composition} contentProps={{}} />);
    const find = (name: string) => view.container.querySelector<HTMLVideoElement>(`[data-clip-label="${name}"] video`);
    return { ref, view, find };
  }

  it("preloads only the approaching window, seeks silently, and retains the element at the cut", async () => {
    const { ref, find } = setup();
    const first = find("first")!;
    expect(find("next")).toBeNull();
    act(() => ref.current!.seek(10));
    const next = find("next")!;
    expect(next.preload).toBe("auto");
    expect(next.muted).toBe(true);
    expect(next.currentTime).toBe(5);
    expect(next.paused).toBe(true);
    expect(next.parentElement!.style.visibility).toBe("hidden");
    expect(next.parentElement!.textContent).toBe("0");
    expect(find("last")).toBeNull();
    const seeks = fake(next).seek.mock.calls.length;
    await act(async () => ref.current!.play());
    await advance(1000);
    expect(find("next")).toBe(next);
    expect(next.parentElement!.style.visibility).toBe("visible");
    expect(next.muted).toBe(false);
    expect(next.paused).toBe(false);
    expect(fake(next).seek).toHaveBeenCalledTimes(seeks);
    expect(first.getAttribute("src")).toBeNull();
    expect(first.paused).toBe(true);
    expect(fake(first).load).toHaveBeenCalledOnce();
  });

  it("ignores a hidden stall, then freezes at its cut until it becomes ready", async () => {
    const { ref, find } = setup();
    act(() => ref.current!.seek(10));
    const next = find("next")!;
    act(() => { fake(next).readyState = 2; fireEvent.waiting(next); });
    await act(async () => ref.current!.play());
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(20);
    await advance(1000);
    expect(ref.current!.readFrame()).toBe(20);
    act(() => { fake(next).readyState = 4; fireEvent.canPlay(next); });
    await advance(100);
    expect(ref.current!.readFrame()).toBe(21);
  });

  it("releases blockers on a seek out of the window and ignores detached events", async () => {
    const { ref, find } = setup();
    const first = find("first")!;
    await act(async () => ref.current!.play());
    act(() => fireEvent.waiting(first));
    act(() => ref.current!.seek(40));
    expect(find("first")).toBeNull();
    act(() => fireEvent.waiting(first));
    await advance(100);
    expect(ref.current!.readFrame()).toBe(41);
  });

  it("adds nested offsets and keeps children hidden while their ancestor preloads", () => {
    const ref = createRef<FramePlayerHandle>();
    function Nested() {
      return <FrameWindow startFrame={10} frameCount={20} preloadFrames={20}>
        <FrameWindow startFrame={0} frameCount={10}><ClipVideo src="clip.mp4" sourceStartFrame={30} /><Frame /></FrameWindow>
      </FrameWindow>;
    }
    const view = render(<FramePlayer {...props} ref={ref} content={Nested} contentProps={{}} />);
    const video = view.container.querySelector("video")!;
    expect(video.muted).toBe(true);
    expect(video.currentTime).toBe(3);
    act(() => ref.current!.seek(12));
    expect(view.container.querySelector("video")).toBe(video);
    expect(video.currentTime).toBe(3.2);
    expect(view.container.querySelector("span")!.textContent).toBe("2");
  });

  it("survives strict effects with sources restored and no scheduler leak", async () => {
    const ref = createRef<FramePlayerHandle>();
    const view = render(<StrictMode><FramePlayer {...props} ref={ref} content={Composition} contentProps={{}} /></StrictMode>);
    expect(view.container.querySelector("video")!.getAttribute("src")).toBe("first.mp4");
    await act(async () => ref.current!.play());
    await advance(100);
    expect(ref.current!.readFrame()).toBe(1);
    view.unmount();
    expect(callbacks.size).toBe(0);
  });
});

it("ends once and rewinds a buffered clock on replay", () => {
  const clock = new FrameClock(10, 10, { now: () => time, request: (cb) => { callbacks.set(++handle, cb); return handle; }, cancel: (id) => { callbacks.delete(id); } });
  const pause = vi.fn(), ended = vi.fn();
  clock.listen("pause", pause);
  clock.listen("end", ended);
  clock.play();
  time = 1000;
  for (const cb of [...callbacks.values()]) cb(time);
  expect(clock.isRunning()).toBe(false);
  expect(pause).toHaveBeenCalledOnce();
  expect(ended).toHaveBeenCalledOnce();
  const owner = {};
  clock.setBlocked(owner, true);
  clock.play();
  expect(clock.readFrame()).toBe(0);
  expect(clock.isRunning()).toBe(true);
  clock.dispose();
});
