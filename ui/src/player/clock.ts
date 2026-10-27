export type FrameEventName = "play" | "pause" | "end" | "frame" | "seek" | "rate" | "sound";

type EventDetails = {
  play: Record<string, never>;
  pause: Record<string, never>;
  end: Record<string, never>;
  frame: { frame: number };
  seek: { frame: number };
  rate: { speed: number };
  sound: { soundBlocked: boolean };
};

export type FrameListener<Name extends FrameEventName> = (event: {
  type: Name;
  detail: EventDetails[Name];
}) => void;

export interface FramePlayerHandle {
  seek(frame: number): void;
  readFrame(): number;
  play(): void;
  pause(): void;
  isRunning(): boolean;
  isSoundBlocked(): boolean;
  /** Call from a user gesture to retry playback with sound. */
  enableSound(): void;
  listen<Name extends FrameEventName>(name: Name, listener: FrameListener<Name>): void;
  unlisten<Name extends FrameEventName>(name: Name, listener: FrameListener<Name>): void;
}

export interface FrameScheduler {
  now(): number;
  request(callback: FrameRequestCallback): number;
  cancel(handle: number): void;
}

const browserScheduler: FrameScheduler = {
  now: () => performance.now(),
  request: (callback) => requestAnimationFrame(callback),
  cancel: (handle) => cancelAnimationFrame(handle),
};

function positive(value: number, label: string): void {
  if (!Number.isFinite(value) || value <= 0) throw new RangeError(`${label} must be positive and finite`);
}

export class FrameClock implements Omit<FramePlayerHandle, "enableSound"> {
  private frame = 0;
  private position = 0;
  private anchorTime = 0;
  private playing = false;
  private soundBlocked = false;
  private blockers = new Set<object>();
  private bufferingListeners = new Set<() => void>();
  private handle: number | null = null;
  private rate = 1;
  private seekVersion = 0;
  private listeners = {
    play: new Set<FrameListener<"play">>(),
    pause: new Set<FrameListener<"pause">>(),
    end: new Set<FrameListener<"end">>(),
    frame: new Set<FrameListener<"frame">>(),
    seek: new Set<FrameListener<"seek">>(),
    rate: new Set<FrameListener<"rate">>(),
    sound: new Set<FrameListener<"sound">>(),
  };

  constructor(private fps: number, private duration: number, private scheduler = browserScheduler) {
    this.validate(fps, duration);
  }

  private validate(fps: number, duration: number): void {
    positive(fps, "fps");
    positive(duration, "frameCount");
    if (!Number.isInteger(duration)) throw new RangeError("frameCount must be an integer");
  }

  readFrame = (): number => this.frame;
  readPosition = (): number => this.advancing() ? Math.min(this.duration, this.elapsedPosition()) : this.position;
  readSeekVersion = (): number => this.seekVersion;
  readSpeed = (): number => this.rate;
  isRunning = (): boolean => this.playing;
  isSoundBlocked = (): boolean => this.soundBlocked;
  setSoundBlocked(blocked: boolean): void {
    if (this.soundBlocked === blocked) return;
    this.soundBlocked = blocked;
    this.emit("sound", { soundBlocked: blocked });
  }
  isBuffering = (): boolean => this.blockers.size > 0;
  subscribeBuffering = (listener: () => void): (() => void) => {
    this.bufferingListeners.add(listener);
    return () => { this.bufferingListeners.delete(listener); };
  };

  private advancing(): boolean {
    return this.playing && !this.isBuffering();
  }

  setBlocked(owner: object, blocked: boolean): void {
    const wasBuffering = this.isBuffering();
    if (this.advancing()) this.position = Math.min(this.duration, this.elapsedPosition());
    if (blocked) this.blockers.add(owner);
    else this.blockers.delete(owner);
    this.anchorTime = this.scheduler.now();
    if (this.isBuffering()) this.cancel();
    else this.schedule();
    if (wasBuffering !== this.isBuffering()) {
      this.setFrame(Math.min(this.duration - 1, Math.floor(this.position)));
      for (const notify of [...this.bufferingListeners]) notify();
    }
  }

  listen<Name extends FrameEventName>(name: Name, listener: FrameListener<Name>): void {
    (this.listeners[name] as Set<FrameListener<Name>>).add(listener);
  }

  unlisten<Name extends FrameEventName>(name: Name, listener: FrameListener<Name>): void {
    (this.listeners[name] as Set<FrameListener<Name>>).delete(listener);
  }

  private emit<Name extends FrameEventName>(name: Name, detail: EventDetails[Name]): void {
    const listeners = this.listeners[name] as Set<FrameListener<Name>>;
    for (const listener of [...listeners]) listener({ type: name, detail });
  }

  private elapsedPosition(): number {
    return this.position + Math.max(0, this.scheduler.now() - this.anchorTime) * this.fps * this.rate / 1000;
  }

  private setFrame(frame: number): void {
    if (this.frame === frame) return;
    this.frame = frame;
    this.emit("frame", { frame });
  }

  private sample(): void {
    const position = this.elapsedPosition();
    this.setFrame(Math.min(this.duration - 1, Math.floor(position)));
    if (this.playing && position >= this.duration) this.pause();
  }

  private schedule(): void {
    if (!this.advancing() || this.handle !== null) return;
    this.handle = this.scheduler.request(() => {
      this.handle = null;
      if (!this.advancing()) return;
      this.sample();
      this.schedule();
    });
  }

  play(): void {
    if (this.playing) return;
    if (this.position >= this.duration) this.seek(0);
    this.anchorTime = this.scheduler.now();
    this.playing = true;
    this.emit("play", {});
    this.schedule();
  }

  pause(): void {
    if (!this.playing) return;
    if (this.advancing()) this.position = Math.min(this.duration, this.elapsedPosition());
    const end = this.position >= this.duration;
    this.playing = false;
    this.cancel();
    this.setFrame(Math.min(this.duration - 1, Math.floor(this.position)));
    this.emit("pause", {});
    if (end) this.emit("end", {});
  }

  seek(frame: number): void {
    if (!Number.isFinite(frame)) throw new RangeError("frame must be finite");
    this.position = Math.max(0, Math.min(this.duration - 1, Math.floor(frame)));
    this.anchorTime = this.scheduler.now();
    this.frame = this.position;
    this.seekVersion += 1;
    this.emit("seek", { frame: this.frame });
    this.emit("frame", { frame: this.frame });
  }

  setSpeed(rate: number): void {
    positive(rate, "speed");
    if (this.rate === rate) return;
    if (this.advancing()) this.position = Math.min(this.duration, this.elapsedPosition());
    this.anchorTime = this.scheduler.now();
    this.rate = rate;
    this.emit("rate", { speed: rate });
  }

  configure(fps: number, duration: number): void {
    this.validate(fps, duration);
    if (this.fps === fps && this.duration === duration) return;
    if (!this.playing && this.position >= this.duration && duration > this.duration) this.position = this.frame;
    if (this.advancing()) this.position = this.elapsedPosition();
    this.position = Math.min(duration, this.position);
    this.anchorTime = this.scheduler.now();
    this.fps = fps;
    this.duration = duration;
    this.setFrame(Math.min(duration - 1, Math.floor(this.position)));
    if (this.advancing()) this.sample();
  }

  private cancel(): void {
    if (this.handle !== null) this.scheduler.cancel(this.handle);
    this.handle = null;
  }

  dispose(): void {
    if (this.playing) this.pause();
    this.blockers.clear();
    this.cancel();
  }
}
