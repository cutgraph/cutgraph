import { describe, expect, it, vi } from "vitest";
import { FrameClock, type FrameScheduler, type FrameEventName } from "../src/player/clock";

function setup(fps = 30, duration = 300) {
  let time = 0;
  let nextHandle = 0;
  const callbacks = new Map<number, FrameRequestCallback>();
  const scheduler: FrameScheduler = {
    now: () => time,
    request: (callback) => { callbacks.set(++nextHandle, callback); return nextHandle; },
    cancel: (handle) => { callbacks.delete(handle); },
  };
  const clock = new FrameClock(fps, duration, scheduler);
  return {
    clock,
    pending: () => callbacks.size,
    advance(ms: number, paint = true) {
      time += ms;
      if (!paint) return;
      const pending = [...callbacks.values()];
      callbacks.clear();
      for (const callback of pending) callback(time);
    },
  };
}

describe("frame clock", () => {
  it.each([24, 30, 60, 29.97])("advances at %s fps from elapsed time, including missed paints", (fps) => {
    const { clock, advance, pending } = setup(fps);
    clock.play();
    advance(1000);
    expect(clock.readFrame()).toBe(Math.floor(fps));
    advance(1250);
    expect(clock.readFrame()).toBe(Math.floor(fps * 2.25));
    expect(pending()).toBe(1);
  });

  it("holds while paused and preserves fractional time through resume", () => {
    const { clock, advance, pending } = setup(10);
    clock.play();
    advance(150, false);
    clock.pause();
    expect(clock.readFrame()).toBe(1);
    expect(pending()).toBe(0);
    advance(1000);
    expect(clock.readFrame()).toBe(1);
    clock.play();
    advance(50);
    expect(clock.readFrame()).toBe(2);
  });

  it("changes rate and fps without jumping or applying the new rate to past time", () => {
    const { clock, advance } = setup(10);
    clock.play();
    advance(1000);
    clock.setSpeed(2);
    advance(500);
    expect(clock.readFrame()).toBe(20);
    clock.configure(20, 300);
    advance(500);
    expect(clock.readFrame()).toBe(40);
    clock.setSpeed(0.5);
    advance(1000);
    expect(clock.readFrame()).toBe(50);
  });

  it("reports rate changes immediately, including while paused", () => {
    const { clock } = setup();
    const changed = vi.fn();
    clock.listen("rate", changed);
    expect(clock.readSpeed()).toBe(1);
    clock.setSpeed(2);
    expect(clock.readSpeed()).toBe(2);
    expect(changed).toHaveBeenLastCalledWith({ type: "rate", detail: { speed: 2 } });
    clock.setSpeed(2);
    expect(changed).toHaveBeenCalledOnce();
    expect(() => clock.setSpeed(0)).toThrow(RangeError);
    expect(clock.readSpeed()).toBe(2);
    clock.unlisten("rate", changed);
    clock.setSpeed(0.5);
    expect(changed).toHaveBeenCalledOnce();
  });

  it("seeks while playing and reanchors subsequent playback", () => {
    const { clock, advance } = setup(10);
    const seeks = vi.fn();
    const frames = vi.fn();
    clock.listen("seek", seeks);
    clock.listen("frame", frames);
    clock.play();
    advance(1000);
    clock.seek(50.9);
    expect(seeks).toHaveBeenLastCalledWith({ type: "seek", detail: { frame: 50 } });
    expect(frames).toHaveBeenLastCalledWith({ type: "frame", detail: { frame: 50 } });
    advance(100);
    expect(clock.readFrame()).toBe(51);
    clock.pause();
    clock.seek(-10);
    expect(clock.readFrame()).toBe(0);
    clock.seek(900);
    expect(clock.readFrame()).toBe(299);
  });

  it("emits both seek events even when seeking to the displayed frame", () => {
    const { clock } = setup();
    const events: string[] = [];
    clock.listen("seek", (event) => events.push(`${event.type}:${event.detail.frame}`));
    clock.listen("frame", (event) => events.push(`${event.type}:${event.detail.frame}`));
    clock.seek(0);
    expect(events).toEqual(["seek:0", "frame:0"]);
  });

  it("ends after the last frame interval with pause and end, then restarts from zero", () => {
    const { clock, advance, pending } = setup(10, 5);
    const events: FrameEventName[] = [];
    for (const name of ["play", "pause", "end", "frame"] as const) {
      clock.listen(name, (event) => events.push(event.type));
    }
    clock.play();
    clock.play();
    advance(400);
    expect(clock.readFrame()).toBe(4);
    expect(clock.isRunning()).toBe(true);
    expect(events).toEqual(["play", "frame"]);
    advance(99);
    expect(clock.isRunning()).toBe(true);
    advance(1);
    expect(clock.readFrame()).toBe(4);
    expect(clock.isRunning()).toBe(false);
    expect(pending()).toBe(0);
    advance(1000);
    clock.pause();
    expect(events).toEqual(["play", "frame", "pause", "end"]);
    clock.play();
    expect(clock.readFrame()).toBe(0);
    expect(clock.isRunning()).toBe(true);
  });

  it("seeks and resumes within the last frame without restarting or losing its fractional interval", () => {
    const { clock, advance } = setup(10, 5);
    const end = vi.fn();
    clock.listen("end", end);
    clock.seek(4);
    clock.play();
    expect(clock.readFrame()).toBe(4);
    advance(40);
    clock.setSpeed(2);
    advance(10);
    clock.pause();
    advance(1000);
    clock.play();
    expect(clock.readFrame()).toBe(4);
    advance(19);
    expect(clock.isRunning()).toBe(true);
    advance(1);
    expect(clock.isRunning()).toBe(false);
    expect(end).toHaveBeenCalledTimes(1);
  });

  it("emits end when paused past the end before the next paint", () => {
    const { clock, advance, pending } = setup(10, 5);
    const events: string[] = [];
    clock.listen("pause", () => events.push("pause"));
    clock.listen("end", () => events.push("end"));
    clock.play();
    advance(600, false);
    clock.pause();
    expect(clock.readFrame()).toBe(4);
    expect(clock.isRunning()).toBe(false);
    expect(pending()).toBe(0);
    expect(events).toEqual(["pause", "end"]);
    clock.pause();
    advance(1000);
    expect(events).toEqual(["pause", "end"]);
  });

  it("keeps the displayed last frame when an ended duration grows", () => {
    const { clock, advance } = setup(10, 5);
    clock.play();
    advance(500);
    expect(clock.readFrame()).toBe(4);
    clock.configure(10, 10);
    expect(clock.readFrame()).toBe(4);
    clock.play();
    advance(100);
    expect(clock.readFrame()).toBe(5);
  });

  it("does not emit frame on paints within the same frame", () => {
    const { clock, advance } = setup(10);
    const frames = vi.fn();
    clock.listen("frame", frames);
    clock.play();
    advance(20);
    advance(20);
    expect(frames).not.toHaveBeenCalled();
    advance(60);
    expect(frames).toHaveBeenCalledTimes(1);
  });

  it("deduplicates listeners, removes them, and cancels disposed clocks", () => {
    const { clock, advance, pending } = setup();
    const listener = vi.fn();
    clock.listen("play", listener);
    clock.listen("play", listener);
    clock.play();
    expect(listener).toHaveBeenCalledTimes(1);
    clock.pause();
    clock.unlisten("play", listener);
    clock.play();
    expect(listener).toHaveBeenCalledTimes(1);
    clock.dispose();
    expect(pending()).toBe(0);
    advance(1000);
    expect(clock.readFrame()).toBe(0);
  });

  it("emits pause once on disposal so hosts observe stopped playback", () => {
    const { clock, pending } = setup();
    const paused = vi.fn();
    clock.listen("pause", paused);
    clock.play();
    clock.dispose();
    expect(clock.isRunning()).toBe(false);
    expect(pending()).toBe(0);
    expect(paused).toHaveBeenCalledOnce();
    clock.dispose();
    expect(paused).toHaveBeenCalledOnce();
  });

  it("clamps shortened durations and supports a single-frame composition", () => {
    const { clock, advance } = setup(30);
    clock.seek(100);
    clock.configure(30, 20);
    expect(clock.readFrame()).toBe(19);
    clock.configure(1, 1);
    const end = vi.fn();
    clock.listen("end", end);
    clock.play();
    advance(999);
    expect(clock.readFrame()).toBe(0);
    expect(clock.isRunning()).toBe(true);
    expect(end).not.toHaveBeenCalled();
    advance(1);
    expect(clock.isRunning()).toBe(false);
    expect(end).toHaveBeenCalledTimes(1);
  });

  it("rejects invalid time settings without corrupting the clock", () => {
    expect(() => setup(0)).toThrow(RangeError);
    expect(() => setup(30, 0)).toThrow(RangeError);
    expect(() => setup(30, 2.5)).toThrow(RangeError);
    const { clock } = setup();
    expect(() => clock.seek(NaN)).toThrow(RangeError);
    expect(() => clock.setSpeed(-1)).toThrow(RangeError);
    expect(() => clock.configure(Infinity, 300)).toThrow(RangeError);
    expect(clock.readFrame()).toBe(0);
  });
});
