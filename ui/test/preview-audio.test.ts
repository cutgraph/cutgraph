import { describe, expect, it } from "vitest";
import type { Project } from "../src/types/project";
import { dbToGain, clipGain, speechAtFrame } from "../src/preview/audio";

function fixture(): Project {
  return {
    schemaVersion: 2, name: "mix", fps: 30, width: 1920, height: 1080, revision: 1,
    tracks: [
      { id: "speech", kind: "speech", clips: [
        { id: "dialogue-a", media: { source: "media/dialogue.mp4" }, srcInF: 0, srcOutF: 60, tlStartF: 30 },
        { id: "dialogue-b", media: { source: "media/dialogue.mp4" }, srcInF: 90, srcOutF: 120, tlStartF: 120 },
      ] },
      { id: "bed", kind: "audio", clips: [
        { id: "music", media: { source: "media/music.mp3" }, srcInF: 0, srcOutF: 180, tlStartF: 0, gainDb: -6, duckDb: -12 },
      ] },
      { id: "broll", kind: "video", clips: [
        { id: "clip", media: { source: "media/broll.mp4" }, srcInF: 0, srcOutF: 90, tlStartF: 90, gainDb: -3, duckDb: -9 },
      ] },
    ],
  };
}

describe("preview audio mix", () => {
  it("uses gainDb as the base multiplier", () => {
    expect(dbToGain(0)).toBe(1);
    expect(dbToGain(-6)).toBeCloseTo(10 ** (-6 / 20));
    expect(dbToGain(undefined)).toBe(1);
  });

  it("finds speech spans with half-open frame boundaries", () => {
    const doc = fixture();
    expect(speechAtFrame(doc, 29)).toBe(false);
    expect(speechAtFrame(doc, 30)).toBe(true);
    expect(speechAtFrame(doc, 89)).toBe(true);
    expect(speechAtFrame(doc, 90)).toBe(false);
    expect(speechAtFrame(doc, 120)).toBe(true);
  });

  it("ducks audio only for clip-local frames that overlap dialogue", () => {
    const doc = fixture();
    const music = doc.tracks[1].clips![0];
    const gain = clipGain(doc, "audio", music);
    const base = dbToGain(-6);

    expect(gain(0)).toBeCloseTo(base);       // tl 0: before dialogue
    expect(gain(30)).toBeCloseTo(base * dbToGain(-12)); // tl 30: dialogue starts
    expect(gain(90)).toBeCloseTo(base);      // tl 90: first dialogue ends
    expect(gain(120)).toBeCloseTo(base * dbToGain(-12)); // tl 120: next dialogue starts
  });

  it("applies the same overlap rule to video audio while speech stays at base gain", () => {
    const doc = fixture();
    const video = doc.tracks[2].clips![0];
    const speech = doc.tracks[0].clips![0];
    const videoGain = clipGain(doc, "video", video);
    const speechGain = clipGain(doc, "speech", speech);

    expect(videoGain(0)).toBeCloseTo(dbToGain(-3)); // video tl 90, no speech
    expect(videoGain(30)).toBeCloseTo(dbToGain(-3) * dbToGain(-9)); // video tl 120, dialogue
    expect(speechGain(0)).toBe(1); // speech never ducks itself
  });

  it("keeps a ducked clip at base gain when there is no speech track", () => {
    const doc = fixture();
    doc.tracks = doc.tracks.filter((track) => track.kind !== "speech");
    const music = doc.tracks[0].clips![0];
    expect(clipGain(doc, "audio", music)(60)).toBeCloseTo(dbToGain(-6));
  });
});
